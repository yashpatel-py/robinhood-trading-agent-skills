#!/usr/bin/env python3
"""reconcile.py - line up Robinhood's order history with this kit's local audit log.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: Robinhood tags each equity and options order with who placed it (`placed_agent`:
agentic = through the MCP connector, user, recurring, drip, and possibly other values). That answers
"was it an agent?" but not
"was it MY agent?": every MCP client on every machine shows up as `agentic`. This kit's hook logs
every place call it sees, with the order id Robinhood returned. Matching the two sides answers the
questions that matter after an agent goes live:
  * matched            - orders this kit's session sent (with or without a prior same-session review)
  * broker_only        - agent orders with no local audit entry: another agent, app or machine
  * audit_only         - place calls in the log with no broker order (failed, rejected, wrong window)
  * source_unknown     - crypto orders and OCOs whose legs carry no source (no placed_agent filter), and
                         any equity/option row passed without placed_agent (a mapping gap: UNKNOWN)
  * user_orders_in_agentic - orders in the Agentic account that Robinhood marks as not placed by an agent:
                         any placed_agent other than agentic (user, recurring, drip, any other value, or
                         "not_agentic" for a row from the all-sources read that carried no value)
  * agent_orders_outside_agentic - agent orders in a read-only account (should never exist)
It also records which read-only accounts were verified to hold no agent orders, and computes the
report's status line: ACTION NEEDED > UNKNOWN (an incomplete read) > CLEAR. Unknown is never clear.

Before matching: rows with the same (account, asset, order_id) are one order (the same order comes back
from the agentic-filtered read and the all-sources read; the agentic copy wins), and each OCO's legs
are folded into the OCO row (get_advanced_orders hydrates them and get_equity_orders lists them too), so
an OCO counts once: by `leg_order_ids`, else by same account, symbol, side and quantity, created within
120 s of the OCO, one stop leg and one limit leg, each price equal to the OCO's when the OCO carries it.
An OCO with no source of its own takes it from its legs (agentic if any leg is agentic).

Matching, in order: (1) the order id the place call returned; (2) ref_id, if the broker row carries
one (the 2026-09-22 capture did not show it, so this rarely applies); (3) same asset, symbol, side
and quantity with created_at within 120 s of the place call. Each order and each place call is used
at most once.

Input  (op `run`):
  {"window": {"start","end"} | {"start_date","end_date"}, "agentic_account_last4": "X4F1",
   "accounts"?: [{"last4","agentic": bool}],
   "broker_orders": [{"account_last4","asset":"equity"|"option"|"crypto"|"oco","order_id","ref_id"?,
                      "symbol"?,"side"?,"quantity"?,"state"?,"created_at","placed_agent"?,"type"?,
                      "price"?,"stop_price"?,"dollar_based_amount"?,"average_price"?,
                      "cumulative_quantity"?,"last_transaction_at"?,"multiplier"?,
                      "leg_order_ids"? (oco rows: the ids of its hydrated legs)}],
   "reads": [{"account_last4","tool","filter"? (the placed_agent value sent; "all" for an order read
              with no placed_agent), "status":"complete"|"partial"|"failed"|"not_enabled"|"not_in_scope"}],
   "audit"?: <audit_verify.py output>,  and/or  "audit_events"?: [place rows or raw audit lines]}
Output: {"ok":true,"window","audit_coverage","counts",{...},"matched":[...],"broker_only":[...],
         "unattributed":[...],"audit_only":[...],"placed_without_review":[...],"source_unknown":[...],
         "user_orders_in_agentic":[...],"agent_orders_outside_agentic":[...],
         "read_only_verification":[...],"incomplete_reads":[...],"flags":[...],"status",
         "at_stake":[...],"status_line","match_method","notes":[...]}

Usage:
    python3 reconcile.py run < input.json > output.json
    python3 reconcile.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0 whenever
JSON was printed, exit 1 only on a crash. Money is Decimal, rounded half-up to cents, as strings.
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

VERSION = "2.0.0"
SCRIPT = "reconcile"

MATCH_WINDOW_S = 120
ASSETS = ("equity", "option", "crypto", "oco")
READ_STATUSES = ("complete", "partial", "failed", "not_enabled", "not_in_scope")
FAMILY_BY_TOOL = {"place_equity_order": "equity", "place_option_order": "option", "place_crypto_order": "crypto",
                  "place_advanced_order": "oco"}
MONEY_RE = re.compile(r"^(place|exercise|replace)_")
BROKER_ONLY_REASON = "no local audit entry: placed by another agent, app or machine"
UNATTRIBUTED_NO_LOG = "no local audit log on this surface: cannot tell which agent placed it"
UNATTRIBUTED_BEFORE_LOG = "created before the local audit log starts: cannot tell which agent placed it"
AUDIT_ONLY_REASON = "place call with no broker order: blocked, failed or rejected"
SOURCE_UNKNOWN_REASON = "source not distinguishable: this order list has no placed_agent filter"
UNTAGGED_REASON = ("no placed_agent passed for this equity/option order: pass agentic if it was in the "
                   "agentic-filtered read, else its own value (or not_agentic)")
PWR_REASON = "no same-session review with the same order fingerprint before this place call"
MATCH_METHOD = ("order id returned by the place call; else ref_id if the broker order carries it (VERIFY-D1: "
                "not seen in the 2026-09-22 capture); else asset+symbol+side+quantity within 120 s")
# (tool, filter): a filter must match exactly ("all" = the read sent no placed_agent, so it returns every
# source: user, recurring, drip and any other value); None = any filter (crypto and advanced have none).
# A placed_agent "user" read is optional: the all-sources read already covers it.
AGENTIC_REQUIRED_READS = (("get_equity_orders", "agentic"), ("get_equity_orders", "all"),
                          ("get_option_orders", "agentic"), ("get_option_orders", "all"),
                          ("get_crypto_orders", None), ("get_advanced_orders", None))
SOURCED_ASSETS = ("equity", "option")
READ_ONLY_REQUIRED = ("get_equity_orders", "get_option_orders")
CENT = Decimal("0.01")
OPTION_MULTIPLIER = Decimal("100")
MASK = "\u2022" * 4


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


# ---------------------------------------------------------------------------------------------
# Time and number helpers (US Eastern via the 2007+ DST rule; no tzdata needed)
# ---------------------------------------------------------------------------------------------
_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?\s*(Z|z|[+-]\d{2}:?\d{2})?$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DEC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")


def parse_ts(value, naive_is_utc=True):
    if not isinstance(value, str):
        return None
    m = _TS_RE.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, off = m.groups()
    try:
        micro = int((frac or "0")[:6].ljust(6, "0"))
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), micro)
    except ValueError:
        return None
    if off is None:
        return dt.replace(tzinfo=timezone.utc) if naive_is_utc else None
    if off in ("Z", "z"):
        return dt.replace(tzinfo=timezone.utc)
    sign = 1 if off[0] == "+" else -1
    digits = off[1:].replace(":", "")
    return (dt - sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))).replace(tzinfo=timezone.utc)


def _nth_sunday(year, month, n):
    first = datetime(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def et_offset(dt_utc):
    start = _nth_sunday(dt_utc.year, 3, 2).replace(hour=7, tzinfo=timezone.utc)
    end = _nth_sunday(dt_utc.year, 11, 1).replace(hour=6, tzinfo=timezone.utc)
    return timedelta(hours=-4) if start <= dt_utc < end else timedelta(hours=-5)


def fmt_et(dt):
    return None if dt is None else (dt + et_offset(dt)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M ET")


def fmt_et_date(dt):
    return (dt + et_offset(dt)).replace(tzinfo=None).strftime("%Y-%m-%d")


def fmt_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(text, field):
    m = _DATE_RE.match(str(text or "").strip())
    if not m:
        raise InputError("BAD_WINDOW", field, "expected an ET date YYYY-MM-DD, got %r" % (text,))
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise InputError("BAD_WINDOW", field, "not a calendar date: %r" % (text,))


def et_midnight_utc(local):
    guess = (local + timedelta(hours=5)).replace(tzinfo=timezone.utc)
    return (local - et_offset(guess)).replace(tzinfo=timezone.utc)


def parse_window(window):
    if not isinstance(window, dict):
        raise InputError("BAD_WINDOW", "window", "give window {start, end} (UTC instants) or {start_date, end_date}")
    if "start" in window or "end" in window:
        start = parse_ts(window.get("start"), naive_is_utc=False)
        end = parse_ts(window.get("end"), naive_is_utc=False)
        if start is None or end is None:
            raise InputError("BAD_WINDOW", "window", "window.start and window.end must be ISO 8601 times with Z "
                             "or an offset (convert ET dates with rh_time.py to_utc, or pass start_date/end_date)")
    elif "start_date" in window and "end_date" in window:
        start = et_midnight_utc(parse_date(window.get("start_date"), "window.start_date"))
        end = et_midnight_utc(parse_date(window.get("end_date"), "window.end_date") + timedelta(days=1))
    else:
        raise InputError("BAD_WINDOW", "window", "give window.start and window.end, or window.start_date and "
                         "window.end_date")
    if not start < end:
        raise InputError("BAD_WINDOW", "window", "window start must be before its end")
    return start, end


def window_out(start, end):
    last_day = end - timedelta(seconds=1)
    return {"start": fmt_utc(start), "end": fmt_utc(end),
            "label_et": "%s → %s ET" % (fmt_et_date(start), fmt_et_date(last_day))}


def dec(value, field=None):
    """Decimal from a decimal string or int; None for missing/empty. Raises InputError when malformed."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise InputError("BAD_NUMBER", field or "", "expected a decimal string, got a boolean")
    if isinstance(value, float):
        value = repr(value)
    text = str(value).strip()
    if not _DEC_RE.match(text):
        raise InputError("BAD_NUMBER", field or "", "expected a decimal string, got %r" % (value,))
    try:
        return Decimal(text)
    except InvalidOperation:
        raise InputError("BAD_NUMBER", field or "", "expected a decimal string, got %r" % (value,))


def money(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def usd(d):
    """Display dollars: $1,234.50 (the JSON fields stay plain decimal strings)."""
    q = d.quantize(CENT, rounding=ROUND_HALF_UP)
    return ("-$" if q < 0 else "$") + "{:,.2f}".format(abs(q))


def last4(value):
    s = str(value or "").strip()
    return s[-4:] if s else s


def masked(l4):
    return MASK + l4


def _bare(tool):
    return tool.rsplit("__", 1)[-1] if isinstance(tool, str) and tool else None


def _s(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def plural(n, word, suffix="s"):
    return "%d %s%s" % (n, word, "" if n == 1 else suffix)


# ---------------------------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------------------------
def norm_order(row, i):
    field = "broker_orders[%d]" % i
    if not isinstance(row, dict):
        raise InputError("BAD_ORDER", field, "each broker order must be an object")
    asset = str(row.get("asset") or "").strip().lower()
    if asset not in ASSETS:
        raise InputError("BAD_ORDER", field + ".asset", "asset must be one of %s" % ", ".join(ASSETS))
    order_id = _s(row.get("order_id"))
    if not order_id:
        raise InputError("BAD_ORDER", field + ".order_id", "order_id is required (the order's id from the response)")
    created = parse_ts(row.get("created_at"))
    if created is None:
        raise InputError("BAD_ORDER", field + ".created_at", "created_at must be an ISO 8601 time")
    acct = last4(row.get("account_last4"))
    if len(acct) != 4:
        raise InputError("BAD_ORDER", field + ".account_last4", "account_last4 must be the last 4 characters")
    placed_agent = row.get("placed_agent")
    placed_agent = str(placed_agent).strip().lower() if placed_agent not in (None, "") else None
    legs = row.get("leg_order_ids")
    if legs is None:
        legs = []
    if not isinstance(legs, list) or not all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in legs):
        raise InputError("BAD_ORDER", field + ".leg_order_ids", "leg_order_ids must be a list of order ids")
    o = {
        "account_last4": acct, "asset": asset, "order_id": order_id, "ref_id": _s(row.get("ref_id")),
        "symbol": (_s(row.get("symbol")) or "").strip().upper() or None,
        "side": (_s(row.get("side")) or "").strip().lower() or None,
        "quantity": dec(row.get("quantity"), field + ".quantity"),
        "state": (_s(row.get("state")) or "").strip().lower() or None,
        "created_at": _s(row.get("created_at")), "created_dt": created, "placed_agent": placed_agent,
        "type": (_s(row.get("type")) or "").strip().lower() or None,
        "price": dec(row.get("price"), field + ".price"),
        "stop_price": dec(row.get("stop_price"), field + ".stop_price"),
        "dollar_based_amount": dec(row.get("dollar_based_amount"), field + ".dollar_based_amount"),
        "average_price": dec(row.get("average_price"), field + ".average_price"),
        "cumulative_quantity": dec(row.get("cumulative_quantity"), field + ".cumulative_quantity"),
        "multiplier": dec(row.get("multiplier"), field + ".multiplier"),
        "fill_dt": parse_ts(row.get("last_transaction_at")),
        "leg_order_ids": [str(x) for x in legs if str(x)] if asset == "oco" else [],
        "legs_folded": [],
    }
    o["notional"] = notional(o)
    return o


def notional(o):
    """Filled value when anything filled; else the order's intended size; None when unknowable."""
    mult = o["multiplier"] if o["multiplier"] is not None else (OPTION_MULTIPLIER if o["asset"] == "option" else Decimal(1))
    cum, avg = o["cumulative_quantity"], o["average_price"]
    if cum is not None and cum > 0 and avg is not None:
        return cum * avg * mult
    if o["dollar_based_amount"] is not None:
        return o["dollar_based_amount"]
    qty = o["quantity"]
    if o["asset"] == "oco" and qty is not None:
        # one OCO, one amount: quantity x the higher of its two leg prices, never the two legs added up
        prices = [x for x in (o["price"], o["stop_price"]) if x is not None]
        return qty * max(prices) * mult if prices else None
    if qty is not None and o["price"] is not None:
        return qty * o["price"] * mult
    if qty is not None and o["stop_price"] is not None:
        return qty * o["stop_price"] * mult
    return None


def dedupe_orders(orders):
    """One row per (account, asset, order_id). The same order comes back from the agentic-filtered read and
    the all-sources read; keep the copy tagged agentic, else one that carries a source, else the first."""
    def rank(o):
        return 0 if o["placed_agent"] == "agentic" else (1 if o["placed_agent"] else 2)
    kept, index = [], {}
    for o in orders:
        key = (o["account_last4"], o["asset"], o["order_id"])
        if key not in index:
            index[key] = len(kept)
            kept.append(o)
        elif rank(o) < rank(kept[index[key]]):
            kept[index[key]] = o
    return kept


def fold_oco_legs(orders):
    """Fold each OCO's legs (equity rows) into the OCO row, so one OCO is one order. Returns (orders, folded)."""
    ocos = [o for o in orders if o["asset"] == "oco"]
    if not ocos:
        return orders, 0
    owner = {}
    for oco in ocos:
        for lid in oco["leg_order_ids"]:
            owner.setdefault((oco["account_last4"], lid), oco)
    legs_by_oco = {id(oco): [] for oco in ocos}
    rest = []
    for o in orders:
        parent = owner.get((o["account_last4"], o["order_id"])) if o["asset"] == "equity" else None
        if parent is not None:
            legs_by_oco[id(parent)].append(o)
        else:
            rest.append(o)
    # no leg ids: same account, symbol, side and quantity, created within 120 s of the OCO; one stop leg and one
    # limit leg, each price equal to the OCO's when the OCO row carries it
    taken = set()
    for oco in sorted(ocos, key=lambda x: (x["created_dt"], x["order_id"])):
        if oco["leg_order_ids"] or not oco["symbol"] or oco["quantity"] is None:
            continue
        want, used = {"stop": oco["stop_price"], "limit": oco["price"]}, set()
        for o in sorted(rest, key=lambda x: (x["created_dt"], x["order_id"])):
            if id(o) in taken or o["asset"] != "equity" or o["account_last4"] != oco["account_last4"]:
                continue
            if o["symbol"] != oco["symbol"] or o["quantity"] != oco["quantity"]:
                continue
            if o["side"] and oco["side"] and o["side"] != oco["side"]:
                continue
            if abs((o["created_dt"] - oco["created_dt"]).total_seconds()) > MATCH_WINDOW_S:
                continue
            # a price the OCO row carries must match; one it doesn't carry can't rule a leg out
            slot = None
            if "stop" not in used and o["stop_price"] is not None and want["stop"] in (None, o["stop_price"]):
                slot = "stop"
            elif ("limit" not in used and o["stop_price"] is None and o["price"] is not None
                  and want["limit"] in (None, o["price"])):
                slot = "limit"
            if slot is None:
                continue
            used.add(slot)
            taken.add(id(o))
            legs_by_oco[id(oco)].append(o)
    rest = [o for o in rest if id(o) not in taken]
    folded = 0
    for oco in ocos:
        legs = legs_by_oco[id(oco)]
        if not legs:
            continue
        folded += len(legs)
        merge_oco(oco, legs)
    return rest, folded


def merge_oco(oco, legs):
    """The OCO takes what it lacks from its legs: source, symbol, side, quantity, prices and any fill."""
    oco["legs_folded"] = sorted(leg["order_id"] for leg in legs)
    if oco["placed_agent"] is None:
        tags = [leg["placed_agent"] for leg in legs if leg["placed_agent"]]
        oco["placed_agent"] = "agentic" if "agentic" in tags else (tags[0] if tags else None)
    for key in ("symbol", "side", "quantity"):
        if oco[key] is None:
            oco[key] = next((leg[key] for leg in legs if leg[key] is not None), None)
    if oco["price"] is None:
        oco["price"] = next((leg["price"] for leg in legs if leg["stop_price"] is None and leg["price"] is not None),
                            None)
    if oco["stop_price"] is None:
        oco["stop_price"] = next((leg["stop_price"] for leg in legs if leg["stop_price"] is not None), None)
    filled = [leg for leg in legs if leg["cumulative_quantity"] is not None and leg["cumulative_quantity"] > 0
              and leg["average_price"] is not None]
    if filled and not (oco["cumulative_quantity"] is not None and oco["cumulative_quantity"] > 0):
        qty = sum((leg["cumulative_quantity"] for leg in filled), Decimal(0))
        oco["cumulative_quantity"] = qty
        oco["average_price"] = sum((leg["cumulative_quantity"] * leg["average_price"] for leg in filled),
                                   Decimal(0)) / qty
        stamps = [leg["fill_dt"] for leg in filled if leg["fill_dt"] is not None]
        oco["fill_dt"] = max(stamps) if stamps else oco["fill_dt"]
    oco["notional"] = notional(oco)


def norm_place(row, i, source):
    """A place attempt from audit_verify `places` rows, or from a raw audit line."""
    field = "%s[%d]" % (source, i)
    if not isinstance(row, dict):
        raise InputError("BAD_AUDIT_EVENT", field, "each audit event must be an object")
    tool = _bare(row.get("tool"))
    event = row.get("event", "post")
    if event != "post" or not tool or not MONEY_RE.match(tool):
        return None
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
    ok = row.get("ok") if isinstance(row.get("ok"), bool) else result.get("ok") is not False

    def pick(key):
        value = row.get(key)
        if value is None:
            value = result.get(key) if key in ("order_id", "state") else inputs.get(key)
        return _s(value)

    dt = parse_ts(row.get("ts"))
    if dt is None:
        raise InputError("BAD_AUDIT_EVENT", field + ".ts", "ts must be an ISO 8601 time")
    mr = row.get("matched_review")
    return {
        "ts": _s(row.get("ts")), "dt": dt, "tool": tool, "asset": FAMILY_BY_TOOL.get(tool),
        "fingerprint": _s(row.get("fingerprint")), "ref_id": _s(row.get("ref_id")),
        "order_id": pick("order_id"), "state": pick("state"), "ok": ok,
        "symbol": (pick("symbol") or "").upper() or None, "side": (pick("side") or "").lower() or None,
        "quantity": dec(pick("quantity"), field + ".quantity"),
        "matched_review": mr if isinstance(mr, bool) else None,
        "review_age_s": row.get("review_age_s") if isinstance(row.get("review_age_s"), int) else None,
        "session": _s(row.get("session")),
    }


def norm_read(row, i):
    field = "reads[%d]" % i
    if not isinstance(row, dict):
        raise InputError("BAD_READ", field, "each read must be an object")
    status = str(row.get("status") or "").strip().lower()
    if status not in READ_STATUSES:
        raise InputError("BAD_READ", field + ".status", "status must be one of %s" % ", ".join(READ_STATUSES))
    acct = last4(row.get("account_last4"))
    if len(acct) != 4:
        raise InputError("BAD_READ", field + ".account_last4", "account_last4 must be the last 4 characters")
    flt = row.get("filter")
    flt = str(flt).strip().lower() if flt not in (None, "") else None
    return {"account_last4": acct, "tool": _bare(_s(row.get("tool"))), "filter": flt, "status": status}


# ---------------------------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------------------------
def match(orders, places):
    """Returns (pairs [(order, place, method)], unmatched_places)."""
    pairs = []
    used_orders, used_places = set(), set()
    by_id = {}
    for o in orders:
        for leg_id in o["legs_folded"]:
            by_id.setdefault(leg_id, o)  # a place call that returned a leg's id matches its OCO
    by_id.update((o["order_id"], o) for o in orders)
    for pi, p in enumerate(places):
        o = by_id.get(p["order_id"]) if p["order_id"] else None
        if o is not None and o["order_id"] not in used_orders:
            pairs.append((o, p, "order_id"))
            used_orders.add(o["order_id"])
            used_places.add(pi)
    by_ref = {}
    for o in orders:
        if o["ref_id"] and o["order_id"] not in used_orders:
            by_ref.setdefault(o["ref_id"], o)
    for pi, p in enumerate(places):
        if pi in used_places or not p["ref_id"]:
            continue
        o = by_ref.get(p["ref_id"])
        if o is not None and o["order_id"] not in used_orders:
            pairs.append((o, p, "ref_id"))
            used_orders.add(o["order_id"])
            used_places.add(pi)
    for pi in sorted((i for i in range(len(places)) if i not in used_places), key=lambda i: places[i]["dt"]):
        p = places[pi]
        best = None
        for o in orders:
            if o["order_id"] in used_orders or o["asset"] != p["asset"]:
                continue
            if o["placed_agent"] not in (None, "agentic"):
                continue
            delta = abs((o["created_dt"] - p["dt"]).total_seconds())
            if delta > MATCH_WINDOW_S:
                continue
            if p["symbol"] and o["symbol"] and p["symbol"] != o["symbol"]:
                continue
            if not (p["symbol"] and o["symbol"]) and p["asset"] != "option":
                continue
            if p["side"] and o["side"] and p["side"] != o["side"]:
                continue
            if p["quantity"] is None or o["quantity"] is None or p["quantity"] != o["quantity"]:
                continue
            key = (delta, o["created_dt"], o["order_id"])
            if best is None or key < best[0]:
                best = (key, o)
        if best is not None:
            o = best[1]
            method = "asset+symbol+side+quantity within 120 s" if p["symbol"] else \
                "asset+quantity within 120 s (no symbol in the logged input)"
            pairs.append((o, p, method))
            used_orders.add(o["order_id"])
            used_places.add(pi)
    return pairs, [p for i, p in enumerate(places) if i not in used_places]


def order_view(o, extra=None):
    view = {"order_id": o["order_id"], "account_last4": o["account_last4"], "asset": o["asset"],
            "symbol": o["symbol"], "side": o["side"], "quantity": _qty(o["quantity"]), "state": o["state"],
            "created_at": o["created_at"], "created_et": fmt_et(o["created_dt"]), "placed_agent": o["placed_agent"],
            "notional_usd": money(o["notional"])}
    if o["asset"] == "oco" and o["legs_folded"]:
        view["legs_folded"] = list(o["legs_folded"])
    if extra:
        view.update(extra)
    return view


def _qty(d):
    if d is None:
        return None
    text = format(d, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


# ---------------------------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------------------------
def op_run(data):
    start, end = parse_window(data.get("window"))
    agentic = last4(data.get("agentic_account_last4"))
    if len(agentic) != 4:
        raise InputError("MISSING_FIELD", "agentic_account_last4",
                         "give the Agentic account's last 4 characters (from get_accounts, agentic_allowed true)")
    raw_orders = data.get("broker_orders")
    if not isinstance(raw_orders, list):
        raise InputError("MISSING_FIELD", "broker_orders", "broker_orders must be a list (it may be empty)")
    raw_reads = data.get("reads")
    if not isinstance(raw_reads, list):
        raise InputError("MISSING_FIELD", "reads", "reads must list every order read you made and its status; "
                         "without it, completeness cannot be judged")
    orders = dedupe_orders([norm_order(r, i) for i, r in enumerate(raw_orders)])
    orders, legs_folded = fold_oco_legs(orders)
    reads = [norm_read(r, i) for i, r in enumerate(raw_reads)]
    notes = []
    if legs_folded:
        notes.append("%s counted once, as part of %s OCO" % (plural(legs_folded, "OCO leg order"),
                                                              "its" if legs_folded == 1 else "their"))

    audit = data.get("audit")
    if audit is not None and not isinstance(audit, dict):
        raise InputError("BAD_INPUT", "audit", "audit must be the audit_verify.py output object")
    audit = audit or {}
    if audit and audit.get("ok") is False:
        raise InputError("BAD_INPUT", "audit", "the audit_verify output is an error; fix that run first")
    events = data.get("audit_events")
    if events is not None and not isinstance(events, list):
        raise InputError("BAD_INPUT", "audit_events", "audit_events must be a list")
    places = []
    if events:
        places = [p for p in (norm_place(r, i, "audit_events") for i, r in enumerate(events)) if p]
    elif isinstance(audit.get("places"), list):
        places = [p for p in (norm_place(r, i, "audit.places") for i, r in enumerate(audit["places"])) if p]
    audit_available = bool(audit.get("available")) or bool(events)
    first_dt = parse_ts(audit.get("first_ts")) if audit.get("first_ts") else None
    if events and first_dt is None:
        stamps = [parse_ts(e.get("ts")) for e in events if isinstance(e, dict)]
        stamps = [s for s in stamps if s is not None]
        first_dt = min(stamps) if stamps else None
    if not audit_available:
        coverage = "none"
    elif first_dt is not None and first_dt <= start:
        coverage = "full"
    else:
        coverage = "partial"

    in_win = [o for o in orders if start <= o["created_dt"] < end]
    outside = len(orders) - len(in_win)
    agentic_orders = [o for o in in_win if o["account_last4"] == agentic]
    other_orders = [o for o in in_win if o["account_last4"] != agentic]
    margin = timedelta(seconds=MATCH_WINDOW_S)
    cand_places = [p for p in places if start - margin <= p["dt"] < end + margin]
    pairs, unmatched = match(agentic_orders, cand_places)

    matched = []
    matched_ids = set()
    placed_without_review = []
    for o, p, method in sorted(pairs, key=lambda t: (t[0]["created_dt"], t[0]["order_id"])):
        matched_ids.add(o["order_id"])
        matched.append(order_view(o, {"match_method": method, "audit_ts": p["ts"], "fingerprint": p["fingerprint"],
                                      "ref_id": p["ref_id"], "matched_review": p["matched_review"],
                                      "review_age_s": p["review_age_s"]}))
        if o["placed_agent"] not in (None, "agentic"):
            notes.append("order %s was sent by this kit but Robinhood marks it %r; check the order in the app"
                         % (o["order_id"], o["placed_agent"]))
        if p["matched_review"] is False:
            placed_without_review.append(order_view(o, {"audit_ts": p["ts"], "fingerprint": p["fingerprint"],
                                                        "reason": PWR_REASON}))
    if any(p["matched_review"] is None for _, p, _ in pairs):
        notes.append("some logged place calls carry no review information; pass audit_verify output to check "
                     "for placed-without-review")

    broker_only, unattributed, source_unknown, user_in_agentic = [], [], [], []
    untagged = 0
    for o in sorted(agentic_orders, key=lambda x: (x["created_dt"], x["order_id"])):
        if o["order_id"] in matched_ids:
            continue
        if o["placed_agent"] is None:
            if o["asset"] in SOURCED_ASSETS:
                untagged += 1
                source_unknown.append(order_view(o, {"reason": UNTAGGED_REASON}))
            else:
                source_unknown.append(order_view(o, {"reason": SOURCE_UNKNOWN_REASON}))
        elif o["placed_agent"] == "agentic":
            if coverage != "none" and first_dt is not None and first_dt <= o["created_dt"]:
                broker_only.append(order_view(o, {"reason": BROKER_ONLY_REASON}))
            else:
                reason = UNATTRIBUTED_NO_LOG if coverage == "none" else UNATTRIBUTED_BEFORE_LOG
                unattributed.append(order_view(o, {"reason": reason}))
        else:
            user_in_agentic.append(order_view(o))

    outside_agentic = [order_view(o) for o in sorted(other_orders, key=lambda x: (x["created_dt"], x["order_id"]))
                       if o["placed_agent"] == "agentic"]
    ignored_other = sum(1 for o in other_orders if o["placed_agent"] != "agentic")
    if ignored_other:
        notes.append("%s from read-only accounts without the agent tag %s ignored (this report covers agent "
                     "activity)" % (plural(ignored_other, "order"), "was" if ignored_other == 1 else "were"))

    audit_only = []
    for p in sorted(unmatched, key=lambda x: x["dt"]):
        if not (start <= p["dt"] < end):
            continue
        detail = ("the place call returned an error" if not p["ok"] else
                  "the call was sent but no broker order matched in the reads for this window: check the "
                  "window, the account, or the order in the app")
        audit_only.append({"ref_id": p["ref_id"], "ts": p["ts"], "ts_et": fmt_et(p["dt"]), "tool": p["tool"],
                           "symbol": p["symbol"], "side": p["side"], "quantity": _qty(p["quantity"]),
                           "order_id": p["order_id"], "ok": p["ok"], "reason": AUDIT_ONLY_REASON, "detail": detail})

    # read coverage
    incomplete = [r for r in reads if r["status"] in ("partial", "failed")]
    not_enabled = [r for r in reads if r["status"] == "not_enabled"]
    for r in not_enabled:
        notes.append("%s on %s: not enabled for this account (the tool family is off); this is not the same "
                     "as having none" % (r["tool"], masked(r["account_last4"])))
    missing_agentic = []
    for tool, flt in AGENTIC_REQUIRED_READS:
        found = [r for r in reads if r["account_last4"] == agentic and r["tool"] == tool
                 and (flt is None or r["filter"] == flt)]
        if not found:
            missing_agentic.append("%s%s" % (tool, "" if flt is None else
                                             " (%s)" % ("all sources" if flt == "all" else flt)))
    adv_reads = [r for r in reads if r["account_last4"] == agentic and r["tool"] == "get_advanced_orders"]
    if adv_reads and not any(r["status"] == "complete" for r in adv_reads) and any(
            o["asset"] == "equity" for o in agentic_orders):
        notes.append("OCO legs can't be told apart from single orders: get_advanced_orders on %s was %s, so an "
                     "OCO's two legs, if any, are counted as two equity orders"
                     % (masked(agentic), "/".join(sorted({r["status"].replace("_", " ") for r in adv_reads}))))
    accounts = data.get("accounts")
    ro_accounts = []
    if isinstance(accounts, list):
        for i, a in enumerate(accounts):
            if not isinstance(a, dict):
                raise InputError("BAD_INPUT", "accounts[%d]" % i, "each account must be an object")
            l4 = last4(a.get("last4"))
            if l4 and l4 != agentic and not a.get("agentic"):
                ro_accounts.append(l4)
    for r in reads:
        if r["account_last4"] != agentic and r["account_last4"] not in ro_accounts:
            ro_accounts.append(r["account_last4"])
    verification = []
    found_outside = {o["account_last4"] for o in outside_agentic}
    for l4 in ro_accounts:
        rs = [r for r in reads if r["account_last4"] == l4]
        checked = sorted({r["tool"] for r in rs if r["status"] == "complete" and r["filter"] == "agentic"})
        if l4 in found_outside:
            status = "agent_orders_found"
        elif any(r["status"] == "not_in_scope" for r in rs):
            status = "not_in_scope"
        elif not rs:
            status = "not_checked"
        elif any(r["status"] in ("partial", "failed") for r in rs) or not set(READ_ONLY_REQUIRED) <= set(checked):
            status = "incomplete"
        else:
            status = "verified_none"
        verification.append({"account_last4": l4, "status": status, "checked": checked})

    # status
    at_stake = []

    def stake(kind, rows, text):
        if not rows:
            return
        known = [dec(r["notional_usd"]) for r in rows if r.get("notional_usd") is not None]
        unknown = len(rows) - len(known)
        total = sum(known, Decimal(0)) if known else None
        amount = usd(total) if total is not None else "amount unknown"
        if total is not None and unknown:
            amount += " + %d amount%s unknown" % (unknown, "" if unknown == 1 else "s")
        at_stake.append({"kind": kind, "count": len(rows), "usd": money(total), "text": text(len(rows), amount)})

    stake("broker_only", broker_only, lambda n, a: "%s (%s) in Agentic %s %s no local audit entry (placed by "
          "another agent, app or machine)" % (plural(n, "order"), a, masked(agentic), "has" if n == 1 else "have"))
    stake("agent_orders_outside_agentic", outside_agentic, lambda n, a: "%s (%s) found in read-only account(s) %s"
          % (plural(n, "agent order"), a, ", ".join(masked(x) for x in sorted(found_outside))))
    stake("placed_without_review", placed_without_review, lambda n, a: "%s (%s) placed without a matching review "
          "in the log" % (plural(n, "order"), a))
    stake("user_orders_in_agentic", user_in_agentic, lambda n, a: "%s (%s) in Agentic %s that Robinhood marks as "
          "not placed by an agent" % (plural(n, "order"), a, masked(agentic)))
    at_stake.sort(key=lambda s: (s["usd"] is None, -(dec(s["usd"]) or Decimal(0)), s["kind"]))

    flags = []
    inj = audit.get("possible_injection") if isinstance(audit.get("possible_injection"), list) else []
    if inj:
        after = sorted({str(x.get("after_tool")) for x in inj if isinstance(x, dict)})
        flags.append({"flag": "possible_injection", "count": len(inj),
                      "text": "%s right after %s" % (plural(len(inj), "possible prompt-injection attempt"),
                                                     ", ".join(after))})
    if audit.get("chain_ok") is False:
        breaks = audit.get("breaks") if isinstance(audit.get("breaks"), list) else []
        where = ""
        if breaks and isinstance(breaks[0], dict):
            where = " at %s line %s" % (breaks[0].get("file"), breaks[0].get("line"))
        flags.append({"flag": "audit_chain_broken", "count": len(breaks),
                      "text": "local audit log chain broken%s (a line was changed or removed)" % where})
    sent_no_order = [a for a in audit_only if a["ok"]]
    if sent_no_order:
        flags.append({"flag": "place_sent_no_broker_order", "count": len(sent_no_order),
                      "text": "%s logged as sent with no matching broker order" % plural(len(sent_no_order),
                                                                                         "place call")})
    unknown_reasons = []
    if untagged:
        unknown_reasons.append("%s in Agentic passed with no source tag" % plural(untagged, "equity/option order"))
    if incomplete:
        unknown_reasons.append("%s incomplete" % plural(len(incomplete), "order read"))
    if missing_agentic:
        unknown_reasons.append("not read for Agentic: %s" % ", ".join(missing_agentic))
    unverified = [v["account_last4"] for v in verification if v["status"] in ("not_checked", "incomplete")]
    if unverified:
        unknown_reasons.append("read-only account(s) not verified: %s" % ", ".join(masked(x) for x in unverified))

    if at_stake or flags:
        status = "ACTION NEEDED"
    elif unknown_reasons:
        status = "UNKNOWN"
    else:
        status = "CLEAR"
    parts = [s["text"] for s in at_stake[:3]] + [f["text"] for f in flags]
    if status == "UNKNOWN":
        line = "UNKNOWN: Dollars at stake: none found in what was read; " + "; ".join(unknown_reasons)
    else:
        if not at_stake:
            parts = ["none found"] + parts
        line = "%s: Dollars at stake: %s" % (status, " · ".join(parts))
        if status == "ACTION NEEDED" and unknown_reasons:
            notes.append("also incomplete: " + "; ".join(unknown_reasons))
    in_scope_excluded = [v["account_last4"] for v in verification if v["status"] == "not_in_scope"]
    if in_scope_excluded:
        notes.append("excluded by you (not checked): %s" % ", ".join(masked(x) for x in in_scope_excluded))
    if coverage == "none":
        notes.append("no local audit log: agent orders are listed, but which agent placed them cannot be told")
    if outside:
        notes.append("%s outside the window %s ignored" % (plural(outside, "order"), "was" if outside == 1 else "were"))

    agent_orders = [o for o in agentic_orders if o["placed_agent"] == "agentic" or o["order_id"] in matched_ids]
    return {
        "ok": True,
        "window": window_out(start, end),
        "agentic_account_last4": agentic,
        "audit_coverage": coverage,
        "counts": {
            "agentic_orders": len(agentic_orders), "agent_orders": len(agent_orders), "matched": len(matched),
            "broker_only": len(broker_only), "unattributed": len(unattributed), "audit_only": len(audit_only),
            "placed_without_review": len(placed_without_review), "source_unknown": len(source_unknown),
            "user_orders_in_agentic": len(user_in_agentic),
            "agent_orders_outside_agentic": len(outside_agentic), "outside_window": outside,
            "oco_legs_folded": legs_folded,
        },
        "matched": matched,
        "broker_only": broker_only,
        "unattributed": unattributed,
        "audit_only": audit_only,
        "placed_without_review": placed_without_review,
        "source_unknown": source_unknown,
        "user_orders_in_agentic": user_in_agentic,
        "agent_orders_outside_agentic": outside_agentic,
        "read_only_verification": verification,
        "incomplete_reads": [dict(r) for r in incomplete],
        "missing_reads": missing_agentic,
        "flags": flags,
        "status": status,
        "at_stake": at_stake,
        "status_line": line,
        "match_method": MATCH_METHOD,
        "notes": notes,
    }


OPS = {"run": op_run}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected: run" % op)
    if not isinstance(data, dict):
        return _err("BAD_INPUT", "", "input must be a JSON object")
    try:
        return OPS[op](data)
    except InputError as exc:
        return _err(exc.code, exc.field, exc.msg)


# ---------------------------------------------------------------------------------------------
# JSON Schemas (--schema) and the small validator used by --selftest
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_I = {"type": "integer"}
_IN = {"type": ["integer", "null"]}
_B = {"type": "boolean"}
_BN = {"type": ["boolean", "null"]}
_NUMS = {"type": ["string", "integer", "null"]}


def _obj(props, required=(), extra=False):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": extra}


_ERR = _obj({"ok": {"enum": [False]}, "errors": {"type": "array", "items": _obj(
    {"code": _S, "field": _S, "msg": _S}, ["code", "field", "msg"])}}, ["ok", "errors"])
_WINDOW_IN = {"anyOf": [_obj({"start": _S, "end": _S}, ["start", "end"]),
                        _obj({"start_date": _S, "end_date": _S}, ["start_date", "end_date"])]}
_ORDER_IN = _obj({
    "account_last4": _S, "asset": {"enum": list(ASSETS)}, "order_id": _S, "ref_id": _SN, "symbol": _SN, "side": _SN,
    "quantity": _NUMS, "state": _SN, "created_at": _S, "placed_agent": _SN, "type": _SN, "price": _NUMS,
    "stop_price": _NUMS, "dollar_based_amount": _NUMS, "average_price": _NUMS, "cumulative_quantity": _NUMS,
    "last_transaction_at": _SN, "multiplier": _NUMS, "leg_order_ids": {"type": "array", "items": _S}},
    ["account_last4", "asset", "order_id", "created_at"], extra=True)
_ORDER_OUT = _obj({
    "order_id": _S, "account_last4": _S, "asset": _S, "symbol": _SN, "side": _SN, "quantity": _SN, "state": _SN,
    "created_at": _SN, "created_et": _SN, "placed_agent": _SN, "notional_usd": _SN},
    ["order_id", "account_last4", "asset", "notional_usd"], extra=True)
SCHEMAS = {
    "run": {
        "input": _obj({
            "window": _WINDOW_IN, "agentic_account_last4": _S,
            "accounts": {"type": "array", "items": _obj({"last4": _S, "agentic": _B}, ["last4"], extra=True)},
            "broker_orders": {"type": "array", "items": _ORDER_IN},
            "reads": {"type": "array", "items": _obj({"account_last4": _S, "tool": _S, "filter": _SN,
                                                      "status": {"enum": list(READ_STATUSES)}},
                                                     ["account_last4", "tool", "status"])},
            "audit": {"type": "object"}, "audit_events": {"type": "array"},
        }, ["window", "agentic_account_last4", "broker_orders", "reads"]),
        "output": {"anyOf": [_obj({
            "ok": {"enum": [True]},
            "window": _obj({"start": _S, "end": _S, "label_et": _S}, ["start", "end", "label_et"]),
            "agentic_account_last4": _S, "audit_coverage": {"enum": ["full", "partial", "none"]},
            "counts": {"type": "object", "additionalProperties": _I},
            "matched": {"type": "array", "items": _ORDER_OUT}, "broker_only": {"type": "array", "items": _ORDER_OUT},
            "unattributed": {"type": "array", "items": _ORDER_OUT}, "audit_only": {"type": "array"},
            "placed_without_review": {"type": "array", "items": _ORDER_OUT},
            "source_unknown": {"type": "array", "items": _ORDER_OUT},
            "user_orders_in_agentic": {"type": "array", "items": _ORDER_OUT},
            "agent_orders_outside_agentic": {"type": "array", "items": _ORDER_OUT},
            "read_only_verification": {"type": "array", "items": _obj({
                "account_last4": _S, "checked": {"type": "array", "items": _S},
                "status": {"enum": ["verified_none", "agent_orders_found", "not_checked", "incomplete",
                                    "not_in_scope"]}}, ["account_last4", "status", "checked"])},
            "incomplete_reads": {"type": "array"}, "missing_reads": {"type": "array", "items": _S},
            "flags": {"type": "array", "items": _obj({"flag": _S, "count": _I, "text": _S}, ["flag", "count", "text"])},
            "status": {"enum": ["ACTION NEEDED", "UNKNOWN", "CLEAR"]},
            "at_stake": {"type": "array", "items": _obj({"kind": _S, "count": _I, "usd": _SN, "text": _S},
                                                        ["kind", "count", "usd", "text"])},
            "status_line": _S, "match_method": _S, "notes": {"type": "array", "items": _S},
        }, ["ok", "window", "audit_coverage", "counts", "matched", "broker_only", "audit_only",
            "placed_without_review", "source_unknown", "user_orders_in_agentic", "agent_orders_outside_agentic",
            "read_only_verification", "status", "at_stake", "status_line", "match_method"]), _ERR]},
    }
}


def _type_ok(value, t):
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    return False


def schema_errors(value, schema, path="$"):
    """Minimal JSON Schema check (type, enum, properties, required, additionalProperties, items, anyOf)."""
    if "anyOf" in schema:
        if any(not schema_errors(value, s, path) for s in schema["anyOf"]):
            return []
        return ["%s: matches no anyOf branch" % path]
    errs = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(value, x) for x in types):
            return ["%s: expected %s" % (path, "/".join(types))]
    if "enum" in schema and not any(value == e and type(value) is type(e) for e in schema["enum"]):
        errs.append("%s: %r not in enum" % (path, value))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errs.append("%s: missing %s" % (path, key))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, sub in value.items():
            if key in props:
                errs.extend(schema_errors(sub, props[key], "%s.%s" % (path, key)))
            elif extra is False:
                errs.append("%s: unexpected key %s" % (path, key))
            elif isinstance(extra, dict):
                errs.extend(schema_errors(sub, extra, "%s.%s" % (path, key)))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs.extend(schema_errors(item, schema["items"], "%s[%d]" % (path, i)))
    return errs


def full_schema():
    ops = {}
    for name, pair in SCHEMAS.items():
        ops[name] = {
            "input": dict(pair["input"], **{"$schema": "https://json-schema.org/draft/2020-12/schema"}),
            "output": dict(pair["output"], **{"$schema": "https://json-schema.org/draft/2020-12/schema"}),
        }
    return {"script": SCRIPT, "version": VERSION, "ops": ops}


# ---------------------------------------------------------------------------------------------
# Self-test (tests/golden/reconcile/ covers much more)
# ---------------------------------------------------------------------------------------------
_READS_OK = [
    {"account_last4": "X4F1", "tool": "get_equity_orders", "filter": "agentic", "status": "complete"},
    {"account_last4": "X4F1", "tool": "get_equity_orders", "filter": "all", "status": "complete"},
    {"account_last4": "X4F1", "tool": "get_option_orders", "filter": "agentic", "status": "complete"},
    {"account_last4": "X4F1", "tool": "get_option_orders", "filter": "all", "status": "complete"},
    {"account_last4": "X4F1", "tool": "get_crypto_orders", "status": "complete"},
    {"account_last4": "X4F1", "tool": "get_advanced_orders", "status": "not_enabled"},
    {"account_last4": "M7Q5", "tool": "get_equity_orders", "filter": "agentic", "status": "complete"},
    {"account_last4": "M7Q5", "tool": "get_option_orders", "filter": "agentic", "status": "complete"},
]
_AUDIT = {"ok": True, "available": True, "first_ts": "2026-11-01T12:00:00Z", "chain_ok": True,
          "possible_injection": [], "places": [
              {"ts": "2026-11-09T15:05:13.000Z", "tool": "place_equity_order", "order_id": "e1",
               "fingerprint": "sha256:aa", "ref_id": "r1", "ok": True, "symbol": "PLTR", "side": "buy",
               "quantity": "10", "matched_review": True, "review_age_s": 40},
              {"ts": "2026-11-10T15:15:02.000Z", "tool": "place_equity_order", "order_id": None,
               "fingerprint": "sha256:bb", "ref_id": "r2", "ok": True, "symbol": "AMD", "side": "buy",
               "quantity": "2", "matched_review": False, "review_age_s": None}]}
EXAMPLES = [
    ({"window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
      "broker_orders": [
          {"account_last4": "X4F1", "asset": "equity", "order_id": "e1", "symbol": "PLTR", "side": "buy",
           "quantity": "10", "state": "filled", "created_at": "2026-11-09T15:05:12Z", "placed_agent": "agentic",
           "average_price": "29.48", "cumulative_quantity": "10"},
          {"account_last4": "X4F1", "asset": "equity", "order_id": "e3", "symbol": "AMD", "side": "buy",
           "quantity": "2", "state": "filled", "created_at": "2026-11-10T15:16:00Z", "placed_agent": "agentic",
           "average_price": "153.85", "cumulative_quantity": "2"},
          {"account_last4": "X4F1", "asset": "equity", "order_id": "e9", "symbol": "PLTR", "side": "buy",
           "quantity": "12.875", "state": "filled", "created_at": "2026-11-13T16:20:00Z", "placed_agent": "agentic",
           "dollar_based_amount": "412.00", "average_price": "32.00", "cumulative_quantity": "12.875"},
          {"account_last4": "X4F1", "asset": "crypto", "order_id": "c1", "symbol": "ETH", "side": "sell",
           "quantity": "0.42", "state": "confirmed", "created_at": "2026-11-13T14:00:00Z", "stop_price": "2600"}],
      "reads": _READS_OK, "audit": _AUDIT},
     {"ok": True, "status": "ACTION NEEDED", "audit_coverage": "full",
      "counts": {"matched": 2, "broker_only": 1, "placed_without_review": 1, "source_unknown": 1},
      "broker_only": [{"order_id": "e9", "notional_usd": "412.00", "reason": BROKER_ONLY_REASON}],
      "matched": [{"order_id": "e1", "match_method": "order_id"},
                  {"order_id": "e3", "match_method": "asset+symbol+side+quantity within 120 s"}],
      "read_only_verification": [{"account_last4": "M7Q5", "status": "verified_none"}],
      "status_line": "ACTION NEEDED: Dollars at stake: 1 order ($412.00) in Agentic " + MASK + "X4F1 has "
                     "no local audit entry (placed by another agent, app or machine) · 1 order ($307.70) "
                     "placed without a matching review in the log"}),
    ({"window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
      "accounts": [{"last4": "X4F1", "agentic": True}, {"last4": "M7Q5", "agentic": False}],
      "broker_orders": [], "reads": _READS_OK[:5]},
     {"ok": True, "status": "UNKNOWN", "audit_coverage": "none", "missing_reads": ["get_advanced_orders"],
      "read_only_verification": [{"account_last4": "M7Q5", "status": "not_checked"}],
      "status_line": "UNKNOWN: Dollars at stake: none found in what was read; not read for Agentic: "
                     "get_advanced_orders; read-only account(s) not verified: " + MASK + "M7Q5"}),
    ({"window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
      "broker_orders": [], "reads": _READS_OK},
     {"ok": True, "status": "CLEAR", "status_line": "CLEAR: Dollars at stake: none found"}),
    ({"window": {"start": "2026-11-09", "end": "2026-11-16"}, "agentic_account_last4": "X4F1",
      "broker_orders": [], "reads": []},
     {"ok": False, "errors": [{"code": "BAD_WINDOW"}]}),
    # recurring, DRIP and a user-placed option order in Agentic, from the all-sources reads: never CLEAR
    ({"window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
      "broker_orders": [
          {"account_last4": "X4F1", "asset": "equity", "order_id": "r1", "symbol": "VOO", "side": "buy",
           "quantity": "0.1", "state": "filled", "created_at": "2026-11-10T14:31:00Z", "placed_agent": "recurring",
           "dollar_based_amount": "50.00"},
          {"account_last4": "X4F1", "asset": "equity", "order_id": "d1", "symbol": "KO", "side": "buy",
           "quantity": "0.2", "state": "filled", "created_at": "2026-11-11T14:31:00Z", "placed_agent": "drip",
           "average_price": "70.00", "cumulative_quantity": "0.2"},
          {"account_last4": "X4F1", "asset": "option", "order_id": "o1", "symbol": "KO", "side": "sell",
           "quantity": "1", "state": "filled", "created_at": "2026-11-12T15:00:00Z", "placed_agent": "not_agentic",
           "price": "1.10"}],
      "reads": _READS_OK, "audit": dict(_AUDIT, places=[])},
     {"ok": True, "status": "ACTION NEEDED", "counts": {"user_orders_in_agentic": 3, "source_unknown": 0},
      "user_orders_in_agentic": [{"order_id": "r1", "placed_agent": "recurring"},
                                 {"order_id": "d1", "placed_agent": "drip"},
                                 {"order_id": "o1", "placed_agent": "not_agentic", "notional_usd": "110.00"}],
      "status_line": "ACTION NEEDED: Dollars at stake: 3 orders ($174.00) in Agentic " + MASK + "X4F1 that "
                     "Robinhood marks as not placed by an agent"}),
    # the same agent order from both reads counts once; an OCO and its two legs count once
    ({"window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
      "broker_orders": [
          {"account_last4": "X4F1", "asset": "equity", "order_id": "e1", "symbol": "PLTR", "side": "buy",
           "quantity": "10", "state": "filled", "created_at": "2026-11-09T15:05:12Z", "placed_agent": "agentic",
           "average_price": "29.48", "cumulative_quantity": "10"},
          {"account_last4": "X4F1", "asset": "equity", "order_id": "e1", "symbol": "PLTR", "side": "buy",
           "quantity": "10", "state": "filled", "created_at": "2026-11-09T15:05:12Z", "placed_agent": "not_agentic",
           "average_price": "29.48", "cumulative_quantity": "10"},
          {"account_last4": "X4F1", "asset": "oco", "order_id": "a1", "symbol": "PLTR", "side": "sell",
           "quantity": "10", "state": "active", "created_at": "2026-11-12T15:00:00Z", "price": "34.00",
           "stop_price": "26.00", "leg_order_ids": ["l1", "l2"]},
          {"account_last4": "X4F1", "asset": "equity", "order_id": "l1", "symbol": "PLTR", "side": "sell",
           "quantity": "10", "state": "confirmed", "created_at": "2026-11-12T15:00:00Z", "placed_agent": "agentic",
           "type": "limit", "price": "34.00"},
          {"account_last4": "X4F1", "asset": "equity", "order_id": "l2", "symbol": "PLTR", "side": "sell",
           "quantity": "10", "state": "confirmed", "created_at": "2026-11-12T15:00:00Z", "placed_agent": "agentic",
           "type": "market", "stop_price": "26.00"}],
      "reads": _READS_OK, "audit": _AUDIT},
     {"ok": True, "status": "ACTION NEEDED",
      "counts": {"agentic_orders": 2, "agent_orders": 2, "matched": 1, "broker_only": 1, "oco_legs_folded": 2,
                 "user_orders_in_agentic": 0},
      "broker_only": [{"order_id": "a1", "asset": "oco", "placed_agent": "agentic", "notional_usd": "340.00",
                       "legs_folded": ["l1", "l2"]}]}),
    # an equity row passed with no source tag is a mapping gap: UNKNOWN, never CLEAR
    ({"window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
      "broker_orders": [
          {"account_last4": "X4F1", "asset": "equity", "order_id": "x1", "symbol": "VOO", "side": "buy",
           "quantity": "1", "state": "filled", "created_at": "2026-11-10T14:31:00Z"}],
      "reads": _READS_OK},
     {"ok": True, "status": "UNKNOWN", "source_unknown": [{"order_id": "x1", "reason": UNTAGGED_REASON}],
      "status_line": "UNKNOWN: Dollars at stake: none found in what was read; 1 equity/option order in "
                     "Agentic passed with no source tag"}),
]


def _subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) <= len(actual) and all(
            _subset(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def selftest():
    failures = []
    for i, (inp, expected) in enumerate(EXAMPLES):
        out = run("run", inp)
        problems = schema_errors(out, SCHEMAS["run"]["output"])
        if not _subset(expected, out):
            problems.append("output mismatch: %s" % json.dumps(out, sort_keys=True)[:800])
        if problems:
            failures.append({"case": i, "problems": problems})
    return {"ok": not failures, "script": SCRIPT, "selftest": {"cases": len(EXAMPLES), "failed": failures}}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--schema" in argv:
        print(json.dumps(full_schema(), indent=2, sort_keys=True))
        return 0
    if "--selftest" in argv:
        result = selftest()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1
    if not argv:
        print(json.dumps(_err("MISSING_OP", "op", "usage: reconcile.py run < input.json")))
        return 0
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        print(json.dumps(_err("BAD_JSON", "", "stdin is not valid JSON: %s" % exc)))
        return 0
    print(json.dumps(run(argv[0], data), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
