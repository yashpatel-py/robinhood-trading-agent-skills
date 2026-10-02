#!/usr/bin/env python3
"""lot_select.py - compare specific-lot sell choices side by side, and validate a tax_lots array.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: when a user sells part of a position built over time, choosing lots is choosing a tax
bill. Robinhood sells first-in, first-out unless the order names lots, and review_equity_order accepts a
`tax_lots` array of {open_lot_id, quantity} whose quantities must sum exactly to the order quantity, each
within the lot's quantity_available, at most 30 lots, and never with dollar_amount, stop orders,
all_day_hours or fractional limit orders. Getting that array wrong wastes a review; getting the choice
wrong costs real money. This script lays the common choices next to each other with their realized
gain or loss and short/long-term split, and never says which one is "best": that decision depends on the
user's whole tax picture, which the agent does not have.

Ops:
  compare   per strategy: the tax_lots array, realized $ (short and long term), lot count, validity.
            Strategies: fifo (Robinhood's default), highest_cost, lowest_cost, long_term_first,
            losses_first. No "best" key anywhere, by design.
  validate  checks a tax_lots array against the lots and the order before review_equity_order.

Holding period (IRS Pub 550): it starts the day after acquisition, and long-term means held MORE than
one year, so a lot is long-term from the day after its one-year anniversary (2025-12-03 -> 2026-12-04).
A lot bought on the last day of a month is long-term from the first day of the 13th month after it
(Rev. Rul. 66-7: 2024-02-29 -> 2025-03-01, 2027-02-28 -> 2028-03-01). Same rule as holding_period.py.
Robinhood's own `term` for a lot wins over the date arithmetic when the two differ (a wash-sale
replacement lot carries the washed shares' holding period, and inherited or gifted lots can too), and the
disagreement is reported: Robinhood's tax documents govern. Acquisition dates are US Eastern trade dates:
a timestamp is converted to its ET calendar date, never read as a UTC date (same as holding_period.py).

Usage:
    python3 lot_select.py <compare|validate> < input.json > output.json
    python3 lot_select.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0 whenever
JSON was printed, exit 1 only on a crash. Estimates are before fees and at the price you pass.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

VERSION = "2.0.0"
SCRIPT = "lot_select"
CENT = Decimal("0.01")
ZERO = Decimal(0)
MAX_LOTS = 30
STRATEGIES = ("fifo", "highest_cost", "lowest_cost", "long_term_first", "losses_first")
FIFO_LABEL = "Robinhood's default"
ORDER_DISCLOSURE = {
    "fifo": "oldest acquisition first (what Robinhood does when the order names no lots)",
    "highest_cost": "highest cost per share first (usually the smallest gain or largest loss)",
    "lowest_cost": "lowest cost per share first (usually the largest gain)",
    "long_term_first": "long-term lots first, oldest first within each term",
    "losses_first": ("short-term losses, then long-term losses (largest loss per share first), then long-term "
                     "gains, then short-term gains (smallest gain per share first)"),
}
STOP_TYPES = ("stop_market", "stop_limit")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True  # sibling imports must not leave __pycache__ in a skill folder


def _import_rh_time():
    """rh_time.py sits next to this script in every skill (synced); in the repo it is in shared/scripts."""
    for folder in (_HERE, os.path.normpath(os.path.join(_HERE, "..", "..", "..", "shared", "scripts"))):
        if os.path.isfile(os.path.join(folder, "rh_time.py")):
            if folder not in sys.path:
                sys.path.insert(0, folder)
            try:
                import rh_time as module  # noqa: E402

                return module
            except Exception:  # pragma: no cover - a broken copy behaves like a missing one
                return None
    return None


rh_time = _import_rh_time()


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


_DEC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")


def dec(value, field, required=True, positive=False, nonneg=False, strict_string=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InputError("MISSING_FIELD", field, "%s is required" % field)
        return None
    if isinstance(value, bool) or (strict_string and not isinstance(value, str)):
        raise InputError("BAD_QUANTITY", field, "%s must be a decimal string like \"5\" or \"0.25\"" % field)
    text = repr(value) if isinstance(value, float) else str(value).strip()
    if not _DEC_RE.match(text):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string (got %r)" % (field, value))
    d = Decimal(text)
    if positive and d <= 0:
        raise InputError("BAD_VALUE", field, "%s must be greater than zero" % field)
    if nonneg and d < 0:
        raise InputError("BAD_VALUE", field, "%s must not be negative" % field)
    return d


def money(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def qty(d):
    text = format(d.normalize(), "f")
    return "0" if text in ("-0", "") else text


def parse_day(value, field):
    """A YYYY-MM-DD date, or an ISO timestamp converted to its US Eastern calendar date (never read as a UTC
    date: an evening ET purchase has the next day's UTC date). Same rule as holding_period.parse_day."""
    if not isinstance(value, str) or not value.strip():
        raise InputError("BAD_DATE", field, "expected YYYY-MM-DD (got %r)" % (value,))
    text = value.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError as exc:
            raise InputError("BAD_DATE", field, "not a real calendar date: %s" % exc)
    if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]", text):
        raise InputError("BAD_DATE", field, "expected YYYY-MM-DD or an ISO timestamp (got %r)" % (value,))
    if rh_time is None:
        raise InputError("RH_TIME_MISSING", field, "rh_time.py is not next to this script; pass acquisition dates as "
                                                   "YYYY-MM-DD (US Eastern)")
    out = rh_time.run("to_et", {"utc": text})
    if not out.get("ok"):
        raise InputError("BAD_DATE", field, out["errors"][0]["msg"])
    return datetime.strptime(out["date_et"], "%Y-%m-%d").date()


def broker_term(value):
    """Robinhood's reported term for a lot, normalized to long/short; None when absent or unrecognized."""
    text = str(value or "").strip().lower()
    if text.startswith("long"):
        return "long"
    if text.startswith("short"):
        return "short"
    return None


def long_term_on(acquired):
    """First day the lot is long-term (held more than one year) -> (date, False).

    The day after the one-year anniversary, except that a lot acquired on the last day of a month is long-term
    from the first day of the 13th month after it (Rev. Rul. 66-7): 2027-02-28 -> 2028-03-01, 2024-02-29 ->
    2025-03-01. Same rule as holding_period.long_term_on. The second value is the old leap-day flag, kept for
    the output shape; Rev. Rul. 66-7 settles the Feb 29 case, so it is always False."""
    nxt = acquired + timedelta(days=1)
    if nxt.day == 1:  # acquired on the last day of its month
        return date(nxt.year + 1, nxt.month, 1), False
    return acquired.replace(year=acquired.year + 1) + timedelta(days=1), False


def term_on(acquired, on_date):
    lt, _ = long_term_on(acquired)
    return "long" if on_date >= lt else "short"


def _today():
    return datetime.now().date()


def norm_lots(lots, need_dates=True):
    if not isinstance(lots, list) or not lots:
        raise InputError("MISSING_FIELD", "lots", "give lots from get_equity_tax_lots (every page)")
    out, seen = [], set()
    for i, lot in enumerate(lots):
        where = "lots[%d]" % i
        if not isinstance(lot, dict):
            raise InputError("BAD_VALUE", where, "each lot is an object")
        lot_id = str(lot.get("open_lot_id") or "").strip()
        if not lot_id:
            raise InputError("MISSING_FIELD", where + ".open_lot_id", "open_lot_id is required")
        if lot_id in seen:
            raise InputError("DUPLICATE_LOT", where + ".open_lot_id", "open_lot_id %s appears twice" % lot_id)
        seen.add(lot_id)
        avail = lot.get("quantity_available", lot.get("quantity"))
        rec = {
            "open_lot_id": lot_id,
            "available": dec(avail, where + ".quantity_available", nonneg=True),
            "cost": dec(lot.get("cost_per_share"), where + ".cost_per_share", required=False, nonneg=True),
            "acquired": parse_day(lot.get("acquired") or lot.get("open_date"), where + ".acquired")
            if need_dates else None,
            "selectable": lot.get("is_selectable") is not False,
            "broker_term": broker_term(lot.get("term")),
        }
        out.append(rec)
    return out


def set_terms(lots, as_of):
    """Each lot's term on as_of: Robinhood's reported term when given, else the date arithmetic."""
    for l in lots:
        l["date_term"] = term_on(l["acquired"], as_of)
        l["term"] = l["broker_term"] or l["date_term"]


def _order_key(name, price, as_of):
    def fifo(l):
        return (l["acquired"], l["open_lot_id"])

    def cost_missing(l):
        return l["cost"] is None

    if name == "fifo":
        return fifo
    if name == "highest_cost":
        return lambda l: (cost_missing(l), -(l["cost"] or ZERO), l["acquired"], l["open_lot_id"])
    if name == "lowest_cost":
        return lambda l: (cost_missing(l), l["cost"] or ZERO, l["acquired"], l["open_lot_id"])
    if name == "long_term_first":
        return lambda l: (0 if l["term"] == "long" else 1, l["acquired"], l["open_lot_id"])

    def losses(l):
        if l["cost"] is None:
            return (4, ZERO, l["acquired"], l["open_lot_id"])
        per = price - l["cost"]
        long_term = l["term"] == "long"
        if per < 0:
            group = 1 if long_term else 0
        else:
            group = 2 if long_term else 3
        return (group, per, l["acquired"], l["open_lot_id"])

    return losses


def op_compare(data):
    symbol = str(data.get("symbol", "")).strip().upper()
    sell = dec(data.get("sell_quantity"), "sell_quantity", positive=True)
    price = dec(data.get("price"), "price", positive=True)
    as_of = parse_day(data.get("as_of"), "as_of") if data.get("as_of") else _today()
    wanted = data.get("strategies") or list(STRATEGIES)
    if not isinstance(wanted, list):
        raise InputError("BAD_VALUE", "strategies", "strategies is a list")
    for s in wanted:
        if s not in STRATEGIES:
            raise InputError("BAD_VALUE", "strategies", "unknown strategy %r; choose from %s" % (s, ", ".join(STRATEGIES)))
    lots = norm_lots(data.get("lots"))
    set_terms(lots, as_of)
    usable = [l for l in lots if l["selectable"] and l["available"] > 0]
    not_selectable = [{"open_lot_id": l["open_lot_id"], "why": "still syncing (is_selectable=false); it cannot be "
                                                               "named in tax_lots yet"}
                      for l in lots if not l["selectable"]]
    total_avail = sum((l["available"] for l in usable), ZERO)
    results = {}
    for name in wanted:
        ordered = sorted(usable, key=_order_key(name, price, as_of))
        left = sell
        picks = []
        for lot in ordered:
            if left <= 0:
                break
            take = min(left, lot["available"])
            picks.append((lot, take))
            left -= take
        errors, warnings = [], []
        if left > 0:
            errors.append({"code": "INSUFFICIENT_AVAILABLE", "field": "sell_quantity",
                           "msg": "only %s shares are available to name across selectable lots; %s requested"
                                  % (qty(total_avail), qty(sell))})
        if len(picks) > MAX_LOTS:
            errors.append({"code": "TAX_LOTS_MAX_30", "field": "tax_lots",
                           "msg": "this choice needs %d lots; an order can name at most 30" % len(picks)})
        realized = short = long_ = ZERO
        pending = False
        detail = []
        for lot, take in picks:
            term = lot["term"]
            lt_on, amb = long_term_on(lot["acquired"])
            row = {"open_lot_id": lot["open_lot_id"], "quantity": qty(take), "acquired": lot["acquired"].isoformat(),
                   "term": term, "cost_per_share": money(lot["cost"]), "realized_usd": None}
            if lot["broker_term"]:
                row.update({"broker_term": lot["broker_term"], "date_term": lot["date_term"],
                            "term_agrees": lot["broker_term"] == lot["date_term"]})
                if not row["term_agrees"]:
                    warnings.append({
                        "code": "TERM_DISAGREES", "field": "lots",
                        "msg": "lot %s: Robinhood reports %s-term but the date arithmetic says %s-term on %s; the "
                               "%s-term figure is used, and Robinhood's tax documents govern (a wash-sale "
                               "replacement, inherited or gifted lot can carry an earlier holding period)"
                               % (lot["open_lot_id"], lot["broker_term"], lot["date_term"], as_of.isoformat(),
                                  lot["broker_term"])})
            if term == "short" and lot["date_term"] == "short":
                row["long_term_on"] = lt_on.isoformat()
                row["days_until_long_term"] = (lt_on - as_of).days
            if amb:
                row["leap_day_ambiguous"] = True
            if lot["cost"] is None:
                pending = True
            else:
                r = (price - lot["cost"]) * take
                row["realized_usd"] = money(r)
                realized += r
                if term == "long":
                    long_ += r
                else:
                    short += r
            detail.append(row)
        if pending:
            warnings.append({"code": "BASIS_PENDING", "field": "lots",
                             "msg": "a chosen lot has no cost basis yet; its gain or loss is unknown (never zero)"})
        results[name] = {
            "tax_lots": [{"open_lot_id": lot["open_lot_id"], "quantity": qty(take)} for lot, take in picks],
            "lots_detail": detail,
            "realized_usd": None if pending else money(realized),
            "realized_short_usd": None if pending else money(short),
            "realized_long_usd": None if pending else money(long_),
            "lot_count": len(picks),
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "order": ORDER_DISCLOSURE[name],
        }
        if name == "fifo":
            results[name]["label"] = FIFO_LABEL
    return {
        "ok": True,
        "symbol": symbol,
        "sell_quantity": qty(sell),
        "price": money(price),
        "as_of": as_of.isoformat(),
        "fifo_label": FIFO_LABEL,
        "strategy_order": list(wanted),
        "strategies": results,
        "not_selectable": not_selectable,
        "notes": ["estimates at %s per share before fees; the fill price decides the real figures" % money(price),
                  "these are side-by-side choices, not a recommendation: which is better depends on your other "
                  "gains, losses and tax bracket, which this kit cannot see",
                  "term is judged on %s (the sale's trade date); Robinhood's reported term wins when it differs "
                  "from the date arithmetic" % as_of.isoformat()],
    }


def op_validate(data):
    sell = dec(data.get("sell_quantity"), "sell_quantity", positive=True)
    arr = data.get("tax_lots")
    if not isinstance(arr, list) or not arr:
        raise InputError("MISSING_FIELD", "tax_lots", "tax_lots is a non-empty list of {open_lot_id, quantity}")
    lots = {l["open_lot_id"]: l for l in norm_lots(data.get("lots"), need_dates=False)}
    order = data.get("order") or {}
    if not isinstance(order, dict):
        raise InputError("BAD_VALUE", "order", "order is {side, type, market_hours, dollar_amount?}")
    errors = []

    def add(code, field, msg):
        errors.append({"code": code, "field": field, "msg": msg})

    side = str(order.get("side", "sell")).strip().lower()
    if side != "sell":
        add("TAX_LOTS_SELL_ONLY", "order.side", "tax_lots is for sell orders only")
    if len(arr) > MAX_LOTS:
        add("TAX_LOTS_MAX_30", "tax_lots", "at most 30 lots per order (got %d)" % len(arr))
    if order.get("dollar_amount") not in (None, ""):
        add("TAX_LOTS_NOT_ALLOWED_WITH", "order.dollar_amount", "tax_lots cannot be combined with dollar_amount")
    otype = str(order.get("type", "")).strip().lower()
    if otype in STOP_TYPES:
        add("TAX_LOTS_NOT_ALLOWED_WITH", "order.type", "tax_lots cannot be combined with stop orders")
    if str(order.get("market_hours", "")).strip().lower() == "all_day_hours":
        add("TAX_LOTS_NOT_ALLOWED_WITH", "order.market_hours", "tax_lots cannot be used in all_day_hours (the 24 Hour "
                                                               "Market); use extended_hours or regular_hours")
    if otype == "limit" and sell != sell.to_integral_value():
        add("TAX_LOTS_NOT_ALLOWED_WITH", "sell_quantity", "tax_lots cannot be used on a fractional limit order")
    total = ZERO
    seen = set()
    normalized = []
    for i, item in enumerate(arr):
        where = "tax_lots[%d]" % i
        if not isinstance(item, dict):
            add("BAD_VALUE", where, "each entry is {open_lot_id, quantity}")
            continue
        extra = sorted(k for k in item if k not in ("open_lot_id", "quantity"))
        if extra:
            add("BAD_VALUE", where, "only open_lot_id and quantity are allowed (remove %s)" % ", ".join(extra))
        lot_id = str(item.get("open_lot_id") or "").strip()
        try:
            q = dec(item.get("quantity"), where + ".quantity", positive=True, strict_string=True)
        except InputError as exc:
            add("BAD_QUANTITY", exc.field, exc.msg)
            continue
        if not lot_id:
            add("MISSING_FIELD", where + ".open_lot_id", "open_lot_id is required")
            continue
        if lot_id in seen:
            add("DUPLICATE_LOT", where + ".open_lot_id", "lot %s is named twice; combine the quantities" % lot_id)
        seen.add(lot_id)
        total += q
        normalized.append({"open_lot_id": lot_id, "quantity": item.get("quantity")})
        lot = lots.get(lot_id)
        if lot is None:
            add("UNKNOWN_LOT", where + ".open_lot_id", "lot %s is not in get_equity_tax_lots for this account and "
                                                       "symbol" % lot_id)
            continue
        if not lot["selectable"]:
            add("LOT_NOT_SELECTABLE", where + ".open_lot_id", "lot %s is still syncing (is_selectable=false)" % lot_id)
        if q > lot["available"]:
            add("TAX_LOTS_EXCEEDS_AVAILABLE", where + ".quantity",
                "lot %s has %s available (other orders may hold the rest); %s requested"
                % (lot_id, qty(lot["available"]), qty(q)))
    if total != sell:
        add("TAX_LOTS_SUM_MISMATCH", "tax_lots", "lot quantities sum to %s but the order sells %s (must be exact)"
            % (qty(total), qty(sell)))
    return {"ok": True, "valid": not errors, "errors": errors, "tax_lots": normalized, "sum": qty(total),
            "sell_quantity": qty(sell), "lot_count": len(arr)}


OPS = {"compare": op_compare, "validate": op_validate}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected compare or validate" % op)
    if not isinstance(data, dict):
        return _err("BAD_INPUT", "", "input must be a JSON object")
    try:
        return OPS[op](data)
    except InputError as exc:
        return _err(exc.code, exc.field, exc.msg)
    except (TypeError, AttributeError, ValueError, KeyError, IndexError, ArithmeticError) as exc:
        # A field of the wrong JSON type (a list where an object belongs, a number where a string belongs).
        return _err("BAD_INPUT", "", "input has an unexpected shape (%s: %s); compare it with --schema"
                    % (type(exc).__name__, exc))


# ---------------------------------------------------------------------------------------------
# JSON Schemas and self-test
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_M = {"type": ["string", "null"], "pattern": r"^-?\d+\.\d{2}$"}
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}"}
_ERRS = {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                    "properties": {"code": _S, "field": _S, "msg": _S}}}
_ERR = {"type": "object", "required": ["ok", "errors"], "properties": {"ok": {"enum": [False]}, "errors": _ERRS}}
_TL = {"type": "array", "items": {"type": "object", "required": ["open_lot_id", "quantity"],
                                  "properties": {"open_lot_id": _S, "quantity": _S}}}
_LOTS = {"type": "array", "items": {"type": "object", "required": ["open_lot_id"], "properties": {
    "open_lot_id": _S, "acquired": _DATE, "open_date": _DATE, "quantity": _S, "quantity_available": _S,
    "cost_per_share": _SN, "is_selectable": {"type": "boolean"}, "term": _S}}}
_STRAT = {"type": "object", "required": ["tax_lots", "realized_usd", "realized_short_usd", "realized_long_usd",
                                         "lot_count", "valid", "errors", "order"],
          "properties": {"tax_lots": _TL, "lots_detail": {"type": "array"}, "realized_usd": _M,
                         "realized_short_usd": _M, "realized_long_usd": _M, "lot_count": {"type": "integer"},
                         "valid": {"type": "boolean"}, "errors": _ERRS, "warnings": {"type": "array"},
                         "order": _S, "label": _S}}
SCHEMAS = {
    "compare": {
        "input": {"type": "object", "required": ["sell_quantity", "price", "lots"], "additionalProperties": False,
                  "properties": {"symbol": _S, "sell_quantity": _S, "price": _S, "as_of": _DATE, "lots": _LOTS,
                                 "strategies": {"type": "array", "items": {"enum": list(STRATEGIES)}}}},
        "output": {"anyOf": [{"type": "object", "required": ["ok", "fifo_label", "strategies", "strategy_order"],
                              "properties": {"ok": {"enum": [True]}, "fifo_label": {"enum": [FIFO_LABEL]},
                                             "strategies": {"type": "object", "additionalProperties": _STRAT},
                                             "strategy_order": {"type": "array"}, "not_selectable": {"type": "array"},
                                             "notes": {"type": "array"}}}, _ERR]},
    },
    "validate": {
        "input": {"type": "object", "required": ["sell_quantity", "tax_lots", "lots"], "additionalProperties": False,
                  "properties": {"sell_quantity": _S, "tax_lots": {"type": "array"}, "lots": _LOTS,
                                 "order": {"type": "object", "properties": {
                                     "side": _S, "type": _S, "market_hours": _S, "dollar_amount": _SN,
                                     "time_in_force": _S}}}},
        "output": {"anyOf": [{"type": "object", "required": ["ok", "valid", "errors", "tax_lots", "sum"],
                              "properties": {"ok": {"enum": [True]}, "valid": {"type": "boolean"}, "errors": _ERRS,
                                             "tax_lots": _TL, "sum": _S, "lot_count": {"type": "integer"}}}, _ERR]},
    },
}


def _type_ok(value, t):
    return {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }.get(t, lambda v: False)(value)


def schema_errors(value, schema, path="$"):
    """Minimal JSON Schema check used by --selftest."""
    if "anyOf" in schema:
        if any(not schema_errors(value, s, path) for s in schema["anyOf"]):
            return []
        return ["%s: matches no anyOf branch" % path]
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(value, x) for x in types):
            return ["%s: expected %s" % (path, "/".join(types))]
    errs = []
    if "enum" in schema and not any(value == e and type(value) is type(e) for e in schema["enum"]):
        errs.append("%s: %r not in enum" % (path, value))
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        errs.append("%s: does not match pattern" % path)
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errs.append("%s: missing %s" % (path, key))
        props, extra = schema.get("properties", {}), schema.get("additionalProperties", True)
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
        ops[name] = {k: dict(v, **{"$schema": "https://json-schema.org/draft/2020-12/schema"}) for k, v in pair.items()}
    return {"script": SCRIPT, "version": VERSION, "ops": ops}


_TSLA_LOTS = [{"open_lot_id": "L7", "acquired": "2026-06-02", "quantity_available": "20", "cost_per_share": "340.00"},
              {"open_lot_id": "L1", "acquired": "2025-03-10", "quantity_available": "20", "cost_per_share": "280.00"}]

EXAMPLES = [
    ("compare", {"symbol": "TSLA", "sell_quantity": "20", "price": "262.00", "as_of": "2026-11-16",
                 "lots": _TSLA_LOTS},
     {"ok": True, "fifo_label": "Robinhood's default", "strategies": {
         "fifo": {"tax_lots": [{"open_lot_id": "L1", "quantity": "20"}], "realized_usd": "-360.00",
                  "realized_long_usd": "-360.00", "realized_short_usd": "0.00", "valid": True,
                  "label": "Robinhood's default"},
         "highest_cost": {"tax_lots": [{"open_lot_id": "L7", "quantity": "20"}], "realized_usd": "-1560.00",
                          "realized_short_usd": "-1560.00"},
         "losses_first": {"tax_lots": [{"open_lot_id": "L7", "quantity": "20"}]}}}),
    ("validate", {"sell_quantity": "25", "tax_lots": [{"open_lot_id": "L7", "quantity": "20"},
                                                      {"open_lot_id": "L1", "quantity": "5"}],
                  "lots": _TSLA_LOTS, "order": {"side": "sell", "type": "limit", "market_hours": "extended_hours"}},
     {"ok": True, "valid": True, "errors": [], "sum": "25"}),
    ("validate", {"sell_quantity": "0.3", "tax_lots": [{"open_lot_id": "L7", "quantity": "0.1"},
                                                       {"open_lot_id": "L1", "quantity": "0.2"}],
                  "lots": _TSLA_LOTS, "order": {"side": "sell", "type": "market", "market_hours": "regular_hours"}},
     {"ok": True, "valid": True, "sum": "0.3"}),
    ("validate", {"sell_quantity": "10", "tax_lots": [{"open_lot_id": "L7", "quantity": "9"}], "lots": _TSLA_LOTS,
                  "order": {"side": "sell", "type": "stop_market"}},
     {"ok": True, "valid": False, "errors": [{"code": "TAX_LOTS_NOT_ALLOWED_WITH"},
                                             {"code": "TAX_LOTS_SUM_MISMATCH"}]}),
]


def _subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) <= len(actual) and all(
            _subset(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def _has_key(obj, key):
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


def selftest():
    failures = []
    for i, (op, inp, expected) in enumerate(EXAMPLES):
        problems = schema_errors(inp, SCHEMAS[op]["input"])
        out = run(op, inp)
        problems += schema_errors(out, SCHEMAS[op]["output"])
        if not _subset(expected, out):
            problems.append("output mismatch: %s" % json.dumps(out, sort_keys=True))
        if _has_key(out, "best"):
            problems.append("output has a 'best' key; this script never ranks choices")
        if problems:
            failures.append({"case": i, "op": op, "problems": problems})
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: lot_select.py <compare|validate> < input.json")))
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
