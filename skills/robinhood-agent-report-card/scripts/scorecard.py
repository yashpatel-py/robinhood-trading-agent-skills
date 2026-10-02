#!/usr/bin/env python3
"""scorecard.py - the facts behind a weekly agent report: counts, realized P&L, slippage, flags.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: "how is my agent doing?" invites a verdict, and verdicts built on a week of trades
are noise. This script computes only what the connector's data supports, with the sample size next
to every rate and a definition for every number, so the report can state facts and leave the
judgment to the user:
  * order counts by state, for orders Robinhood tags as agent-placed (plus the ones this kit sent); the
    same order passed twice (agentic-filtered and all-sources reads) counts once, and an OCO's legs are
    folded into the OCO so it counts once (reconcile.py describes the folding)
  * realized P&L from Robinhood's own get_realized_pnl (all sources in the Agentic account)
  * per-trade results, but only for get_pnl_trade_history rows that match a filled agent SELL order
    (same symbol and quantity, fill time within the tolerance). Trade-history rows carry no asset
    class: option closes appear under the underlying ticker, crypto under the base asset (BTC), and
    some rows have no symbol. Unmatched rows are counted as "unclassified", never guessed.
  * slippage against the quote seen at review time (from this kit's audit log)
  * behavior flags: averaging down, a new buy within 24 h of a realized loss, turnover
  * an optional trade-matched SPY comparison: same dollars, same holding period, one pair per
    matched agent buy->sell slice, only when both fill times exist. It is reported with n and the
    fill timestamps, and it is not a performance claim. Account-level returns are never computed:
    deposits and withdrawals are not visible to the connector.

Fill time: get_equity_orders rows expose `last_transaction_at`, used as the fill time of a filled
order (the 2026-09-22 capture did not show per-execution fills). Pass `fills` to override.

Input  (op `run`):
  {"window": {"start","end"} | {"start_date","end_date"}, "agentic_account_last4": "X4F1",
   "orders": [broker order rows, same shape as reconcile.py broker_orders],
   "matched"?: [reconcile.py matched rows (order_id, fingerprint)],
   "fills"?: [{"order_id","avg_fill_price","filled_at","side"?,"quantity"}],
   "realized_pnl"?: <get_realized_pnl data object>, "realized_trades"?: [get_pnl_trade_history trades],
   "trade_history_span"?: "week", "reviews"?: [{"fingerprint","ts","quote_bid"|"bid","quote_ask"|"ask"}],
   "spy_bars"?: [{"t","close"}], "portfolio_value_usd"?: "…", "audit_summary"?: {...},
   "match_tolerance_s"?: 120}
Output: {"ok":true,"window","counts","counts_source_unknown","realized_usd","realized_source",
         "realized_na_buckets","closing_trades_n","per_trade","win_rate":{"value","n"},"avg_win_usd",
         "avg_loss_usd","profit_factor","slippage_bps_vs_review":{"avg","n"},"trade_matched_spy",
         "spy_request","flags":[...],"definitions","not_measured","notes"}

Usage:
    python3 scorecard.py run < input.json > output.json
    python3 scorecard.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0 whenever
JSON was printed, exit 1 only on a crash. Money is Decimal, rounded half-up to cents, as strings;
rates are numbers rounded to 1 decimal with full precision in *_raw fields.
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

VERSION = "2.0.0"
SCRIPT = "scorecard"

ASSETS = ("equity", "option", "crypto", "oco")
DEFAULT_TOLERANCE_S = 120
CENT = Decimal("0.01")
TENTH = Decimal("0.1")
HUNDREDTH = Decimal("0.01")
BPS = Decimal("10000")
OPTION_MULTIPLIER = Decimal("100")
LOSS_LOOKAHEAD = timedelta(hours=24)
BAR_GAP_LIMIT = timedelta(days=1)
REVIEW_GRACE = timedelta(seconds=120)
FILLED_STATES = ("filled",)
REJECTED_STATES = ("rejected",)
FAILED_STATES = ("failed",)
CANCELLED_STATES = ("cancelled", "canceled", "voided")
# options: a cancel was requested but not confirmed; the order is still working and can still fill
CANCEL_PENDING_STATES = ("pending_cancelled",)
PARTIAL_STATES = ("partially_filled",)
OPEN_STATES = ("new", "queued", "confirmed", "unconfirmed", "active")
MATCH_WINDOW_S = 120
FILL_ASSETS = ("equity", "oco")  # an OCO's legs are equity orders: its fill is a share fill
SPY_CAVEAT = "small sample; not a performance claim"
SPY_METHOD = "same dollars, same holding period"

DEFINITIONS = {
    "counts": "Orders created inside the window in the Agentic account that Robinhood tags as agent-placed "
              "(placed_agent agentic) plus orders this kit's audit log shows it sent, each order once (an OCO "
              "and its legs are one order). Cancelled includes voided. Cancel pending (options "
              "pending_cancelled) is still working and may fill: it is counted apart, never as cancelled. "
              "Open includes an OCO that is active.",
    "counts_source_unknown": "Crypto and OCO orders in the Agentic account that could not be attributed: those "
                             "order lists have no placed_agent filter.",
    "realized_usd": "Robinhood's realized P&L for the Agentic account over the window (get_realized_pnl), all "
                    "sources: it includes any order you placed there yourself. Null buckets are n/a, not $0.",
    "per_trade": "Rows from get_pnl_trade_history inside the window, attributed to the agent only when they match "
                 "a filled agent sell order (same symbol and quantity, fill time within the tolerance). Rows "
                 "with no match are unclassified (option closes under the underlying ticker, crypto, "
                 "prediction markets, partial fills) and are left out of the per-trade figures.",
    "win_rate": "Share of agent-attributed realized rows with a gain above $0, with n = rows counted.",
    "avg_win_usd": "Mean realized gain of agent-attributed rows above $0.",
    "avg_loss_usd": "Mean realized result of agent-attributed rows below $0 (a negative number).",
    "profit_factor": "Sum of gains / absolute sum of losses over agent-attributed rows; null when there are no "
                     "losses.",
    "slippage_bps_vs_review": "Per filled agent order with a review in this kit's log: buy (fill - review ask) / "
                              "review ask x 10,000; sell (review bid - fill) / review bid x 10,000. Positive = "
                              "worse than the quote at review time. Equity and crypto only.",
    "trade_matched_spy": "For each agent buy->sell pair inside the window (FIFO by fill time, per symbol): agent "
                         "P&L = quantity x (exit fill - entry fill); SPY P&L = the same entry dollars x (SPY at "
                         "exit / SPY at entry - 1), using the hourly SPY bar that contains each fill (or the "
                         "latest earlier bar). Ignores fees, dividends, taxes and positions opened before the "
                         "window. " + SPY_CAVEAT + ".",
    "averaging_down": "Agent buys filled below the fill price of the agent's previous buy of the same symbol in "
                      "the window, with no agent sell of it in between.",
    "trade_within_24h_of_loss": "Agent buy orders created within 24 hours after an agent-attributed realized "
                                "loss (any symbol).",
    "turnover_pct": "Filled agent notional (buys + sells) in the window as a percentage of the Agentic account's "
                    "current total value (get_portfolio). Context only; not annualized.",
}
NOT_MEASURED = [
    "account return versus an index: deposits and withdrawals are not visible to the connector",
    "tax impact: use robinhood-tax-loss-harvesting",
    "fees: realized figures are Robinhood's; slippage and the SPY pairs use fill prices only",
    "per-trade results for options and crypto: trade-history rows carry no asset class",
]


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


def dec(value, field=None):
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


def soft_dec(value):
    try:
        return dec(value)
    except InputError:
        return None


def money(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def one_dec(d):
    return None if d is None else float(d.quantize(TENTH, rounding=ROUND_HALF_UP))


def two_dec(d):
    return None if d is None else float(d.quantize(HUNDREDTH, rounding=ROUND_HALF_UP))


def last4(value):
    s = str(value or "").strip()
    return s[-4:] if s else s


def _s(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _qty(d):
    if d is None:
        return None
    text = format(d, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _px(d):
    """A price exactly as given (29.50 stays 29.50)."""
    return None if d is None else format(d, "f")


# ---------------------------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------------------------
def norm_order(row, i):
    field = "orders[%d]" % i
    if not isinstance(row, dict):
        raise InputError("BAD_ORDER", field, "each order must be an object")
    asset = str(row.get("asset") or "").strip().lower()
    if asset not in ASSETS:
        raise InputError("BAD_ORDER", field + ".asset", "asset must be one of %s" % ", ".join(ASSETS))
    order_id = _s(row.get("order_id"))
    if not order_id:
        raise InputError("BAD_ORDER", field + ".order_id", "order_id is required")
    created = parse_ts(row.get("created_at"))
    if created is None:
        raise InputError("BAD_ORDER", field + ".created_at", "created_at must be an ISO 8601 time")
    pa = row.get("placed_agent")
    legs = row.get("leg_order_ids")
    if legs is None:
        legs = []
    if not isinstance(legs, list) or not all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in legs):
        raise InputError("BAD_ORDER", field + ".leg_order_ids", "leg_order_ids must be a list of order ids")
    o = {
        "account_last4": last4(row.get("account_last4")), "asset": asset, "order_id": order_id,
        "symbol": (_s(row.get("symbol")) or "").strip().upper() or None,
        "side": (_s(row.get("side")) or "").strip().lower() or None,
        "quantity": dec(row.get("quantity"), field + ".quantity"),
        "state": (_s(row.get("state")) or "").strip().lower() or None,
        "created_dt": created, "placed_agent": str(pa).strip().lower() if pa not in (None, "") else None,
        "average_price": dec(row.get("average_price"), field + ".average_price"),
        "cumulative_quantity": dec(row.get("cumulative_quantity"), field + ".cumulative_quantity"),
        "fill_dt": parse_ts(row.get("last_transaction_at")),
        "fingerprint": _s(row.get("fingerprint")),
        "multiplier": dec(row.get("multiplier"), field + ".multiplier"),
        "price": dec(row.get("price"), field + ".price"),
        "stop_price": dec(row.get("stop_price"), field + ".stop_price"),
        "leg_order_ids": [str(x) for x in legs if str(x)] if asset == "oco" else [],
        "legs_folded": [],
    }
    return o


def dedupe_orders(orders):
    """One row per (account, asset, order_id); the copy tagged agentic wins, else one with a source tag."""
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
    """Fold each OCO's legs (equity rows) into the OCO row, same rules as reconcile.py. Returns (orders, n)."""
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
        if legs:
            folded += len(legs)
            merge_oco(oco, legs)
    return rest, folded


def merge_oco(oco, legs):
    """The OCO takes what it lacks from its legs: source, symbol, side, quantity, and the fill and state of a
    leg that filled (the sibling leg's cancellation is not a separate agent cancellation)."""
    oco["legs_folded"] = sorted(leg["order_id"] for leg in legs)
    if oco["placed_agent"] is None:
        tags = [leg["placed_agent"] for leg in legs if leg["placed_agent"]]
        oco["placed_agent"] = "agentic" if "agentic" in tags else (tags[0] if tags else None)
    for key in ("symbol", "side", "quantity"):
        if oco[key] is None:
            oco[key] = next((leg[key] for leg in legs if leg[key] is not None), None)
    filled = [leg for leg in legs if leg["cumulative_quantity"] is not None and leg["cumulative_quantity"] > 0
              and leg["average_price"] is not None]
    if filled and not (oco["cumulative_quantity"] is not None and oco["cumulative_quantity"] > 0):
        qty = sum((leg["cumulative_quantity"] for leg in filled), Decimal(0))
        oco["cumulative_quantity"] = qty
        oco["average_price"] = sum((leg["cumulative_quantity"] * leg["average_price"] for leg in filled),
                                   Decimal(0)) / qty
        stamps = [leg["fill_dt"] for leg in filled if leg["fill_dt"] is not None]
        if stamps:
            oco["fill_dt"] = max(stamps)
        oco["state"] = "filled" if any(leg["state"] == "filled" for leg in filled) else "partially_filled"
    elif oco["state"] is None:
        oco["state"] = next((leg["state"] for leg in legs if leg["state"]), None)


def mult(o):
    if o["multiplier"] is not None:
        return o["multiplier"]
    return OPTION_MULTIPLIER if o["asset"] == "option" else Decimal(1)


def fill_of(o):
    """(quantity, price, fill_dt) for an order with something filled, else None."""
    qty = o["cumulative_quantity"]
    if qty is None and o["state"] in FILLED_STATES:
        qty = o["quantity"]
    if qty is None or qty <= 0 or o["average_price"] is None:
        return None
    return qty, o["average_price"], o["fill_dt"]


# ---------------------------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------------------------
def count_states(orders):
    c = {"orders": len(orders), "filled": 0, "partially_filled": 0, "rejected": 0, "failed": 0, "cancelled": 0,
         "cancel_pending": 0, "open": 0, "other": 0}
    for o in orders:
        st = o["state"]
        if st in FILLED_STATES:
            c["filled"] += 1
        elif st in PARTIAL_STATES:
            c["partially_filled"] += 1
        elif st in REJECTED_STATES:
            c["rejected"] += 1
        elif st in FAILED_STATES:
            c["failed"] += 1
        elif st in CANCELLED_STATES:
            c["cancelled"] += 1
        elif st in CANCEL_PENDING_STATES:
            c["cancel_pending"] += 1
        elif st in OPEN_STATES:
            c["open"] += 1
        else:
            c["other"] += 1
    return c


def realized_summary(rp, notes):
    out = {"realized_usd": None, "realized_source": None, "realized_na_buckets": 0, "closing_trades_n": None}
    if rp is None:
        notes.append("get_realized_pnl not provided: realized P&L not reported")
        return out
    if not isinstance(rp, dict):
        raise InputError("BAD_INPUT", "realized_pnl", "realized_pnl must be the get_realized_pnl data object")
    if isinstance(rp.get("data"), dict):
        rp = rp["data"]
    points = rp.get("data_points") if isinstance(rp.get("data_points"), list) else []
    bucket_sum, seen, na, trades, trades_seen = Decimal(0), False, 0, 0, False
    for p in points:
        if not isinstance(p, dict):
            continue
        g = soft_dec(p.get("realized_gain"))
        if g is None:
            na += 1
        else:
            bucket_sum += g
            seen = True
        n = p.get("number_of_trades")
        if isinstance(n, int) and not isinstance(n, bool):
            trades += n
            trades_seen = True
        elif isinstance(n, str) and n.strip().isdigit():
            trades += int(n)
            trades_seen = True
    total = rp.get("total_returns")
    if isinstance(total, dict):
        total = total.get("amount", total.get("value"))
    total_d = soft_dec(total)
    if total_d is not None:
        out["realized_usd"], out["realized_source"] = money(total_d), "total_returns"
        if seen and abs(total_d - bucket_sum) > CENT:
            notes.append("Robinhood's window total (%s) differs from the sum of its buckets (%s); the total is "
                         "shown" % (money(total_d), money(bucket_sum)))
    elif seen:
        out["realized_usd"], out["realized_source"] = money(bucket_sum), "bucket_sum"
    out["realized_na_buckets"] = na
    out["closing_trades_n"] = trades if trades_seen else None
    return out


def classify_rows(rows, agentic_orders, agent_ids, start, end, tol, notes):
    """Attribute get_pnl_trade_history rows. Returns (agent_rows, other_rows, unclassified, outside)."""
    sells = []
    for o in agentic_orders:
        f = fill_of(o)
        if o["asset"] in FILL_ASSETS and o["side"] == "sell" and f and f[2] is not None:
            sells.append((o, f))
    used = set()
    agent_rows, other_rows, unclassified = [], [], []
    outside = 0
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InputError("BAD_INPUT", "realized_trades[%d]" % i, "each trade row must be an object")
        ts = parse_ts(row.get("timestamp"))
        if ts is None:
            unclassified.append({"timestamp": _s(row.get("timestamp")), "symbol": _s(row.get("symbol")),
                                 "reason": "no readable timestamp"})
            continue
        if not (start <= ts < end):
            outside += 1
            continue
        symbol = (_s(row.get("symbol")) or "").strip().upper()
        side = (_s(row.get("side")) or "").strip().lower()
        qty = soft_dec(row.get("quantity"))
        gain = soft_dec(row.get("realized_gain"))
        base = {"timestamp": _s(row.get("timestamp")), "symbol": symbol or None, "side": side or None,
                "quantity": _qty(qty), "realized_gain": money(gain)}
        if not symbol or not side:
            unclassified.append(dict(base, reason="no symbol or side (prediction market, adjustment or similar)"))
            continue
        if side != "sell":
            unclassified.append(dict(base, reason="not a sell row: an option close or other realizing row, not a "
                                                  "share sale"))
            continue
        best = None
        for idx, (o, f) in enumerate(sells):
            if idx in used or o["symbol"] != symbol or qty is None or f[0] != qty:
                continue
            delta = abs((f[2] - ts).total_seconds())
            if delta <= tol and (best is None or delta < best[0]):
                best = (delta, idx, o)
        if best is None:
            unclassified.append(dict(base, reason="no filled equity sell with the same symbol, quantity and time "
                                                  "(could be an option close under the underlying, crypto, or a "
                                                  "partial fill)"))
            continue
        used.add(best[1])
        o = best[2]
        row_out = dict(base, order_id=o["order_id"])
        if gain is None:
            unclassified.append(dict(row_out, reason="realized gain missing on the row"))
        elif o["order_id"] in agent_ids:
            agent_rows.append((gain, ts, row_out))
        elif o["placed_agent"] is None:
            unclassified.append(dict(row_out, reason="matched order has no source tag"))
        else:
            other_rows.append(dict(row_out, placed_agent=o["placed_agent"]))
    if outside:
        notes.append("%d trade-history row(s) outside the window were left out (the span preset is wider than "
                     "the window)" % outside)
    return agent_rows, other_rows, unclassified, outside


def per_trade_stats(agent_rows):
    gains = [g for g, _, _ in agent_rows]
    n = len(gains)
    wins = [g for g in gains if g > 0]
    losses = [g for g in gains if g < 0]
    out = {}
    if n:
        rate = Decimal(len(wins)) * 100 / Decimal(n)
        out["win_rate"] = {"value": one_dec(rate), "n": n, "value_raw": str(rate)}
    else:
        out["win_rate"] = {"value": None, "n": 0, "value_raw": None}
    out["avg_win_usd"] = money(sum(wins, Decimal(0)) / len(wins)) if wins else None
    out["avg_loss_usd"] = money(sum(losses, Decimal(0)) / len(losses)) if losses else None
    if losses:
        pf = sum(wins, Decimal(0)) / abs(sum(losses, Decimal(0)))
        out["profit_factor"], out["profit_factor_raw"] = two_dec(pf), str(pf)
    else:
        out["profit_factor"], out["profit_factor_raw"] = None, None
    return out


def slippage(agent_orders, fp_by_order, reviews):
    by_fp = {}
    for i, r in enumerate(reviews):
        if not isinstance(r, dict):
            raise InputError("BAD_INPUT", "reviews[%d]" % i, "each review must be an object")
        fp = _s(r.get("fingerprint"))
        ts = parse_ts(r.get("ts"))
        if not fp or ts is None:
            continue
        bid = soft_dec(r.get("quote_bid", r.get("bid")))
        ask = soft_dec(r.get("quote_ask", r.get("ask")))
        by_fp.setdefault(fp, []).append((ts, bid, ask))
    rows = []
    for o in agent_orders:
        if o["asset"] not in ("equity", "crypto") or o["side"] not in ("buy", "sell"):
            continue
        f = fill_of(o)
        fp = fp_by_order.get(o["order_id"]) or o["fingerprint"]
        if not f or not fp or fp not in by_fp:
            continue
        cands = [r for r in by_fp[fp] if r[0] <= o["created_dt"] + REVIEW_GRACE]
        if not cands:
            continue
        ts, bid, ask = max(cands, key=lambda r: r[0])
        price = f[1]
        if o["side"] == "buy":
            if ask is None or ask <= 0:
                continue
            bps = (price - ask) / ask * BPS
            ref = ask
        else:
            if bid is None or bid <= 0:
                continue
            bps = (bid - price) / bid * BPS
            ref = bid
        rows.append({"order_id": o["order_id"], "symbol": o["symbol"], "side": o["side"], "fill": _px(price),
                     "review_quote": _px(ref), "review_ts": fmt_utc(ts), "bps": one_dec(bps), "bps_raw": str(bps)})
    if not rows:
        return {"avg": None, "n": 0, "avg_raw": None, "rows": []}
    avg = sum((Decimal(r["bps_raw"]) for r in rows), Decimal(0)) / len(rows)
    return {"avg": one_dec(avg), "n": len(rows), "avg_raw": str(avg), "rows": rows}


def spy_pairs(agent_orders, start, end):
    """FIFO buy->sell slices per symbol from agent equity fills inside the window."""
    fills = []
    for o in agent_orders:
        f = fill_of(o)
        if o["asset"] not in FILL_ASSETS or o["side"] not in ("buy", "sell") or not f or f[2] is None:
            continue
        if not (start <= f[2] < end):
            continue
        fills.append((f[2], o["order_id"], o, f))
    fills.sort(key=lambda t: (t[0], t[1]))
    queues, pairs, unpaired_sell_qty = {}, [], Decimal(0)
    for dt, _oid, o, (qty, price, _) in fills:
        q = queues.setdefault(o["symbol"], [])
        if o["side"] == "buy":
            q.append([qty, price, dt, o["order_id"]])
            continue
        remaining = qty
        while remaining > 0 and q:
            lot = q[0]
            take = min(lot[0], remaining)
            pairs.append({"symbol": o["symbol"], "quantity": take, "entry_price": lot[1], "entry_dt": lot[2],
                          "entry_order_id": lot[3], "exit_price": price, "exit_dt": dt, "exit_order_id": o["order_id"]})
            lot[0] -= take
            remaining -= take
            if lot[0] == 0:
                q.pop(0)
        if remaining > 0:
            unpaired_sell_qty += remaining
    return pairs, unpaired_sell_qty


def bar_close(bars, dt):
    best = None
    for t, close in bars:
        if t <= dt and (best is None or t > best[0]):
            best = (t, close)
    if best is None or dt - best[0] > BAR_GAP_LIMIT:
        return None
    return best


def trade_matched_spy(pairs, bars_in, notes):
    if not pairs:
        return None, None, "no agent buy->sell pair with both fill times inside the window"
    if not bars_in:
        first = min(p["entry_dt"] for p in pairs) - timedelta(days=1)
        last = max(p["exit_dt"] for p in pairs) + timedelta(hours=1)
        req = {"symbols": ["SPY"], "start_time": fmt_utc(first), "end_time": fmt_utc(last), "interval": "hour"}
        return None, req, "SPY bars not provided yet: fetch them with spy_request and run again"
    bars = []
    for i, b in enumerate(bars_in):
        if not isinstance(b, dict):
            raise InputError("BAD_INPUT", "spy_bars[%d]" % i, "each bar must be {t, close}")
        t = parse_ts(b.get("t"))
        c = soft_dec(b.get("close"))
        if t is not None and c is not None and c > 0:
            bars.append((t, c))
    out_pairs, agent_total, spy_total, skipped = [], Decimal(0), Decimal(0), 0
    for p in pairs:
        e = bar_close(bars, p["entry_dt"])
        x = bar_close(bars, p["exit_dt"])
        if e is None or x is None:
            skipped += 1
            continue
        entry_usd = p["quantity"] * p["entry_price"]
        agent = p["quantity"] * (p["exit_price"] - p["entry_price"])
        spy = entry_usd * (x[1] / e[1] - 1)
        agent_total += agent
        spy_total += spy
        out_pairs.append({"symbol": p["symbol"], "quantity": _qty(p["quantity"]),
                          "entry_order_id": p["entry_order_id"], "exit_order_id": p["exit_order_id"],
                          "entry_filled_at": fmt_utc(p["entry_dt"]), "exit_filled_at": fmt_utc(p["exit_dt"]),
                          "entry_filled_et": fmt_et(p["entry_dt"]), "exit_filled_et": fmt_et(p["exit_dt"]),
                          "entry_usd": money(entry_usd), "agent_pnl_usd": money(agent),
                          "spy_entry": _px(e[1]), "spy_exit": _px(x[1]),
                          "spy_bar_entry": fmt_utc(e[0]), "spy_bar_exit": fmt_utc(x[0]), "spy_pnl_usd": money(spy)})
    if skipped:
        notes.append("%d pair(s) left out of the SPY comparison: no SPY bar at or within a day before a fill"
                     % skipped)
    if not out_pairs:
        return None, None, "no pair had SPY bars around both fills"
    return {"agent_pnl_usd": money(agent_total), "spy_pnl_usd": money(spy_total), "n": len(out_pairs),
            "method": SPY_METHOD, "caveat": SPY_CAVEAT, "fill_time_source": "last_transaction_at of each filled "
            "order (or the fills you passed)", "pairs": out_pairs}, None, None


def behavior_flags(agent_orders, agent_rows, portfolio_value, notes):
    flags = []
    buys = []
    events = []
    for o in agent_orders:
        f = fill_of(o)
        if o["asset"] in FILL_ASSETS and f and o["side"] in ("buy", "sell"):
            events.append((f[2] or o["created_dt"], o["order_id"], o, f))
    events.sort(key=lambda t: (t[0], t[1]))
    last_buy = {}
    for _dt, _oid, o, f in events:
        if o["side"] == "sell":
            last_buy.pop(o["symbol"], None)
            continue
        prev = last_buy.get(o["symbol"])
        if prev is not None and f[1] < prev[1]:
            buys.append({"order_id": o["order_id"], "symbol": o["symbol"], "fill": _px(f[1]),
                         "previous_buy_order_id": prev[0], "previous_fill": _px(prev[1])})
        last_buy[o["symbol"]] = (o["order_id"], f[1])
    flags.append({"flag": "averaging_down", "count": len(buys), "orders": buys})

    loss_times = sorted(ts for g, ts, _ in agent_rows if g < 0)
    hits = []
    for o in sorted(agent_orders, key=lambda x: (x["created_dt"], x["order_id"])):
        if o["side"] != "buy":
            continue
        for lt in loss_times:
            if lt < o["created_dt"] <= lt + LOSS_LOOKAHEAD:
                hits.append({"order_id": o["order_id"], "symbol": o["symbol"], "created_et": fmt_et(o["created_dt"]),
                             "loss_at_et": fmt_et(lt)})
                break
    flags.append({"flag": "trade_within_24h_of_loss", "count": len(hits), "orders": hits})

    traded = Decimal(0)
    for o in agent_orders:
        f = fill_of(o)
        if f:
            traded += f[0] * f[1] * mult(o)
    value = soft_dec(portfolio_value)
    if value is not None and value > 0:
        pct = traded / value * 100
        flags.append({"flag": "turnover_pct", "value": one_dec(pct), "value_raw": str(pct),
                      "traded_usd": money(traded), "account_value_usd": money(value)})
    else:
        flags.append({"flag": "turnover_pct", "value": None, "value_raw": None, "traded_usd": money(traded),
                      "account_value_usd": None})
        notes.append("turnover not computed: pass portfolio_value_usd from get_portfolio")
    return flags


# ---------------------------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------------------------
def op_run(data):
    start, end = parse_window(data.get("window"))
    agentic = last4(data.get("agentic_account_last4"))
    if len(agentic) != 4:
        raise InputError("MISSING_FIELD", "agentic_account_last4", "give the Agentic account's last 4 characters")
    raw_orders = data.get("orders")
    if not isinstance(raw_orders, list):
        raise InputError("MISSING_FIELD", "orders", "orders must be a list (it may be empty)")
    orders = [norm_order(r, i) for i, r in enumerate(raw_orders)]
    tol = data.get("match_tolerance_s", DEFAULT_TOLERANCE_S)
    if not isinstance(tol, int) or isinstance(tol, bool) or not 0 <= tol <= 3600:
        raise InputError("BAD_INPUT", "match_tolerance_s", "match_tolerance_s must be an integer 0-3600")
    notes = []

    fp_by_order = {}
    matched = data.get("matched") or []
    if not isinstance(matched, list):
        raise InputError("BAD_INPUT", "matched", "matched must be reconcile.py's matched list")
    for m in matched:
        if isinstance(m, dict) and m.get("order_id"):
            fp_by_order[_s(m["order_id"])] = _s(m.get("fingerprint"))
    fills = data.get("fills") or []
    if not isinstance(fills, list):
        raise InputError("BAD_INPUT", "fills", "fills must be a list")
    by_id = {}
    for o in orders:
        by_id.setdefault(o["order_id"], []).append(o)
    for i, f in enumerate(fills):
        if not isinstance(f, dict) or _s(f.get("order_id")) not in by_id:
            raise InputError("BAD_INPUT", "fills[%d]" % i, "each fill needs the order_id of an order in orders")
        for o in by_id[_s(f["order_id"])]:
            o["average_price"] = dec(f.get("avg_fill_price"), "fills[%d].avg_fill_price" % i)
            o["cumulative_quantity"] = dec(f.get("quantity"), "fills[%d].quantity" % i)
            o["fill_dt"] = parse_ts(f.get("filled_at"))
    orders, legs_folded = fold_oco_legs(dedupe_orders(orders))
    if legs_folded:
        notes.append("%d OCO leg order(s) counted once, as part of their OCO" % legs_folded)

    in_win = [o for o in orders if o["account_last4"] == agentic and start <= o["created_dt"] < end]
    agent_ids = {o["order_id"] for o in in_win if o["placed_agent"] == "agentic" or o["order_id"] in fp_by_order}
    agent_orders = [o for o in in_win if o["order_id"] in agent_ids]
    unknown_src = [o for o in in_win if o["order_id"] not in agent_ids and o["placed_agent"] is None]
    by_asset = {}
    for o in unknown_src:
        by_asset[o["asset"]] = by_asset.get(o["asset"], 0) + 1

    rs = realized_summary(data.get("realized_pnl"), notes)
    rows = data.get("realized_trades") or []
    if isinstance(rows, dict):
        rows = rows.get("trades") or (rows.get("data") or {}).get("trades") or []
    if not isinstance(rows, list):
        raise InputError("BAD_INPUT", "realized_trades", "realized_trades must be the trades list")
    # a sell may fill after the window's orders were created; any agentic-account order can be the match
    agentic_all = [o for o in orders if o["account_last4"] == agentic]
    agent_all_ids = agent_ids | {o["order_id"] for o in agentic_all if o["placed_agent"] == "agentic"}
    agent_rows, other_rows, unclassified, _outside = classify_rows(rows, agentic_all, agent_all_ids, start, end,
                                                                   tol, notes)
    stats = per_trade_stats(agent_rows)

    reviews = data.get("reviews") or []
    if not isinstance(reviews, list):
        raise InputError("BAD_INPUT", "reviews", "reviews must be a list")
    slip = slippage(agent_orders, fp_by_order, reviews)

    pairs, unpaired = spy_pairs(agent_orders, start, end)
    bars = data.get("spy_bars")
    if bars is not None and not isinstance(bars, list):
        raise InputError("BAD_INPUT", "spy_bars", "spy_bars must be a list of {t, close}")
    spy, spy_request, spy_note = trade_matched_spy(pairs, bars, notes)
    if unpaired > 0:
        notes.append("%s share(s) sold by the agent had no agent buy inside the window, so they are not in the SPY "
                     "pairs" % _qty(unpaired))

    flags = behavior_flags(agent_orders, agent_rows, data.get("portfolio_value_usd"), notes)
    out = {
        "ok": True,
        "window": {"start": fmt_utc(start), "end": fmt_utc(end),
                   "label_et": "%s → %s ET" % (fmt_et_date(start), fmt_et_date(end - timedelta(seconds=1)))},
        "trade_history_span": _s(data.get("trade_history_span")),
        "counts": count_states(agent_orders),
        "counts_source_unknown": {"orders": len(unknown_src), "by_asset": by_asset},
        "per_trade": {"agent_rows": len(agent_rows), "other_source_rows": len(other_rows),
                      "unclassified_rows": len(unclassified), "agent": [r for _, _, r in agent_rows],
                      "other_source": other_rows, "unclassified": unclassified},
        "slippage_bps_vs_review": slip,
        "trade_matched_spy": spy,
        "trade_matched_spy_note": spy_note,
        "spy_request": spy_request,
        "flags": flags,
        "definitions": DEFINITIONS,
        "not_measured": NOT_MEASURED,
        "notes": notes,
    }
    out.update(rs)
    out.update(stats)
    if "audit_summary" in data:
        out["audit_summary"] = data["audit_summary"]
    return out


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
_NN = {"type": ["number", "null"]}
_NUMS = {"type": ["string", "integer", "null"]}


def _obj(props, required=(), extra=False):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": extra}


_ERR = _obj({"ok": {"enum": [False]}, "errors": {"type": "array", "items": _obj(
    {"code": _S, "field": _S, "msg": _S}, ["code", "field", "msg"])}}, ["ok", "errors"])
_WINDOW_IN = {"anyOf": [_obj({"start": _S, "end": _S}, ["start", "end"]),
                        _obj({"start_date": _S, "end_date": _S}, ["start_date", "end_date"])]}
_SPY = {"anyOf": [{"type": "null"}, _obj({
    "agent_pnl_usd": _S, "spy_pnl_usd": _S, "n": _I, "method": {"enum": [SPY_METHOD]},
    "caveat": {"enum": [SPY_CAVEAT]}, "fill_time_source": _S,
    "pairs": {"type": "array", "items": _obj({
        "symbol": _S, "quantity": _S, "entry_order_id": _S, "exit_order_id": _S, "entry_filled_at": _S,
        "exit_filled_at": _S, "entry_filled_et": _S, "exit_filled_et": _S, "entry_usd": _S, "agent_pnl_usd": _S,
        "spy_entry": _S, "spy_exit": _S, "spy_bar_entry": _S, "spy_bar_exit": _S, "spy_pnl_usd": _S},
        ["entry_filled_at", "exit_filled_at", "agent_pnl_usd", "spy_pnl_usd"])}},
    ["agent_pnl_usd", "spy_pnl_usd", "n", "method", "caveat", "pairs"])]}
SCHEMAS = {
    "run": {
        "input": _obj({
            "window": _WINDOW_IN, "agentic_account_last4": _S, "orders": {"type": "array"},
            "matched": {"type": "array"}, "fills": {"type": "array", "items": _obj({
                "order_id": _S, "avg_fill_price": _NUMS, "filled_at": _S, "side": _SN, "quantity": _NUMS},
                ["order_id", "avg_fill_price", "filled_at", "quantity"])},
            "realized_pnl": {"type": "object"}, "realized_trades": {"type": ["array", "object"]},
            "trade_history_span": {"enum": ["week", "month", "3month", "ytd", "all"]},
            "reviews": {"type": "array"}, "spy_bars": {"type": "array", "items": _obj({"t": _S, "close": _NUMS},
                                                                                     ["t", "close"], extra=True)},
            "portfolio_value_usd": _NUMS, "audit_summary": {"type": "object"}, "match_tolerance_s": _I,
        }, ["window", "agentic_account_last4", "orders"]),
        "output": {"anyOf": [_obj({
            "ok": {"enum": [True]},
            "window": _obj({"start": _S, "end": _S, "label_et": _S}, ["start", "end", "label_et"]),
            "trade_history_span": _SN,
            "counts": _obj({k: _I for k in ("orders", "filled", "partially_filled", "rejected", "failed",
                                            "cancelled", "cancel_pending", "open", "other")},
                           ["orders", "filled", "rejected", "cancelled"]),
            "counts_source_unknown": _obj({"orders": _I, "by_asset": {"type": "object", "additionalProperties": _I}},
                                          ["orders", "by_asset"]),
            "realized_usd": _SN, "realized_source": {"enum": ["total_returns", "bucket_sum", None]},
            "realized_na_buckets": _I, "closing_trades_n": _IN,
            "per_trade": _obj({"agent_rows": _I, "other_source_rows": _I, "unclassified_rows": _I,
                               "agent": {"type": "array"}, "other_source": {"type": "array"},
                               "unclassified": {"type": "array"}},
                              ["agent_rows", "unclassified_rows", "unclassified"]),
            "win_rate": _obj({"value": _NN, "n": _I, "value_raw": _SN}, ["value", "n"]),
            "avg_win_usd": _SN, "avg_loss_usd": _SN, "profit_factor": _NN, "profit_factor_raw": _SN,
            "slippage_bps_vs_review": _obj({"avg": _NN, "n": _I, "avg_raw": _SN, "rows": {"type": "array"}},
                                           ["avg", "n"]),
            "trade_matched_spy": _SPY, "trade_matched_spy_note": _SN,
            "spy_request": {"anyOf": [{"type": "null"}, _obj({
                "symbols": {"type": "array", "items": _S}, "start_time": _S, "end_time": _S,
                "interval": {"enum": ["hour"]}}, ["symbols", "start_time", "end_time", "interval"])]},
            "flags": {"type": "array", "items": _obj({"flag": {"enum": ["averaging_down", "trade_within_24h_of_loss",
                                                                        "turnover_pct"]}},
                                                     ["flag"], extra=True)},
            "definitions": {"type": "object", "additionalProperties": _S},
            "not_measured": {"type": "array", "items": _S}, "notes": {"type": "array", "items": _S},
            "audit_summary": {"type": "object"},
        }, ["ok", "window", "counts", "realized_usd", "win_rate", "avg_win_usd", "avg_loss_usd", "profit_factor",
            "slippage_bps_vs_review", "trade_matched_spy", "flags", "definitions"]), _ERR]},
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
# Self-test (tests/golden/scorecard/ covers much more)
# ---------------------------------------------------------------------------------------------
def _o(oid, sym, side, qty, avg, created, filled, state="filled", pa="agentic"):
    return {"account_last4": "X4F1", "asset": "equity", "order_id": oid, "symbol": sym, "side": side,
            "quantity": qty, "state": state, "created_at": created, "placed_agent": pa, "average_price": avg,
            "cumulative_quantity": qty if state == "filled" else "0", "last_transaction_at": filled}


_BASE = {
    "window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
    "orders": [
        _o("e1", "PLTR", "buy", "10", "29.48", "2026-11-09T15:05:12Z", "2026-11-09T15:05:14Z"),
        _o("e4", "PLTR", "sell", "10", "30.62", "2026-11-10T19:20:05Z", "2026-11-10T19:20:07Z"),
    ],
    "matched": [{"order_id": "e1", "fingerprint": "sha256:aa"}],
    "reviews": [{"fingerprint": "sha256:aa", "ts": "2026-11-09T15:04:30Z", "bid": "29.45", "ask": "29.47"}],
    "realized_pnl": {"data_points": [{"realized_gain": "26.20", "number_of_trades": 1},
                                     {"realized_gain": None, "number_of_trades": 0}]},
    "realized_trades": [
        {"timestamp": "2026-11-10T19:20:07Z", "symbol": "PLTR", "side": "sell", "quantity": "10", "price": "30.62",
         "realized_gain": "26.20"},
        {"timestamp": "2026-11-11T15:00:00Z", "symbol": "", "side": "", "quantity": "1", "price": "0.55",
         "realized_gain": "-0.45"}],
    "portfolio_value_usd": "3000.00",
}
EXAMPLES = [
    (_BASE, {"ok": True, "counts": {"orders": 2, "filled": 2}, "realized_usd": "26.20",
             "realized_source": "bucket_sum", "realized_na_buckets": 1,
             "per_trade": {"agent_rows": 1, "unclassified_rows": 1},
             "win_rate": {"value": 100.0, "n": 1}, "avg_win_usd": "26.20", "avg_loss_usd": None,
             "profit_factor": None, "slippage_bps_vs_review": {"avg": 3.4, "n": 1},
             "trade_matched_spy": None,
             "spy_request": {"symbols": ["SPY"], "start_time": "2026-11-08T15:05:14Z",
                             "end_time": "2026-11-10T20:20:07Z", "interval": "hour"},
             "flags": [{"flag": "averaging_down", "count": 0}, {"flag": "trade_within_24h_of_loss", "count": 0},
                       {"flag": "turnover_pct", "value": 20.0}]}),
    (dict(_BASE, spy_bars=[{"t": "2026-11-09T14:30:00Z", "close": "670.00"},
                           {"t": "2026-11-10T19:30:00Z", "close": "673.35"},
                           {"t": "2026-11-10T18:30:00Z", "close": "671.00"}]),
     {"ok": True, "spy_request": None,
      "trade_matched_spy": {"agent_pnl_usd": "11.40", "spy_pnl_usd": "0.44", "n": 1, "caveat": SPY_CAVEAT,
                            "pairs": [{"entry_filled_at": "2026-11-09T15:05:14Z",
                                       "exit_filled_at": "2026-11-10T19:20:07Z", "spy_entry": "670.00",
                                       "spy_exit": "671.00"}]}}),
    ({"window": {"start": "2026-11-09", "end": "2026-11-16"}, "agentic_account_last4": "X4F1", "orders": []},
     {"ok": False, "errors": [{"code": "BAD_WINDOW"}]}),
    # a pending cancel is still working (it can fill): never counted as cancelled
    ({"window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
      "orders": [{"account_last4": "X4F1", "asset": "option", "order_id": "op1", "symbol": "KO", "side": "sell",
                  "quantity": "1", "state": "pending_cancelled", "created_at": "2026-11-12T15:00:00Z",
                  "placed_agent": "agentic", "price": "1.10"}]},
     {"ok": True, "counts": {"orders": 1, "cancelled": 0, "cancel_pending": 1, "open": 0}}),
    # an OCO, its two legs (one filled, one cancelled) and the same agent buy passed twice: each counts once
    ({"window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
      "orders": [
          _o("e1", "PLTR", "buy", "10", "29.48", "2026-11-09T15:05:12Z", "2026-11-09T15:05:14Z"),
          _o("e1", "PLTR", "buy", "10", "29.48", "2026-11-09T15:05:12Z", "2026-11-09T15:05:14Z", pa="not_agentic"),
          {"account_last4": "X4F1", "asset": "oco", "order_id": "a1", "symbol": "PLTR", "side": "sell",
           "quantity": "10", "state": "filled", "created_at": "2026-11-09T16:00:00Z", "price": "31.00",
           "stop_price": "27.00", "leg_order_ids": ["l1", "l2"]},
          _o("l1", "PLTR", "sell", "10", "31.00", "2026-11-09T16:00:00Z", "2026-11-10T15:00:03Z"),
          _o("l2", "PLTR", "sell", "10", None, "2026-11-09T16:00:00Z", "2026-11-10T15:00:03Z", state="cancelled")],
      "realized_trades": [{"timestamp": "2026-11-10T15:00:03Z", "symbol": "PLTR", "side": "sell", "quantity": "10",
                           "price": "31.00", "realized_gain": "15.20"}],
      "portfolio_value_usd": "3000.00"},
     {"ok": True, "counts": {"orders": 2, "filled": 2, "cancelled": 0},
      "per_trade": {"agent_rows": 1, "unclassified_rows": 0}, "win_rate": {"value": 100.0, "n": 1},
      "flags": [{"flag": "averaging_down", "count": 0}, {"flag": "trade_within_24h_of_loss", "count": 0},
                {"flag": "turnover_pct", "traded_usd": "604.80"}]}),
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
            problems.append("output mismatch: %s" % json.dumps(out, sort_keys=True)[:900])
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: scorecard.py run < input.json")))
        return 0
    raw_in = sys.stdin.read()
    try:
        data = json.loads(raw_in) if raw_in.strip() else {}
    except ValueError as exc:
        print(json.dumps(_err("BAD_JSON", "", "stdin is not valid JSON: %s" % exc)))
        return 0
    print(json.dumps(run(argv[0], data), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
