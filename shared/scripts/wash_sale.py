#!/usr/bin/env python3
"""wash_sale.py - the 61-day wash-sale window across every Robinhood account the user let us read.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: Robinhood reports wash sales per account, and each account gets its own 1099. A loss
sold in the Agentic account is still washed by a buy of the same stock in the user's Individual
account, IRA or Roth, by a recurring buy or by a dividend reinvestment, inside 30 calendar days before
or after the sale. An IRA or Roth buy is worse: the loss is gone for good (Rev. Rul. 2008-5). No 1099
flags any of this, and the agent is the only party that can read every account at once. This script
does the date and dollar arithmetic so the agent never does it in its head:

  window        the 61-day window for a trade date, the UTC `created_at_gte` bound for the order sweep
                (looked back by the GTC lifetime, because an order created before the window can fill
                inside it), the trade-history span to use, and the first clean buy date
  classify_pnl  cross-checks get_pnl_trade_history rows against filled get_equity_orders sells. The
                trade history has no asset class: option closes appear under the underlying ticker and
                crypto under its base code, so a row counts as a share sale only when a filled equity
                sell matches it. Unmatched loss rows are "unclassified" and make that symbol unknown.
  run           the wash-sale result for a loss sale (mode "sale") or a buy (mode "planned_buy"):
                conflicts, partial washes, permanent (IRA) disallowances, possible items, unknowns,
                earliest clean dates, and a status that is never "clear" unless every account was read.
                A planned buy also checks a harvest the user declared (planned_sales), open taxable lots
                below cost that a later loss sale would put in its window, and a planned option purchase
                (planned_buy.instrument "option": a call bought or a put sold is an option to acquire)

Rules (IRS Pub 550, "Wash Sales"; rules as of 2026-09-22):
  * The window is [D - 30, D + 30] in calendar days, both ends included (61 days). Weekends and market
    holidays do not stretch it. D is the TRADE date, never the settlement date.
  * Replacement buys: same ticker, any account (retirement included), filled inside the window, not the
    purchase of the shares being sold. When the sold shares' acquisition date is unknown, a buy in the
    same account up to the sale date may be that purchase, so it is possible, never a definite conflict.
  * Losses are taken in the order they were disposed of (Treas. Reg. 1.1091-1(b)); each is washed by the
    earliest replacement shares in its window (c)-(d), and a purchase that washed one loss cannot wash
    another (e). So a loss sold up to 60 days earlier has first claim on a shared buy.
  * Loss lots sold together count as disposed of in the order they were acquired, earliest first (b), and
    the replacement shares are matched against them in that order (c). Only when a lot's acquisition date
    is missing is the order unknown: then the largest loss per share goes first (the most that can be
    disallowed) and the smallest-first figure is reported as disallowed_low_usd.
  * A replacement bought in an IRA or Roth makes the matched loss permanently disallowed.
  * Crypto held directly is not "stock or securities" under current law: CRYPTO_NOT_SUBJECT.
This is rule arithmetic on the user's data, not tax advice and not a "substantially identical" ruling.

Usage:
    python3 wash_sale.py <op> < input.json > output.json      (ops: window, classify_pnl, run)
    python3 wash_sale.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0 whenever
JSON was printed, exit 1 only on a crash. Account numbers appear only as their last 4 characters.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

VERSION = "2.0.0"
SCRIPT = "wash_sale"
RULES_AS_OF = "2026-09-22"
CENT = Decimal("0.01")
ZERO = Decimal(0)
WINDOW_DAYS = 30
DEFAULT_GTC_LOOKBACK_DAYS = 90  # VERIFY-D1: Robinhood's equity GTC lifetime; 90 days until verified
RECURRING_LOOKBACK_DAYS = 100
DRIP_LOOKBACK_DAYS = 400
DEFAULT_TOLERANCE_SECONDS = 120
DEFAULT_PRICE_TOLERANCE_PCT = Decimal("2")
OVERNIGHT_START_HOUR = 20  # 24 Hour Market fills from 20:00 ET may carry the next trading day's trade date

# Codes that are both crypto assets and something else (BTC and ETH are also ETF tickers). A bare code
# from this list without asset_class is refused instead of guessed.
CRYPTO_CODES = frozenset(
    "BTC ETH SOL DOGE XRP ADA AVAX LTC BCH LINK SHIB UNI XLM ETC DOT AAVE COMP PEPE BONK USDC XTZ ARB OP "
    "SUI HBAR TRUMP WIF POPCAT MOODENG PNUT PENGU VIRTUAL ONDO".split()
)
ACCOUNT_TYPES = ("taxable", "retirement")
READ_STATUSES = ("complete", "partial", "failed", "not_in_scope")
FILL_STATUSES = ("filled", "partial", "unknown")
LOSS_CLASSIFIED = ("equity_sale", "user_stated")

NOT_EVALUATED = [
    "other brokers",
    "spouse accounts",
    "future recurring/DRIP settings beyond history",
    "different-ticker substantially identical securities (unless you declared them)",
    "transfers-in not visible as lots",
    "whether an option (a call bought or a put sold, deep in the money or not) is substantially identical to "
    "the stock (Rev. Rul. 85-87)",
    "basis and holding-period adjustments on replacement lots from earlier washes "
    "(cross-account ones are not in Robinhood's lots)",
]

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


def _err(code, field, msg, **extra):
    out = {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}
    out.update(extra)
    return out


# ---------------------------------------------------------------------------------------------
# Parsing and formatting helpers
# ---------------------------------------------------------------------------------------------
_DEC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def dec(value, field, required=True, positive=False, nonneg=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InputError("MISSING_FIELD", field, "%s is required" % field)
        return None
    if isinstance(value, bool):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string" % field)
    text = repr(value) if isinstance(value, float) else str(value).strip().replace(",", "")
    if not _DEC_RE.match(text):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string like \"12.50\" (got %r)" % (field, value))
    d = Decimal(text)
    if positive and d <= 0:
        raise InputError("BAD_VALUE", field, "%s must be greater than zero" % field)
    if nonneg and d < 0:
        raise InputError("BAD_VALUE", field, "%s must not be negative" % field)
    return d


def money(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def usd(d):
    """$1,170.00 for status lines (JSON money fields stay plain decimal strings)."""
    q = d.quantize(CENT, rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    whole, frac = str(abs(q)).split(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return "%s$%s.%s" % (sign, ",".join(groups), frac)


def qty(d):
    if d is None:
        return None
    text = format(d.normalize(), "f")
    return "0" if text in ("-0", "") else text


def parse_day(value, field):
    if isinstance(value, str):
        m = _DATE_RE.match(value.strip())
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError as exc:
                raise InputError("BAD_DATE", field, "not a real calendar date: %s" % exc)
    raise InputError("BAD_DATE", field, "expected a trade date YYYY-MM-DD (got %r)" % (value,))


def last4(value, field="account_last4"):
    s = str(value or "").strip()
    if not s:
        raise InputError("MISSING_FIELD", field, "account_last4 is required")
    return s[-4:]


def mask(l4):
    return "••••" + l4


def _need_rh_time(field):
    if rh_time is None:
        raise InputError("RH_TIME_MISSING", field, "rh_time.py is not next to this script; timestamps cannot be "
                                                   "converted to US Eastern trade dates")


def et_datetime(ts, field):
    """ISO 8601 timestamp -> aware US Eastern datetime. Naive values are UTC, as the connector's are."""
    _need_rh_time(field)
    out = rh_time.run("to_et", {"utc": ts})
    if not out.get("ok"):
        e = out["errors"][0]
        raise InputError("BAD_TIME", field, e["msg"])
    return datetime.strptime(out["et"][:19], "%Y-%m-%dT%H:%M:%S")


def next_trading_day(d, inclusive):
    """(date or None, note or None) from the embedded NYSE calendar."""
    if rh_time is None:
        return None, "rh_time.py not found next to this script; first trading day not computed"
    out = rh_time.run("next_trading_day", {"date": d.isoformat(), "inclusive": inclusive})
    if not out.get("ok"):
        return None, out["errors"][0]["msg"]
    return parse_day(out["date"], "date"), None


def utc_midnight_et(d):
    if rh_time is None:
        return None
    out = rh_time.run("to_utc", {"date": d.isoformat()})
    return out.get("utc") if out.get("ok") else None


def trade_dates(item, where, date_key="date", ts_key="timestamp"):
    """Return (trade_date, alternative_or_None, note_or_None) for an item carrying a date or a timestamp.

    A date is taken as the trade date the broker or the user stated. A timestamp is converted to the US
    Eastern calendar date (never read as a UTC date). A fill at 20:00 ET or later, or on a day NYSE is
    closed, is in the 24 Hour Market and may carry the next trading day's trade date, so both candidates
    are returned and the caller applies the later-date / union rule."""
    if item.get(date_key) not in (None, ""):
        return parse_day(item.get(date_key), "%s.%s" % (where, date_key)), None, None
    ts = item.get(ts_key)
    if ts in (None, ""):
        raise InputError("MISSING_FIELD", "%s.%s" % (where, date_key), "give %s (trade date) or %s" % (date_key, ts_key))
    et = et_datetime(ts, "%s.%s" % (where, ts_key))
    d = et.date()
    overnight = et.hour >= OVERNIGHT_START_HOUR
    nxt, _ = next_trading_day(d, inclusive=not overnight)
    if nxt is not None and nxt != d:
        return d, nxt, ("filled %s ET; the trade date may be %s (overnight session or a closed day)"
                        % (et.strftime("%Y-%m-%d %H:%M"), nxt.isoformat()))
    return d, None, None


def window_for(d_early, d_late=None):
    d_late = d_late or d_early
    return d_early - timedelta(days=WINDOW_DAYS), d_late + timedelta(days=WINDOW_DAYS)


def is_crypto(symbol, asset_class):
    if asset_class is not None:
        ac = str(asset_class).strip().lower()
        if ac not in ("equity", "crypto"):
            raise InputError("BAD_VALUE", "asset_class", "asset_class must be equity or crypto")
        return ac == "crypto"
    if symbol.endswith("-USD"):
        return True
    if symbol in CRYPTO_CODES:
        raise InputError("AMBIGUOUS_ASSET_CLASS", "asset_class",
                         "%s is both a crypto code and a possible stock/ETF ticker; pass asset_class "
                         "\"equity\" or \"crypto\"" % symbol)
    return False


def crypto_error(symbol):
    return _err(
        "CRYPTO_NOT_SUBJECT", "symbol",
        "The wash-sale rule (IRC 1091) covers stock and securities; crypto held directly is property "
        "(IRS Notice 2014-21), so it does not apply to %s under current law as of %s. A bill introduced "
        "2026-06-08 would change that but is not law. Re-verify every November; this is not tax advice."
        % (symbol, RULES_AS_OF),
        rules_as_of=RULES_AS_OF, reverify_by="2026-11-01",
    )


# ---------------------------------------------------------------------------------------------
# op: window
# ---------------------------------------------------------------------------------------------
def op_window(data):
    d = parse_day(data.get("date"), "date")
    as_of = parse_day(data.get("as_of"), "as_of") if data.get("as_of") else d
    look = data.get("gtc_lookback_days", DEFAULT_GTC_LOOKBACK_DAYS)
    try:
        look = int(str(look).strip())
    except ValueError:
        raise InputError("BAD_VALUE", "gtc_lookback_days", "gtc_lookback_days must be a whole number of days")
    if look < 0:
        raise InputError("BAD_VALUE", "gtc_lookback_days", "gtc_lookback_days must not be negative")
    mode = str(data.get("mode") or "sale").strip().lower()
    if mode not in ("sale", "planned_buy"):
        raise InputError("BAD_VALUE", "mode", "mode must be sale or planned_buy")
    start, end = window_for(d)
    notes = ["calendar days, both ends included; weekends and holidays do not stretch the window",
             "the trade date sets the window and the tax year, not the settlement date"]
    if mode == "planned_buy":
        loss_from = d - timedelta(days=WINDOW_DAYS)
    else:
        # A buy inside this window can already have washed a loss sold up to 60 days before D, and that loss
        # has first claim on it (Treas. Reg. 1.1091-1(b), (e)); its own window reaches back to D - 90.
        loss_from = d - timedelta(days=2 * WINDOW_DAYS)
        notes.append("loss sales from %s are read too: a buy that already washed an earlier loss cannot wash this "
                     "one (Treas. Reg. 1.1091-1(e))" % loss_from.isoformat())
    lookback_start = start - timedelta(days=look)
    if mode == "sale":  # the earlier losses' own replacement buys, back to D - 90, even with a short GTC lookback
        lookback_start = min(lookback_start, loss_from - timedelta(days=WINDOW_DAYS))
    if loss_from >= as_of - timedelta(days=85):
        span, basis = "3month", "covers %s through %s" % (loss_from.isoformat(), min(d, as_of).isoformat())
    elif loss_from.year == as_of.year:
        span, basis = "ytd", "3month would not reach back to %s" % loss_from.isoformat()
    else:
        span, basis = "all", "the window starts in an earlier calendar year than today"
    dnbu = end + timedelta(days=1)
    first, why = next_trading_day(dnbu, inclusive=True)
    if why:
        notes.append(why)
    created = utc_midnight_et(lookback_start)
    if created is None:
        notes.append("rh_time.py not found: convert %s 00:00 US Eastern to UTC before sending created_at_gte"
                     % lookback_start.isoformat())
    future = None
    if as_of < end:
        future = {"from": (as_of + timedelta(days=1)).isoformat(), "to": end.isoformat(),
                  "note": "buys in this part of the window have not happened yet; only projections can be checked"}
    return {
        "ok": True,
        "date": d.isoformat(),
        "as_of": as_of.isoformat(),
        "tax_year": d.year,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": 2 * WINDOW_DAYS + 1},
        "gtc_lookback_days": look,
        "lookback_start": lookback_start.isoformat(),
        "created_at_gte": created,
        "recurring_created_at_gte": utc_midnight_et(d - timedelta(days=RECURRING_LOOKBACK_DAYS)),
        "drip_created_at_gte": utc_midnight_et(d - timedelta(days=DRIP_LOOKBACK_DAYS)),
        "mode": mode,
        "loss_sales_from": loss_from.isoformat(),
        "pnl_span": span,
        "pnl_span_basis": basis,
        "do_not_buy_until": dnbu.isoformat(),
        "first_trading_day_after": first.isoformat() if first else None,
        "future_part_of_window": future,
        "notes": notes,
    }


# ---------------------------------------------------------------------------------------------
# op: classify_pnl
# ---------------------------------------------------------------------------------------------
def _ts(value, field):
    """ISO timestamp -> aware UTC datetime (naive = UTC)."""
    if not isinstance(value, str) or not value.strip():
        raise InputError("MISSING_FIELD", field, "%s is required (ISO 8601)" % field)
    text = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:[.,](\d+))?([+-]\d{2}:?\d{2})?$", text)
    if not m:
        m2 = _DATE_RE.match(text)
        if m2:
            try:
                return datetime(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)), tzinfo=timezone.utc)
            except ValueError as exc:
                raise InputError("BAD_TIME", field, "not a real date: %s" % exc)
        raise InputError("BAD_TIME", field, "not ISO 8601: %r" % value)
    base = "%sT%s" % (m.group(1), m.group(2) if len(m.group(2)) == 8 else m.group(2) + ":00")
    try:
        dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise InputError("BAD_TIME", field, "not a real date or time: %s" % exc)
    if m.group(3):
        dt = dt.replace(microsecond=int(m.group(3)[:6].ljust(6, "0")))
    off = m.group(4)
    if off:
        sign = -1 if off[0] == "-" else 1
        digits = off[1:].replace(":", "")
        tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4])))
        return dt.replace(tzinfo=tz).astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def op_classify_pnl(data):
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise InputError("MISSING_FIELD", "rows", "give rows: the get_pnl_trade_history trades (every page) "
                                                  "with account_last4 added")
    orders_in = data.get("equity_orders")
    if orders_in is None:
        orders_in = data.get("equity_sells", [])
    if not isinstance(orders_in, list):
        raise InputError("BAD_VALUE", "equity_orders", "equity_orders is the list of get_equity_orders rows")
    try:
        tol_s = int(str(data.get("tolerance_seconds", DEFAULT_TOLERANCE_SECONDS)).strip())
    except ValueError:
        raise InputError("BAD_VALUE", "tolerance_seconds", "tolerance_seconds must be a whole number of seconds")
    if not 0 <= tol_s <= 86400:
        raise InputError("BAD_VALUE", "tolerance_seconds", "tolerance_seconds must be between 0 and 86400")
    tol = timedelta(seconds=tol_s)
    ptol = dec(data.get("price_tolerance_pct", str(DEFAULT_PRICE_TOLERANCE_PCT)), "price_tolerance_pct", nonneg=True)

    sells = []
    for i, o in enumerate(orders_in):
        where = "equity_orders[%d]" % i
        if not isinstance(o, dict):
            raise InputError("BAD_VALUE", where, "each order is an object")
        if str(o.get("side", "")).strip().lower() != "sell":
            continue
        filled = dec(o.get("cumulative_quantity"), where + ".cumulative_quantity", required=False, nonneg=True)
        if not filled:
            continue
        created = _ts(o.get("created_at"), where + ".created_at")
        last = o.get("last_transaction_at") or o.get("updated_at")
        last_dt = _ts(last, where + ".last_transaction_at") if last else created
        sells.append({
            "account_last4": last4(o.get("account_last4"), where + ".account_last4"),
            "symbol": str(o.get("symbol", "")).strip().upper(),
            "id": str(o.get("order_id_short") or o.get("id") or "sell-%d" % i)[:8],
            "from": created - tol,
            "to": last_dt + tol,
            "last": last_dt,
            "avg": dec(o.get("average_price"), where + ".average_price", required=False, positive=True),
            "remaining": filled,
        })

    out_rows, sale_losses, unclassified, unattributed = [], [], [], []
    coverage = {}
    order = sorted(range(len(rows)), key=lambda k: str((rows[k] or {}).get("timestamp", "")))
    for k in order:
        r = rows[k]
        where = "rows[%d]" % k
        if not isinstance(r, dict):
            raise InputError("BAD_VALUE", where, "each row is an object")
        acct = last4(r.get("account_last4"), where + ".account_last4")
        sym = str(r.get("symbol") or "").strip().upper()
        side = str(r.get("side") or "").strip().lower()
        q = dec(r.get("quantity"), where + ".quantity", required=False, nonneg=True)
        px = dec(r.get("price"), where + ".price", required=False, nonneg=True)
        gain = r.get("realized_gain", r.get("realized_usd"))
        gain = dec(gain, where + ".realized_gain", required=False)
        when = _ts(r.get("timestamp"), where + ".timestamp")
        # A fill at 20:00 ET or later (the overnight session) or on a closed day may carry the next trading
        # day's trade date: keep both candidates, exactly as for buys.
        d_et, d_alt, d_note = trade_dates({"timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ")}, where)
        alt_iso = d_alt.isoformat() if d_alt else None
        row = {"account_last4": acct, "timestamp": r.get("timestamp"), "date": d_et.isoformat(), "alt_date": alt_iso,
               "symbol": sym, "side": side, "quantity": qty(q) if q is not None else None,
               "price": str(px) if px is not None else None, "realized_usd": money(gain)}
        if d_note:
            row["date_note"] = d_note
        cls, reason, matched = "unclassified", None, None
        if not sym:
            cls, reason = "unattributed", "no symbol (prediction market, adjustment or similar); not a share sale"
        elif side != "sell":
            reason = ("side %r is not a sell; a closing buy (short or option) is not a share sale" % side
                      if side else "no side; cannot be a confirmed share sale")
        elif q is None or q <= 0:
            reason = "no quantity"
        else:
            best = None
            for s in sells:
                if s["account_last4"] != acct or s["symbol"] != sym or not (s["from"] <= when <= s["to"]):
                    continue
                if s["remaining"] < q:
                    continue
                if s["avg"] is None or px is None:
                    continue
                if abs(px - s["avg"]) > s["avg"] * ptol / 100:
                    continue
                gap = abs((s["last"] - when).total_seconds())
                if best is None or gap < best[0]:
                    best = (gap, s)
            if best is not None:
                best[1]["remaining"] -= q
                cls, matched = "equity_sale", best[1]["id"]
                reason = "matches filled equity sell %s (symbol, quantity, time and price)" % matched
            else:
                reason = ("no filled equity sell in this account matches its symbol, quantity, time and price: it may "
                          "be an option close (listed under the underlying) or crypto (listed under its base code)")
        row.update({"classification": cls, "matched_order": matched, "reason": reason})
        out_rows.append(row)
        loss = gain is not None and gain < 0
        dates = {"date": d_et.isoformat(), "alt_date": alt_iso}
        if d_note:
            dates["date_note"] = d_note
        if cls == "equity_sale" and loss:
            sale_losses.append(dict(dates, account_last4=acct, symbol=sym, shares=qty(q), realized_usd=money(gain),
                                    loss_per_share=money(-gain / q), classification="equity_sale",
                                    order_id_short=matched))
        elif cls == "unclassified" and (gain is None or loss):
            unclassified.append(dict(dates, account_last4=acct, symbol=sym, quantity=qty(q) if q is not None else None,
                                     shares=qty(q) if q is not None else None, price=row["price"],
                                     realized_usd=money(gain), classification="unclassified", reason=reason))
        elif cls == "unattributed" and (gain is None or loss):
            unattributed.append(dict(dates, account_last4=acct, realized_usd=money(gain)))
        if sym:
            c = coverage.setdefault(sym, {"status": "complete", "unclassified_loss_rows": 0, "equity_sale_rows": 0})
            if cls == "equity_sale":
                c["equity_sale_rows"] += 1
            if cls == "unclassified" and (gain is None or loss):
                c["unclassified_loss_rows"] += 1
                c["status"] = "unknown"
    notes = ["a row is a share sale only when a filled get_equity_orders sell matches it (same account, symbol, "
             "time within %ds of the order's life, enough filled quantity, price within %s%% of its average)"
             % (tol.seconds, qty(ptol)),
             "unclassified loss rows make that symbol's wash check unknown, never clear"]
    if unattributed:
        notes.append("%d loss row(s) have no symbol; they cannot be tied to any stock" % len(unattributed))
    return {"ok": True, "rows": out_rows, "equity_sale_losses": sale_losses, "unclassified_losses": unclassified,
            "unattributed_losses": unattributed, "coverage_by_symbol": coverage,
            "counts": {"rows": len(out_rows), "equity_sale": sum(1 for r in out_rows if r["classification"] == "equity_sale"),
                       "unclassified": sum(1 for r in out_rows if r["classification"] == "unclassified"),
                       "unattributed": sum(1 for r in out_rows if r["classification"] == "unattributed")},
            "notes": notes}


# ---------------------------------------------------------------------------------------------
# op: run
# ---------------------------------------------------------------------------------------------
class Ctx(object):
    """Accounts in scope, the window and the collected result lists for one run."""

    def __init__(self, accounts):
        self.types, self.status, self.labels = {}, {}, {}
        for i, a in enumerate(accounts):
            where = "accounts[%d]" % i
            if not isinstance(a, dict):
                raise InputError("BAD_VALUE", where, "each account is an object")
            l4 = last4(a.get("last4") or a.get("account_last4"), where + ".last4")
            t = str(a.get("type", "")).strip().lower()
            if t not in ACCOUNT_TYPES:
                raise InputError("BAD_VALUE", where + ".type",
                                 "type must be taxable or retirement (classify from get_accounts, [accounts] "
                                 "retirement, or ask the user once)")
            s = str(a.get("read_status", "")).strip().lower()
            if s not in READ_STATUSES:
                raise InputError("BAD_VALUE", where + ".read_status",
                                 "read_status must be one of %s" % ", ".join(READ_STATUSES))
            self.types[l4], self.status[l4] = t, s
            self.labels[l4] = str(a.get("label") or "").strip()
        self.conflicts, self.possible, self.unknowns, self.ignored, self.excluded = [], [], [], [], []
        self.unmatched_in_window, self.notes = [], []

    def type_of(self, l4, where):
        if l4 not in self.types:
            raise InputError("UNKNOWN_ACCOUNT", where, "account ending %s is not in accounts; list every account "
                                                       "from get_accounts with its type and read_status" % l4)
        return self.types[l4]


def classify_dates(d, alt, w):
    """-> ('in', date_used) | ('out', None) | ('straddle', date_inside)"""
    cands = [d] + ([alt] if alt else [])
    inside = [c for c in cands if w[0] <= c <= w[1]]
    if not inside:
        return "out", None
    if len(inside) < len(cands):
        return "straddle", inside[0]
    return "in", max(inside)


def direction(b, d_early, d_late):
    if b < d_early:
        return "before"
    if b > d_late:
        return "after"
    return "same_day"


def norm_buys(ctx, items, where_name, w, kind, ignored=None):
    """Normalize known buys; split into (definite, possible) inside the window. Buys outside it go to `ignored`
    (default: the run's ignored list)."""
    definite, possible = [], []
    ignored = ctx.ignored if ignored is None else ignored
    for i, b in enumerate(items or []):
        where = "%s[%d]" % (where_name, i)
        if not isinstance(b, dict):
            raise InputError("BAD_VALUE", where, "each buy is an object")
        acct = last4(b.get("account_last4"), where + ".account_last4")
        atype = ctx.type_of(acct, where + ".account_last4")
        d, alt, note = trade_dates(b, where)
        shares = dec(b.get("shares"), where + ".shares", required=False, positive=True)
        fill = str(b.get("fill_status", "filled")).strip().lower()
        if fill not in FILL_STATUSES:
            raise InputError("BAD_VALUE", where + ".fill_status", "fill_status must be filled, partial or unknown")
        rec = {"account_last4": acct, "account_type": atype, "date": d, "shares": shares,
               "source": str(b.get("source", "order")), "placed_agent": b.get("placed_agent"),
               "order_state": b.get("order_state"), "fill_status": fill, "order_id_short": b.get("order_id_short"),
               "lot_id": b.get("lot_id"), "symbol": b.get("symbol"), "kind": kind, "date_note": note, "idx": i}
        rec["cands"] = [d] + ([alt] if alt else [])
        where_in, used = classify_dates(d, alt, w)
        if where_in == "out" and b.get("created_at") and fill != "unknown" and d > w[1]:
            created = et_datetime(b["created_at"], where + ".created_at").date()
            if created <= w[1]:
                rec.update({"date": max(created, w[0]), "fill_status": "unknown",
                            "why": ("created %s and last filled %s, after the window: some shares may have filled "
                                    "inside it" % (created.isoformat(), d.isoformat()))})
                possible.append(rec)
                continue
        if where_in == "out":
            ignored.append({"account_last4": acct, "date": d.isoformat(), "why": "outside the window"})
            continue
        rec["date"] = used
        if where_in == "straddle":
            rec["why"] = note + "; only one of the candidate trade dates is inside the window"
            possible.append(rec)
        elif fill == "unknown":
            rec["why"] = "fill time or quantity not confirmed; some shares may have filled inside the window"
            possible.append(rec)
        elif shares is None:
            rec["why"] = "filled inside the window but the share quantity is unknown"
            possible.append(rec)
        else:
            if note:
                rec["date_note"] = note
            definite.append(rec)
    return definite, possible


def _buy_view(b, d_early, d_late):
    out = {"account_last4": b["account_last4"], "account_type": b["account_type"], "date": b["date"].isoformat(),
           "shares": qty(b["shares"]), "source": b["source"], "direction": direction(b["date"], d_early, d_late)}
    for key in ("placed_agent", "order_state", "fill_status", "order_id_short", "lot_id", "symbol"):
        if b.get(key) not in (None, ""):
            out[key] = b[key]
    if b.get("date_note"):
        out["date_note"] = b["date_note"]
    return out


def match(buys, lots):
    """Chronological matching. lots: list of dicts with lot_id, remaining, loss_per_share (mutated)."""
    results = []
    for b in sorted(buys, key=lambda x: (x["date"], x["account_last4"], str(x.get("order_id_short") or ""),
                                           x["idx"])):
        avail = b["shares"]
        allocations = []
        for lot in lots:
            if avail <= 0:
                break
            take = min(avail, lot["remaining"])
            if take > 0:
                lot["remaining"] -= take
                avail -= take
                allocations.append((lot, take))
        results.append((b, allocations))
    return results


FREQ_MONTHS = {"monthly": 1, "quarterly": 3, "semi_annually": 6, "semi_annual": 6, "semiannually": 6,
               "semiannual": 6, "annually": 12, "annual": 12, "yearly": 12}


def add_months(d, months):
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    last_day = (date(y + (m // 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last_day))


def project_recurring(entry, where, w, as_of, known_dates):
    dates = sorted(set(parse_day(x, "%s.dates" % where) for x in (entry.get("dates") or [])))
    if len(dates) < 2:
        return None, "only %d recurring buy(s) in history; cadence unknown" % len(dates)
    gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]))
    median = gaps[len(gaps) // 2] if len(gaps) % 2 else (gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2]) / 2.0
    projected = []
    if 26 <= median <= 35:
        days = [x.day for x in dates]
        anchor = sorted(set(days), key=lambda v: (-days.count(v), v))[0]
        cadence = "monthly around day %d" % anchor
        y, m = dates[-1].year, dates[-1].month
        for _ in range(4):
            m += 1
            if m > 12:
                y, m = y + 1, 1
            last_day = (date(y + (m // 12), m % 12 + 1, 1) - timedelta(days=1)).day
            scheduled = date(y, m, min(anchor, last_day))
            actual, _ = next_trading_day(scheduled, inclusive=True)
            projected.append(actual or scheduled)
    else:
        step = 7 if 5 <= median <= 9 else 14 if 12 <= median <= 16 else int(round(median))
        cadence = "every %d days" % step
        cur = dates[-1]
        for _ in range(12):
            cur = cur + timedelta(days=step)
            actual, _ = next_trading_day(cur, inclusive=True)
            projected.append(actual or cur)
    hits = [p for p in projected if w[0] <= p <= w[1] and p >= as_of and p not in known_dates]
    return {"cadence": cadence, "dates": hits}, None


def build_loss_lots(ctx, sale, symbol, sale_acct):
    """-> (loss_lots, gain_lots, price) for the sale being checked. Every lot carries the account it is sold
    from (lots_sold[].account_last4, default the sale's account), so one run can check lots sold the same day
    in several accounts against one shared pool of replacement shares."""
    where = "sale"
    price = dec(sale.get("price_per_share"), where + ".price_per_share", required=False, positive=True)
    lots_in = sale.get("lots_sold")
    loss_lots, gain_lots = [], []
    if lots_in:
        if price is None:
            raise InputError("MISSING_FIELD", where + ".price_per_share", "price_per_share is required with lots_sold "
                                                                          "(the user's limit, or the quote for an estimate)")
        for i, lot in enumerate(lots_in):
            lw = "%s.lots_sold[%d]" % (where, i)
            if not isinstance(lot, dict):
                raise InputError("BAD_VALUE", lw, "each lot is an object")
            shares = dec(lot.get("shares"), lw + ".shares", positive=True)
            lot_id = str(lot.get("lot_id") or lot.get("open_lot_id") or "lot%d" % (i + 1))
            lot_acct = last4(lot.get("account_last4"), lw + ".account_last4") if lot.get("account_last4") else sale_acct
            lot_type = ctx.type_of(lot_acct, lw + ".account_last4")
            raw_acq = lot.get("acquired") or lot.get("open_date")
            acquired = lot_day(raw_acq, lw + ".acquired") if raw_acq else None
            cost = dec(lot.get("cost_per_share"), lw + ".cost_per_share", required=False, nonneg=True)
            rec = {"lot_id": lot_id, "shares": shares, "remaining": shares, "acquired": acquired, "cost": cost,
                   "account_last4": lot_acct}
            if lot_type == "retirement":
                ctx.notes.append("lot %s is sold in retirement account %s: a loss there is not deductible, so it "
                                 "cannot be washed" % (lot_id, mask(lot_acct)))
                gain_lots.append(rec)
                continue
            if cost is None:
                ctx.unknowns.append({"kind": "basis_pending", "lot_id": lot_id,
                                     "why": "cost basis not reported yet (never treated as zero); this lot's loss is unknown"})
                continue
            loss_ps = cost - price
            if loss_ps > 0:
                rec["loss_per_share"] = loss_ps
                loss_lots.append(rec)
            else:
                gain_lots.append(rec)
        return loss_lots, gain_lots, price
    shares = dec(sale.get("shares"), where + ".shares", positive=True)
    lps = dec(sale.get("loss_per_share"), where + ".loss_per_share", required=False)
    if lps is None:
        realized = dec(sale.get("realized_usd"), where + ".realized_usd", required=False)
        if realized is None:
            raise InputError("MISSING_FIELD", where + ".lots_sold", "give lots_sold with price_per_share, or shares with "
                                                                    "loss_per_share or realized_usd")
        lps = -realized / shares
    base_id = str(sale.get("lot_id") or "sale")
    acq_lots = sale.get("acquired_lots")
    recs = []
    if acq_lots:
        # The sold shares came from several buys: [{acquired, shares}], summing to the shares sold.
        if not isinstance(acq_lots, list):
            raise InputError("BAD_VALUE", where + ".acquired_lots", "acquired_lots is a list of {acquired, shares}")
        total = ZERO
        for j, part in enumerate(acq_lots):
            pw = "%s.acquired_lots[%d]" % (where, j)
            if not isinstance(part, dict):
                raise InputError("BAD_VALUE", pw, "each entry is {acquired, shares}")
            n = dec(part.get("shares"), pw + ".shares", positive=True)
            total += n
            recs.append({"lot_id": "%s-%d" % (base_id, j + 1), "shares": n, "remaining": n,
                         "acquired": lot_day(part.get("acquired") or part.get("open_date"), pw + ".acquired"),
                         "cost": None, "account_last4": sale_acct})
        if total != shares:
            raise InputError("BAD_VALUE", where + ".acquired_lots", "acquired_lots shares sum to %s but the sale sold %s"
                             % (qty(total), qty(shares)))
    else:
        raw_acq = sale.get("acquired") or sale.get("open_date")
        recs.append({"lot_id": base_id, "shares": shares, "remaining": shares,
                     "acquired": lot_day(raw_acq, where + ".acquired") if raw_acq else None, "cost": None,
                     "account_last4": sale_acct})
    for rec in recs:
        if lps > 0:
            rec["loss_per_share"] = lps
            loss_lots.append(rec)
        else:
            gain_lots.append(rec)
    return loss_lots, gain_lots, price


def exclude_own_purchase(ctx, definite, possible, sold_lots, d_late):
    """The purchase of the shares being sold is not a replacement purchase.

    -> (definite, possible, own, maybe_own). `own` are the excluded buy shares and `maybe_own` the shares moved to
    possible because a sold lot's acquisition date is unknown; both can still be replacements for an EARLIER loss
    sale, so they stay in that pool."""
    pool = {}
    for lot in sold_lots:
        pool.setdefault(lot["lot_id"], {"left": lot["shares"], "acquired": lot["acquired"],
                                        "account": lot["account_last4"]})
    kept, own, maybe_own = [], [], []
    for b in definite:
        key = None
        lid = str(b["lot_id"]) if b.get("lot_id") else None
        if lid in pool and pool[lid]["left"] > 0 and pool[lid]["account"] == b["account_last4"]:
            key = lid
        else:
            for lot_id, p in pool.items():
                if p["account"] == b["account_last4"] and p["acquired"] == b["date"] and p["left"] > 0:
                    key = lot_id
                    break
        if key is None:
            kept.append(b)
            continue
        take = min(b["shares"], pool[key]["left"])
        pool[key]["left"] -= take
        own.append(dict(b, shares=take))
        ctx.excluded.append({"account_last4": b["account_last4"], "date": b["date"].isoformat(), "shares": qty(take),
                             "lot_id": key, "why": "this is the purchase of shares being sold, not a replacement"})
        rest = b["shares"] - take
        if rest > 0:
            possible.append(dict(b, shares=rest,
                                 why=("bought the same day as lot %s that is being sold; if these shares came from the "
                                      "same purchase they are generally not replacement shares (Rev. Rul. 56-602), "
                                      "otherwise they are" % key)))
    definite = kept
    # A sold lot with no acquisition date (a past sale given as shares + realized_usd, or a lot row without
    # open_date): a buy in the same account up to the sale date may be the purchase of those very shares. It is
    # neither excluded nor counted as a definite replacement; it becomes possible.
    unknown = {}
    for p in pool.values():
        if p["acquired"] is None and p["left"] > 0:
            unknown[p["account"]] = unknown.get(p["account"], ZERO) + p["left"]
    if unknown:
        kept = []
        for b in sorted(definite, key=lambda x: (x["date"], x["idx"])):
            cap = unknown.get(b["account_last4"], ZERO)
            if cap > 0 and b["date"] <= d_late:
                take = min(cap, b["shares"])
                unknown[b["account_last4"]] = cap - take
                part = dict(b, shares=take,
                            why=("may be the purchase of the shares sold (their acquisition date was not given); if "
                                 "it is not, these shares are a replacement"))
                possible.append(part)
                maybe_own.append(part)
                if b["shares"] > take:
                    kept.append(dict(b, shares=b["shares"] - take))
                continue
            kept.append(b)
        definite = kept
    return definite, possible, own, maybe_own


def norm_loss(ctx, r, where):
    """A realized loss sale (realized_losses entry or classified trade-history row) -> dict, or None for a gain."""
    if not isinstance(r, dict):
        raise InputError("BAD_VALUE", where, "each loss is an object")
    la = last4(r.get("account_last4"), where + ".account_last4")
    ltype = ctx.type_of(la, where + ".account_last4")
    d, dalt, dnote = trade_dates(r, where)
    if dalt is None and r.get("alt_date") not in (None, ""):
        # classify_pnl keeps the overnight-session candidate of a timestamped row as alt_date
        a = parse_day(r.get("alt_date"), where + ".alt_date")
        if a != d:
            dalt = a
            dnote = r.get("date_note") or ("the trade date may be %s (overnight session or a closed day)"
                                           % a.isoformat())
    cls = str(r.get("classification") or "unclassified").strip().lower()
    n = dec(r.get("shares"), where + ".shares", required=False, positive=True)
    lps = dec(r.get("loss_per_share"), where + ".loss_per_share", required=False)
    realized = dec(r.get("realized_usd"), where + ".realized_usd", required=False)
    if lps is None and realized is not None and n:
        lps = -realized / n
    if (lps is not None and lps <= 0) or (lps is None and realized is not None and realized >= 0):
        return None  # a gain or break-even is not a loss sale
    acquired = lot_day(r.get("acquired"), where + ".acquired") if r.get("acquired") else None
    return {"account_last4": la, "account_type": ltype, "date": d, "alt": dalt, "date_note": dnote,
            "cands": [d] + ([dalt] if dalt else []), "cls": cls, "shares": n, "loss_per_share": lps,
            "realized": realized, "acquired": acquired, "reason": r.get("reason")}


def _loss_view(L, used_date=None):
    n, lps = L["shares"], L["loss_per_share"]
    view = {"account_last4": L["account_last4"], "account_type": L["account_type"],
            "date": (used_date or L["date"]).isoformat(), "shares": qty(n),
            "loss_per_share": money(lps) if lps is not None else None,
            "realized_usd": money(L["realized"]) if L["realized"] is not None else (money(-lps * n) if lps and n else None)}
    shown = used_date or L["date"]
    if L.get("alt") and L["alt"] != shown:
        view["alt_date"] = L["alt"].isoformat()
    elif shown != L["date"]:
        view["date_note"] = ("%s; the later candidate is used (it gives the later clean date)"
                             % (L.get("date_note") or "the trade date may be %s" % shown.isoformat()))
    return view


def collect_loss_rows(ctx, data, raw, symbol):
    """Loss sales of `symbol`: realized_losses plus pnl_rows classified against the filled equity sells.
    -> (rows, number of loss rows with no symbol)."""
    rows = []
    for r in data.get("realized_losses") or []:
        if isinstance(r, dict) and r.get("symbol") not in (None, "") and str(r["symbol"]).strip().upper() != symbol:
            continue
        rows.append(r)
    n_unattr = 0
    if raw["pnl_rows"]:
        cls = op_classify_pnl({"rows": raw["pnl_rows"], "equity_orders": raw["orders"],
                               "tolerance_seconds": data.get("tolerance_seconds", DEFAULT_TOLERANCE_SECONDS),
                               "price_tolerance_pct": data.get("price_tolerance_pct",
                                                               str(DEFAULT_PRICE_TOLERANCE_PCT))})
        rows += [x for x in cls["equity_sale_losses"] + cls["unclassified_losses"] if x.get("symbol") == symbol]
        n_unattr = len(cls["unattributed_losses"])
    return rows, n_unattr


def consume_earlier(losses, pool, include_unconfirmed=False):
    """Treas. Reg. 1.1091-1(b)-(e): losses are taken in the order they were disposed of; each is washed by the
    earliest replacement shares inside its own window, and a purchase that washed one loss is disregarded for
    any other. -> ({id(buy): shares used}, [(loss, [(buy, shares)])], [(loss, why not applied)]).

    Whenever it is unclear whether an earlier loss used a buy, it uses none: the current sale's figure is then
    the maximum, never an understatement."""
    used, usage, skipped = {}, [], []
    for L in losses:
        if L["account_type"] == "retirement":
            continue  # a loss inside an IRA or Roth is not deductible, so it is not washed and uses up no buy
        if L["cls"] not in LOSS_CLASSIFIED and not include_unconfirmed:
            skipped.append((L, "not confirmed as a share sale (it may be an option close or crypto listed under this "
                               "ticker); if it was one, it may already have used some of these buys"))
            continue
        if not L["shares"] or L["loss_per_share"] is None:
            skipped.append((L, "its share count or loss is not known"))
            continue
        lo = max(L["cands"]) - timedelta(days=WINDOW_DAYS)
        hi = min(L["cands"]) + timedelta(days=WINDOW_DAYS)
        cands = sorted((b for b in pool if lo <= b["date"] <= hi and b["shares"] > used.get(id(b), ZERO)),
                       key=lambda b: (b["date"], b["account_last4"], str(b.get("order_id_short") or ""), b["idx"]))
        if L["acquired"] is None and any(b["account_last4"] == L["account_last4"] and b["date"] <= max(L["cands"])
                                         for b in cands):
            skipped.append((L, "its purchase date is not known, and a buy in the same account before it may be that "
                               "purchase rather than a replacement"))
            continue
        need, own_left, took = L["shares"], (L["shares"] if L["acquired"] is not None else ZERO), []
        for b in cands:
            if need <= 0:
                break
            avail = b["shares"] - used.get(id(b), ZERO)
            if own_left > 0 and b["account_last4"] == L["account_last4"] and b["date"] == L["acquired"]:
                ex = min(avail, own_left)  # the earlier sale's own purchase is not its replacement
                own_left -= ex
                avail -= ex
            if avail <= 0:
                continue
            take = min(need, avail)
            used[id(b)] = used.get(id(b), ZERO) + take
            need -= take
            took.append((b, take))
        usage.append((L, took))
    return used, usage, skipped


def order_loss_lots(loss_lots):
    """Order of disposition for loss lots sold together -> (indexes, determined). Treas. Reg. 1.1091-1(b): when
    the order cannot be told apart, shares count as sold in the order they were acquired, earliest first, and (c)
    matches the replacement shares against them in that order. Without every acquisition date that order is
    unknown, and the largest loss per share goes first (the most that can be disallowed)."""
    idx = range(len(loss_lots))
    if all(lot["acquired"] is not None for lot in loss_lots):
        return sorted(idx, key=lambda k: (loss_lots[k]["acquired"], -loss_lots[k]["loss_per_share"], k)), True
    return sorted(idx, key=lambda k: (-loss_lots[k]["loss_per_share"], k)), False


def disallowed_if(buys, lots):
    """Disallowed $ when `buys` are matched to fresh copies of `lots`, in the order given."""
    copies = [dict(lot, remaining=lot["shares"]) for lot in lots]
    return sum((lot["loss_per_share"] * t for _, allocs in match([dict(b) for b in buys], copies) for lot, t in allocs),
               ZERO)


def flatten_rows(data):
    """Raw connector rows, either top-level lists carrying account_last4 or grouped per account:
    per_account: [{account_last4, orders: [...], tax_lots: [...], pnl_rows: [...]}]."""
    out = {"orders": list(data.get("orders") or []), "tax_lots": list(data.get("tax_lots") or []),
           "pnl_rows": list(data.get("pnl_rows") or [])}
    for i, grp in enumerate(data.get("per_account") or []):
        where = "per_account[%d]" % i
        if not isinstance(grp, dict):
            raise InputError("BAD_VALUE", where, "each group is {account_last4, orders, tax_lots, pnl_rows}")
        l4 = last4(grp.get("account_last4"), where + ".account_last4")
        for key in ("orders", "tax_lots", "pnl_rows"):
            for row in grp.get(key) or []:
                if not isinstance(row, dict):
                    raise InputError("BAD_VALUE", "%s.%s" % (where, key), "each row is an object")
                out[key].append(dict(row, account_last4=l4))
    return out


def buys_from_orders(ctx, orders, symbol):
    """get_equity_orders rows -> buys. Filled quantity is cumulative_quantity in ANY state (a cancelled order
    can carry a partial fill); the fill time is last_transaction_at; created_at bounds a GTC that filled later."""
    buys, recurring = [], {}
    for i, o in enumerate(orders):
        where = "orders[%d]" % i
        acct = last4(o.get("account_last4"), where + ".account_last4")
        ctx.type_of(acct, where + ".account_last4")
        sym = str(o.get("symbol") or "").strip().upper()
        if (sym and sym != symbol) or str(o.get("side", "")).strip().lower() != "buy":
            continue
        state = str(o.get("state") or "").strip().lower()
        cum = dec(o.get("cumulative_quantity"), where + ".cumulative_quantity", required=False, nonneg=True)
        fill = "filled"
        if cum is None:
            if state == "filled":
                cum = dec(o.get("quantity"), where + ".quantity", required=False, positive=True)
                if cum is None:
                    fill = "unknown"
            elif state == "partially_filled":
                fill = "unknown"
            else:
                continue
        elif cum <= 0:
            continue
        last = o.get("last_transaction_at") or o.get("updated_at")
        if not last:
            last, fill = o.get("created_at"), "unknown"
        if not last:
            raise InputError("MISSING_FIELD", where + ".last_transaction_at", "an order row needs a time")
        agent = str(o.get("placed_agent") or "").strip().lower() or None
        buys.append({"account_last4": acct, "timestamp": last, "created_at": o.get("created_at"),
                     "shares": qty(cum) if cum is not None else None, "source": "order", "placed_agent": agent,
                     "order_state": state or None, "fill_status": fill,
                     "order_id_short": str(o.get("id") or o.get("order_id_short") or "")[:6] or None})
        if agent == "recurring" and fill == "filled" and cum is not None:
            day = et_datetime(last, where + ".last_transaction_at").date()
            recurring.setdefault(acct, []).append((day, cum))
    rec = []
    for acct, rows in sorted(recurring.items()):
        sizes = set(q for _, q in rows)
        rec.append({"account_last4": acct, "dates": sorted(set(d.isoformat() for d, _ in rows)),
                    "shares_each": qty(sizes.pop()) if len(sizes) == 1 else None})
    return buys, rec


def lot_day(value, field):
    if isinstance(value, str) and _DATE_RE.match(value.strip()):
        return parse_day(value, field)
    if value in (None, ""):
        raise InputError("MISSING_FIELD", field, "open_date is required")
    return et_datetime(value, field).date()


def buys_from_lots(ctx, lots, symbol, w, known):
    """Lots cross-check: an open lot acquired inside the window that no order buy explains (a DRIP, a transfer,
    a corporate action) is a buy too."""
    capacity = {}
    for b in known:
        if b.get("shares") is None:
            continue
        for c in b.get("cands") or [b["date"]]:
            key = (b["account_last4"], c)
            capacity[key] = capacity.get(key, ZERO) + b["shares"]
    extra = []
    rows = []
    for i, lot in enumerate(lots):
        where = "tax_lots[%d]" % i
        acct = last4(lot.get("account_last4"), where + ".account_last4")
        atype = ctx.type_of(acct, where + ".account_last4")
        sym = str(lot.get("symbol") or "").strip().upper()
        if sym and sym != symbol:
            continue
        d = lot_day(lot.get("open_date") or lot.get("acquired"), where + ".open_date")
        if not (w[0] <= d <= w[1]):
            continue
        q = dec(lot.get("quantity"), where + ".quantity", positive=True)
        rows.append((d, acct, atype, q, str(lot.get("open_lot_id") or "lot%d" % i)))
    for d, acct, atype, q, lot_id in sorted(rows):
        cap = capacity.get((acct, d), ZERO)
        take = min(cap, q)
        capacity[(acct, d)] = cap - take
        rest = q - take
        if rest > 0:
            extra.append({"account_last4": acct, "account_type": atype, "date": d, "shares": rest,
                          "source": "tax_lot", "placed_agent": None, "order_state": None, "fill_status": "filled",
                          "order_id_short": None, "lot_id": lot_id, "symbol": symbol, "kind": "buy",
                          "date_note": "open lot acquired inside the window with no matching order (dividend "
                                       "reinvestment, transfer or corporate action)", "idx": 10000 + len(extra),
                          "cands": [d]})
    return extra


def run_sale(ctx, data, symbol, as_of):
    sale = data.get("sale")
    if not isinstance(sale, dict):
        raise InputError("MISSING_FIELD", "sale", "mode sale needs sale: {date, account_last4, price_per_share, "
                                                  "lots_sold[]}")
    acct = last4(sale.get("account_last4"), "sale.account_last4")
    sale_type = ctx.type_of(acct, "sale.account_last4")
    d, alt, dnote = trade_dates(sale, "sale")
    d_early, d_late = (d, d) if not alt else (min(d, alt), max(d, alt))
    if dnote:
        ctx.notes.append("sale %s; the window is the union of both candidate dates (later-date rule for the "
                         "end)" % dnote)
    w = window_for(d_early, d_late)
    base = {"mode": "sale", "symbol": symbol, "as_of": as_of.isoformat(), "sale_date": d_late.isoformat(),
            "sale_account_last4": acct, "tax_year": d_late.year,
            "window": {"start": w[0].isoformat(), "end": w[1].isoformat()}}
    if sale_type == "retirement":
        base.update({"status": "not_applicable",
                     "status_line": "NO ACTION: Dollars at stake: none found. A loss inside an IRA or Roth is not "
                                    "deductible, so the wash-sale rule has nothing to disallow for this sale.",
                     "conflicts": [], "possible": [], "unknowns": [], "not_evaluated": list(NOT_EVALUATED),
                     "rules_as_of": RULES_AS_OF})
        return base

    loss_lots, gain_lots, price = build_loss_lots(ctx, sale, symbol, acct)
    raw = flatten_rows(data)
    order_buys, derived_recurring = buys_from_orders(ctx, raw["orders"], symbol)
    buy_items = list(data.get("buys") or []) + order_buys
    definite, possible = norm_buys(ctx, buy_items, "buys", w, "buy")
    definite += buys_from_lots(ctx, raw["tax_lots"], symbol, w, definite + possible)
    definite, possible, own, maybe_own = exclude_own_purchase(ctx, definite, possible, loss_lots + gain_lots, d_late)

    # Earlier loss sales of S (up to 60 days before this one) have first claim on the replacement buys, and a
    # buy that washed one of them cannot wash this sale (Treas. Reg. 1.1091-1(b), (e)).
    checked = ("realized_losses" in data or "pnl_rows" in data
               or any(isinstance(g, dict) and "pnl_rows" in g for g in data.get("per_account") or []))
    earliest_loss = d_early - timedelta(days=2 * WINDOW_DAYS)
    rows, n_unattr = collect_loss_rows(ctx, data, raw, symbol)
    earlier, same_day = [], []
    for i, r in enumerate(rows):
        L = norm_loss(ctx, r, "realized_losses[%d]" % i)
        if L is None or max(L["cands"]) < earliest_loss:
            continue
        if max(L["cands"]) >= d_early:
            if (L["account_last4"] != acct and L["date"] <= d_late and L["account_type"] == "taxable"
                    and L["cls"] in LOSS_CLASSIFIED):
                same_day.append(L)  # another account's loss sale the same day: its order against this one is unknown
            continue  # this sale itself, or a later one: no priority over this sale
        L["idx"] = i
        earlier.append(L)
    earlier.sort(key=lambda L: (L["date"], L["account_last4"], L["idx"]))
    pre_window = (d_early - timedelta(days=3 * WINDOW_DAYS), w[0] - timedelta(days=1))
    pre = []
    if earlier:
        pre, _ = norm_buys(ctx, buy_items, "buys", pre_window, "buy", ignored=[])
        pre += buys_from_lots(ctx, raw["tax_lots"], symbol, pre_window, pre)
    pool = pre + definite + own + maybe_own
    used_by_earlier, usage, skipped = consume_earlier(earlier, pool)
    by_buy = {}
    for L, took in usage:
        for b, t in took:
            by_buy.setdefault(id(b), []).append((L, t))
    all_definite = definite
    definite, used_views = [], []
    for b in all_definite:
        u = used_by_earlier.get(id(b), ZERO)
        if u > 0:
            washed = by_buy.get(id(b), [])
            used_views.append(dict(_buy_view(dict(b, shares=u), d_early, d_late), why=(
                "already washed the loss sale(s) of %s on %s; a purchase that washed one loss cannot wash another "
                "(Treas. Reg. 1.1091-1(e))" % (symbol, ", ".join(sorted(set(
                    "%s in %s" % (L["date"].isoformat(), mask(L["account_last4"])) for L, _ in washed)))))))
        if b["shares"] > u:
            definite.append(dict(b, shares=b["shares"] - u) if u > 0 else b)

    order, determined = order_loss_lots(loss_lots)
    ordered = [loss_lots[k] for k in order]
    alt_unconfirmed = None
    if any(L["cls"] not in LOSS_CLASSIFIED for L in earlier):
        alt_used, _, _ = consume_earlier(earlier, pool, include_unconfirmed=True)
        alt_buys = [dict(b, shares=b["shares"] - alt_used.get(id(b), ZERO)) for b in all_definite
                    if b["shares"] > alt_used.get(id(b), ZERO)]
        alt_unconfirmed = disallowed_if(alt_buys, ordered)
    disallowed_low = None
    if not determined and len(loss_lots) > 1:
        disallowed_low = disallowed_if(definite, sorted(loss_lots, key=lambda x: (x["loss_per_share"], x["lot_id"])))
    matched = match(definite, ordered)
    per_lot = {}
    for lot in loss_lots:
        per_lot[lot["lot_id"]] = {"shares": lot["shares"], "loss_per_share": lot["loss_per_share"],
                                  "washed": ZERO, "disallowed": ZERO, "permanent": ZERO}
    for b, allocs in matched:
        washed = sum((t for _, t in allocs), ZERO)
        if washed <= 0:
            ctx.unmatched_in_window.append(dict(_buy_view(b, d_early, d_late),
                                                why="inside the window, but every loss share was already matched "
                                                    "to an earlier buy"))
            continue
        disallowed = sum((lot["loss_per_share"] * t for lot, t in allocs), ZERO)
        permanent = b["account_type"] == "retirement"
        for lot, t in allocs:
            p = per_lot[lot["lot_id"]]
            p["washed"] += t
            p["disallowed"] += lot["loss_per_share"] * t
            if permanent:
                p["permanent"] += lot["loss_per_share"] * t
        entry = _buy_view(b, d_early, d_late)
        entry.update({"washed_shares": qty(washed), "disallowed_loss_usd": money(disallowed), "permanent": permanent,
                      "matched_lots": [{"lot_id": lot["lot_id"], "shares": qty(t),
                                        "loss_per_share": money(lot["loss_per_share"]),
                                        "disallowed_usd": money(lot["loss_per_share"] * t)} for lot, t in allocs]})
        if permanent:
            entry["effect"] = ("disallowed PERMANENTLY: a replacement bought in an IRA or Roth does not carry the "
                               "loss into its basis (Rev. Rul. 2008-5)")
        else:
            entry["effect"] = ("deferred, not lost: the disallowed loss is added to the basis of these replacement "
                               "shares and their holding period includes the washed shares'")
        ctx.conflicts.append(entry)

    # Possible items: unknown fills, same-day remainders, projections, options, related tickers.
    remaining_lots = [dict(loss_lots[k]) for k in order if loss_lots[k]["remaining"] > 0]
    possible_views = []
    hypo = []
    for b in possible:
        v = _buy_view(b, d_early, d_late)
        v.update({"kind": "buy", "why": b.get("why")})
        possible_views.append(v)
        if b["shares"] is not None:
            hypo.append({"date": b["date"], "shares": b["shares"], "account_last4": b["account_last4"],
                         "order_id_short": None, "idx": len(hypo)})
    known_dates = {}
    for b in definite + possible:
        known_dates.setdefault(b["account_last4"], set()).add(b["date"])
    for i, r in enumerate(data.get("recurring") or derived_recurring):
        where = "recurring[%d]" % i
        ra = last4(r.get("account_last4"), where + ".account_last4")
        ctx.type_of(ra, where + ".account_last4")
        proj, why = project_recurring(r, where, w, as_of, known_dates.get(ra, set()))
        each = dec(r.get("shares_each"), where + ".shares_each", required=False, positive=True)
        if proj is None:
            possible_views.append({"kind": "recurring_unknown_cadence", "account_last4": ra, "date": None,
                                   "why": why + "; a scheduled buy could land inside the window"})
            continue
        for p in proj["dates"]:
            possible_views.append({"kind": "recurring_projected", "account_last4": ra,
                                   "account_type": ctx.types[ra], "date": p.isoformat(), "shares": qty(each),
                                   "cadence": proj["cadence"], "direction": direction(p, d_early, d_late),
                                   "why": "inferred from history; the connector cannot see your schedule"})
            if each is not None:
                hypo.append({"date": p, "shares": each, "account_last4": ra, "order_id_short": None,
                             "idx": len(hypo)})
    for i, r in enumerate(data.get("drip") or []):
        where = "drip[%d]" % i
        ra = last4(r.get("account_last4"), where + ".account_last4")
        ctx.type_of(ra, where + ".account_last4")
        pay = parse_day(r.get("payable_date"), where + ".payable_date") if r.get("payable_date") else None
        exd = parse_day(r.get("ex_dividend_date"), where + ".ex_dividend_date") if r.get("ex_dividend_date") else None
        step = FREQ_MONTHS.get(str(r.get("distribution_frequency") or "").strip().lower().replace("-", "_"))
        approx = False
        while pay is not None and pay < as_of and step:
            pay, approx = add_months(pay, step), True
        if pay is not None and pay < as_of:
            pay = None  # the last dividend was paid; the next date is unknown
        if pay is not None:
            if w[0] <= pay <= w[1]:
                possible_views.append({"kind": "drip_projected", "account_last4": ra, "account_type": ctx.types[ra],
                                       "date": pay.isoformat(), "direction": direction(pay, d_early, d_late),
                                       "why": ("dividend reinvestment expected around the payable date%s; inferred "
                                               "from the dividend schedule, the connector cannot see your DRIP "
                                               "setting" % (" (projected from the payment frequency)" if approx
                                                            else ""))})
        elif exd is None or (w[0] - timedelta(days=35) <= exd <= w[1]):
            possible_views.append({"kind": "drip_unknown_date", "account_last4": ra, "date": None,
                                   "why": "dividend reinvestment is on for %s but the next payable date is unknown; "
                                          "a reinvestment could land inside the window" % symbol})
    for i, o in enumerate(data.get("option_buys") or []):
        where = "option_buys[%d]" % i
        oa = last4(o.get("account_last4"), where + ".account_last4")
        ctx.type_of(oa, where + ".account_last4")
        od, oalt, onote = trade_dates(o, where)
        state, used = classify_dates(od, oalt, w)
        if state == "out":
            continue
        possible_views.append({"kind": "option", "account_last4": oa, "date": used.isoformat(),
                               "description": str(o.get("description") or "option on %s" % symbol),
                               "contracts": o.get("contracts"), "direction": direction(used, d_early, d_late),
                               "why": "an option on the same stock may be substantially identical; not evaluated"})
    declared = set(str(s).strip().upper() for s in (data.get("declared_related") or []))
    for i, r in enumerate(data.get("related_buys") or []):
        where = "related_buys[%d]" % i
        rs = str(r.get("symbol", "")).strip().upper()
        ra = last4(r.get("account_last4"), where + ".account_last4")
        ctx.type_of(ra, where + ".account_last4")
        rd, ralt, _ = trade_dates(r, where)
        state, used = classify_dates(rd, ralt, w)
        if state == "out":
            continue
        rsh = dec(r.get("shares"), where + ".shares", required=False, positive=True)
        possible_views.append({"kind": "declared_related", "symbol": rs, "account_last4": ra,
                               "account_type": ctx.types[ra], "date": used.isoformat(), "shares": qty(rsh),
                               "direction": direction(used, d_early, d_late),
                               "why": "you told us %s may be substantially identical to %s" % (rs, symbol)
                               if rs in declared else "listed as related to %s" % symbol})
        if rsh is not None:
            hypo.append({"date": used, "shares": rsh, "account_last4": ra, "order_id_short": None, "idx": len(hypo)})
    hyp_matched = match(hypo, remaining_lots)
    possible_max = sum((lot["loss_per_share"] * t for _, allocs in hyp_matched for lot, t in allocs), ZERO)

    loss_shares = sum((lot["shares"] for lot in loss_lots), ZERO)
    washed_total = sum((p["washed"] for p in per_lot.values()), ZERO)
    disallowed_total = sum((p["disallowed"] for p in per_lot.values()), ZERO)
    permanent_total = sum((p["permanent"] for p in per_lot.values()), ZERO)
    loss_total = sum((lot["shares"] * lot["loss_per_share"] for lot in loss_lots), ZERO)

    earliest, e_note, recurring_blocks = None, None, False
    if ctx.conflicts:
        dates = [b["date"] for b in definite]
        if d_late < as_of:
            e_note = "the sale date is in the past; there is no later sale date to move to"
        else:
            earliest = max(dates) + timedelta(days=WINDOW_DAYS + 1)
            e_note = "assumes no new buys; recurring/DRIP may re-wash"
            cadences = sorted(set(p["cadence"] for p in possible_views if p.get("kind") == "recurring_projected"))
            if cadences:
                recurring_blocks = True
                e_note = ("assumes no new buys, but your recurring buy (%s) puts a buy within 30 days of any sale "
                          "date while it continues" % "; ".join(cadences))
    dnbu = w[1] + timedelta(days=1)
    first, why = next_trading_day(dnbu, inclusive=True)
    if why:
        ctx.notes.append(why)
    if as_of < w[1]:
        ctx.notes.append("buys from %s through %s have not happened yet: any buy of %s in any account (IRA and Roth "
                         "included) before %s would wash this loss" % ((as_of + timedelta(days=1)).isoformat(),
                                                                         w[1].isoformat(), symbol, dnbu.isoformat()))
    if gain_lots:
        ctx.notes.append("%s share(s) sold at a gain or break-even are not loss shares and cannot be washed"
                         % qty(sum((lot["shares"] for lot in gain_lots), ZERO)))
    if disallowed_low is None:
        disallowed_low = disallowed_total
    matching_note = None
    if len(loss_lots) > 1 and not determined and disallowed_low != disallowed_total:
        missing = [lot["lot_id"] for lot in loss_lots if lot["acquired"] is None]
        matching_note = ("several loss lots in one sale, and the acquisition date of %s is missing, so the order the "
                         "regulations use (earliest acquired first, Treas. Reg. 1.1091-1(b)-(c)) cannot be applied: "
                         "washed shares are attached to the largest loss per share first (the most that can be "
                         "disallowed); attached smallest-first it would be %s. Pass each lot's acquisition date for "
                         "the exact figure" % (", ".join(missing), usd(disallowed_low)))
    elif len(loss_lots) > 1 and determined and ZERO < washed_total < loss_shares:
        matching_note = ("several loss lots in one sale: replacement shares are matched to the lots sold in the order "
                         "they were acquired, earliest first (Treas. Reg. 1.1091-1(b)-(c))")
    earlier_view = {
        "checked": checked,
        "from": earliest_loss.isoformat(),
        "washed_by_earlier_loss": [dict(_loss_view(L), buys_used=[
            {"account_last4": b["account_last4"], "date": b["date"].isoformat(), "shares": qty(t)} for b, t in took])
            for L, took in usage if took],
        "buys_already_used": used_views,
        "not_applied": [dict(_loss_view(L), why=why) for L, why in skipped],
    }
    if not checked and (ctx.conflicts or possible_views):
        ctx.notes.append("earlier loss sales of %s from %s were not checked: a buy that already washed an earlier loss "
                         "cannot wash this one (Treas. Reg. 1.1091-1(e)), so the disallowed figures are the maximum"
                         % (symbol, earliest_loss.isoformat()))
    if used_views:
        ctx.notes.append("%d in-window buy(s) already washed an earlier loss sale of %s and cannot wash this one (Treas. "
                         "Reg. 1.1091-1(e)); see earlier_loss_sales" % (len(used_views), symbol))
    for L, why in skipped:
        ctx.notes.append("the %s loss sale of %s on %s in %s may already have used some of these buys (Treas. Reg. "
                         "1.1091-1(e)), but %s; the figures assume it did not, so they are the maximum"
                         % ("unconfirmed" if L["cls"] not in LOSS_CLASSIFIED else "earlier", symbol,
                            L["date"].isoformat(), mask(L["account_last4"]), why))
    for L in same_day:
        ctx.notes.append("another loss sale of %s on the same day in %s may have been disposed of first and used some "
                         "of these buys; the figures assume it did not, so they are the maximum"
                         % (symbol, mask(L["account_last4"])))
    if n_unattr:
        ctx.notes.append("%d loss row(s) in the trade history have no symbol; they cannot be tied to %s"
                         % (n_unattr, symbol))

    out = dict(base)
    out.update({
        "price_per_share": money(price) if price is not None else None,
        "loss_shares": qty(loss_shares),
        "loss_per_share": {lot["lot_id"]: money(lot["loss_per_share"]) for lot in loss_lots},
        "loss_usd_total": money(loss_total),
        "per_lot": {k: {"shares": qty(v["shares"]), "loss_per_share": money(v["loss_per_share"]),
                        "washed_shares": qty(v["washed"]), "clean_shares": qty(v["shares"] - v["washed"]),
                        "disallowed_usd": money(v["disallowed"]), "permanent_usd": money(v["permanent"])}
                    for k, v in per_lot.items()},
        "conflicts": ctx.conflicts,
        "possible": possible_views,
        "possible_max_additional_disallowed_usd": money(possible_max),
        "washed_shares": qty(washed_total),
        "clean_shares": qty(loss_shares - washed_total),
        "partial_wash": bool(ctx.conflicts) and washed_total < loss_shares,
        "disallowed_total_usd": money(disallowed_total),
        "disallowed_low_usd": money(disallowed_low),
        "matching_order": (None if len(loss_lots) < 2 else "acquired_earliest_first" if determined
                           else "largest_loss_first"),
        "matching_note": matching_note,
        "disallowed_if_unconfirmed_earlier_losses_are_share_sales_usd": (
            money(alt_unconfirmed) if alt_unconfirmed is not None and alt_unconfirmed != disallowed_total else None),
        "earlier_loss_sales": earlier_view,
        "permanent_total_usd": money(permanent_total),
        "allowed_loss_usd": money(loss_total - disallowed_total),
        "earliest_clean_sale_date": earliest.isoformat() if earliest else None,
        "earliest_clean_sale_note": e_note,
        "recurring_blocks_clean_date": recurring_blocks,
        "do_not_buy_until": dnbu.isoformat(),
        "first_trading_day_after": first.isoformat() if first else None,
        "excluded_as_shares_sold": ctx.excluded,
        "in_window_unmatched": ctx.unmatched_in_window,
    })
    return out


def planned_sale_losses(ctx, data, symbol, w):
    """Loss sales the user has declared they will make (a planned harvest) that fall inside the buy's window.
    planned_sales: [{account_last4, date, price_per_share, lots: [{lot_id, shares, cost_per_share, acquired}]}]
    or [{account_last4, date, shares, loss_per_share | cost_per_share + price_per_share}]."""
    out, lot_ids = [], set()
    for i, ps in enumerate(data.get("planned_sales") or []):
        where = "planned_sales[%d]" % i
        if not isinstance(ps, dict):
            raise InputError("BAD_VALUE", where, "each planned sale is an object")
        sym = ps.get("symbol")
        if sym not in (None, "") and str(sym).strip().upper() != symbol:
            continue
        la = last4(ps.get("account_last4"), where + ".account_last4")
        ltype = ctx.type_of(la, where + ".account_last4")
        d = parse_day(ps.get("date"), where + ".date")
        price = dec(ps.get("price_per_share"), where + ".price_per_share", required=False, positive=True)
        entries = []
        if ps.get("lots"):
            if not isinstance(ps["lots"], list):
                raise InputError("BAD_VALUE", where + ".lots", "lots is a list of {lot_id, shares, cost_per_share}")
            if price is None:
                raise InputError("MISSING_FIELD", where + ".price_per_share", "price_per_share (the user's limit or the "
                                                                              "quote) is required with lots")
            for j, lot in enumerate(ps["lots"]):
                lw = "%s.lots[%d]" % (where, j)
                if not isinstance(lot, dict):
                    raise InputError("BAD_VALUE", lw, "each lot is an object")
                n = dec(lot.get("shares") or lot.get("quantity"), lw + ".shares", positive=True)
                lot_id = str(lot.get("lot_id") or lot.get("open_lot_id") or "planned%d-%d" % (i + 1, j + 1))
                lot_ids.add(lot_id)
                raw_acq = lot.get("acquired") or lot.get("open_date")
                cost = dec(lot.get("cost_per_share"), lw + ".cost_per_share", required=False, nonneg=True)
                if cost is None:
                    ctx.unknowns.append({"kind": "basis_pending", "lot_id": lot_id,
                                         "why": "cost basis not reported yet (never treated as zero); the planned "
                                                "sale's loss on this lot is unknown"})
                    continue
                if cost - price > 0:
                    entries.append({"lot_id": lot_id, "shares": n, "loss_per_share": cost - price,
                                    "acquired": lot_day(raw_acq, lw + ".acquired") if raw_acq else None})
        else:
            n = dec(ps.get("shares"), where + ".shares", positive=True)
            lps = dec(ps.get("loss_per_share"), where + ".loss_per_share", required=False)
            if lps is None:
                cost = dec(ps.get("cost_per_share"), where + ".cost_per_share", required=False, nonneg=True)
                if cost is None or price is None:
                    raise InputError("MISSING_FIELD", where + ".loss_per_share",
                                     "give lots with price_per_share, or shares with loss_per_share (or cost_per_share "
                                     "and price_per_share)")
                lps = cost - price
            if lps > 0:
                entries.append({"lot_id": str(ps.get("lot_id") or "planned%d" % (i + 1)), "shares": n,
                                "loss_per_share": lps, "acquired": None})
        if ltype == "retirement":
            ctx.notes.append("the planned sale in retirement account %s is not deductible, so this buy cannot wash it"
                             % mask(la))
            continue
        if not entries:
            continue
        if not (w[0] <= d <= w[1]):
            ctx.ignored.append({"account_last4": la, "date": d.isoformat(), "why": "planned sale outside the window"})
            continue
        order, _ = order_loss_lots(entries)
        for seq, k in enumerate(order):
            e = entries[k]
            view = {"account_last4": la, "account_type": ltype, "date": d.isoformat(), "shares": qty(e["shares"]),
                    "loss_per_share": money(e["loss_per_share"]),
                    "realized_usd": money(-e["loss_per_share"] * e["shares"]), "lot_id": e["lot_id"],
                    "planned": True}
            out.append({"lot_id": "%s:%s:%s" % (la, d.isoformat(), e["lot_id"]), "date": d, "account_last4": la,
                        "account_type": ltype, "shares": e["shares"], "remaining": e["shares"],
                        "loss_per_share": e["loss_per_share"], "view": view, "planned": True, "sale_seq": (i, seq)})
    return out, lot_ids


def open_lot_exposure(ctx, lots, symbol, price, shares_left, skip_ids, rewash, permanent, buy_date):
    """Open lots of the symbol in taxable accounts that a loss sale through `rewash` would put under this buy's
    window: the harvest the user has not declared. -> (possible items, up-to $ with the buy shares left)."""
    items, loss_recs = [], []
    tail = " PERMANENTLY (an IRA or Roth buy, Rev. Rul. 2008-5)" if permanent else ""
    for i, lot in enumerate(lots):
        where = "tax_lots[%d]" % i
        la = last4(lot.get("account_last4"), where + ".account_last4")
        if ctx.type_of(la, where + ".account_last4") == "retirement":
            continue
        sym = str(lot.get("symbol") or "").strip().upper()
        if sym and sym != symbol:
            continue
        lot_id = str(lot.get("open_lot_id") or lot.get("lot_id") or "lot%d" % i)
        if lot_id in skip_ids:
            continue
        n = dec(lot.get("quantity") or lot.get("quantity_available"), where + ".quantity", required=False,
                nonneg=True)
        cost = dec(lot.get("cost_per_share"), where + ".cost_per_share", required=False, nonneg=True)
        base = {"account_last4": la, "account_type": "taxable", "lot_id": lot_id, "shares": qty(n) if n else None,
                "date": None}
        if cost is None:
            items.append(dict(base, kind="open_lot_basis_pending",
                              why="cost basis pending, so whether this lot is below cost is unknown; a loss sale of it "
                                  "through %s would be washed by this buy%s" % (rewash.isoformat(), tail)))
        elif price is None:
            items.append(dict(base, kind="open_lot_unpriced",
                              why="no price given, so whether this lot is below cost is unknown; a loss sale of it "
                                  "through %s would be washed by this buy%s" % (rewash.isoformat(), tail)))
        elif cost > price and n:
            loss_recs.append({"lot_id": lot_id, "shares": n, "remaining": n, "loss_per_share": cost - price,
                              "view": dict(base, kind="open_lot_below_cost", cost_per_share=money(cost),
                                           loss_per_share=money(cost - price),
                                           unrealized_loss_usd=money((cost - price) * n), permanent=permanent)})
    loss_recs.sort(key=lambda r: (-r["loss_per_share"], r["lot_id"]))
    alloc = {}
    if shares_left > 0 and loss_recs:
        hyp = match([{"date": buy_date, "shares": shares_left, "account_last4": "", "order_id_short": None,
                      "idx": 0}], loss_recs)
        alloc = dict((id(lot), t) for lot, t in hyp[0][1])
    total = ZERO
    for r in loss_recs:
        t = alloc.get(id(r), ZERO)
        total += r["loss_per_share"] * t
        items.append(dict(r["view"], up_to_disallowed_usd=money(r["loss_per_share"] * t), washable_shares=qty(t),
                          why="below cost at %s: a loss sale of this lot through %s would be washed by this buy%s"
                              % (money(price), rewash.isoformat(), tail)))
    return items, total


def run_planned_buy(ctx, data, symbol, as_of):
    pb = data.get("planned_buy")
    if not isinstance(pb, dict):
        raise InputError("MISSING_FIELD", "planned_buy", "mode planned_buy needs planned_buy: {date, account_last4, "
                                                         "shares}")
    acct = last4(pb.get("account_last4"), "planned_buy.account_last4")
    btype = ctx.type_of(acct, "planned_buy.account_last4")
    b, alt, bnote = trade_dates(pb, "planned_buy")
    b_early, b_late = (b, b) if not alt else (min(b, alt), max(b, alt))
    instrument = str(pb.get("instrument") or "shares").strip().lower()
    if instrument not in ("shares", "option"):
        raise InputError("BAD_VALUE", "planned_buy.instrument", "instrument is shares or option")
    option = instrument == "option"
    price = None
    if option:
        # A call bought or a put sold is an option to acquire the stock (IRC 1091(a)); whether it is
        # "substantially identical" is not evaluated, so every loss it could wash is a possible item.
        contracts = dec(pb.get("contracts"), "planned_buy.contracts", positive=True)
        shares = contracts * 100
        structure = str(pb.get("structure") or "option to acquire").strip()
        price = dec(pb.get("underlying_price"), "planned_buy.underlying_price", required=False, positive=True)
    else:
        shares = dec(pb.get("shares"), "planned_buy.shares", required=False, positive=True)
        price = dec(pb.get("price_per_share"), "planned_buy.price_per_share", required=False, positive=True)
        if shares is None:
            amount = dec(pb.get("dollar_amount"), "planned_buy.shares", positive=True)
            px = dec(pb.get("price_per_share"), "planned_buy.price_per_share", positive=True)
            shares = (amount / px).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            ctx.notes.append("share count estimated from the dollar amount at %s; the broker sizes the real order"
                             % money(px))
    w = window_for(b_early, b_late)
    permanent = btype == "retirement"
    rewash = b_late + timedelta(days=WINDOW_DAYS)

    losses, unclassified = [], []
    raw = flatten_rows(data)
    rows, n_unattr = collect_loss_rows(ctx, data, raw, symbol)
    if n_unattr:
        ctx.notes.append("%d loss row(s) in the trade history have no symbol; they cannot be tied to %s"
                         % (n_unattr, symbol))
    for i, r in enumerate(rows):
        where = "realized_losses[%d]" % i
        L = norm_loss(ctx, r, where)
        if L is None:
            continue
        state, used = classify_dates(L["date"], L["alt"], (w[0], w[1]))
        if state == "out":
            ctx.ignored.append({"account_last4": L["account_last4"], "date": L["date"].isoformat(),
                                "why": "loss sale outside the window"})
            continue
        if L["account_type"] == "retirement":
            ctx.notes.append("a loss sold in retirement account %s is not deductible, so it cannot be washed"
                             % mask(L["account_last4"]))
            continue
        view = _loss_view(L, used if state == "in" else None)
        if L["cls"] in LOSS_CLASSIFIED and L["shares"] and L["loss_per_share"] is not None and state == "in":
            losses.append({"lot_id": "%s:%s" % (L["account_last4"], used.isoformat()), "date": used,
                           "account_last4": L["account_last4"], "account_type": L["account_type"],
                           "shares": L["shares"], "remaining": L["shares"], "loss_per_share": L["loss_per_share"],
                           "view": view, "planned": False, "sale_seq": (i, 0)})
        elif state == "straddle":
            unclassified.append(dict(view, kind="ambiguous_trade_date", why=(
                "%s: one candidate trade date is inside this buy's window and one is not" % (L["date_note"] or
                                                                                            "the trade date is ambiguous"))))
        else:
            reason = L["reason"] or ("trade-history row not matched to a filled equity sell: it may be an option "
                                     "close or crypto listed under this ticker, or a share sale" if L["cls"] not in
                                     LOSS_CLASSIFIED else "shares or loss per share missing")
            unclassified.append(dict(view, kind="unclassified_loss", why=reason))

    planned, planned_ids = planned_sale_losses(ctx, data, symbol, w)
    losses += planned
    losses.sort(key=lambda x: (x["date"], x["planned"], x["sale_seq"], x["account_last4"]))
    buy = {"date": b_late, "shares": shares, "account_last4": acct, "order_id_short": None, "idx": 0}
    possible, possible_max = [], ZERO
    if option:
        allocs = match([dict(buy)], [dict(lot) for lot in losses])[0][1]
        took = dict((lot["lot_id"], t) for lot, t in allocs)
        for lot in losses:
            t = took.get(lot["lot_id"], ZERO)
            possible_max += lot["loss_per_share"] * t
            possible.append(dict(lot["view"], kind="option_to_acquire", source=("planned_sale" if lot["planned"]
                                                                                   else "loss_sale"),
                                 direction=direction_of_buy(b_early, b_late, lot["date"]),
                                 up_to_disallowed_usd=money(lot["loss_per_share"] * t), permanent=permanent,
                                 why=("this %s (%s contract(s), %s) is an option to acquire %s, which can wash this loss "
                                      "(IRC 1091(a); Rev. Rul. 85-87 for a short put); whether it is substantially "
                                      "identical is not evaluated" % (structure, qty(contracts),
                                                                      str(pb.get("description") or symbol), symbol))))
        matched_allocs = []
    else:
        matched_allocs = match([buy], losses)[0][1]
    for lot, t in matched_allocs:
        dis = lot["loss_per_share"] * t
        entry = dict(lot["view"])
        entry.update({"source": "planned_sale" if lot["planned"] else "loss_sale",
                      "direction": direction_of_buy(b_early, b_late, lot["date"]),
                      "washed_shares": qty(t), "disallowed_loss_usd": money(dis), "permanent": permanent,
                      "buy_account_last4": acct})
        entry["effect"] = ("disallowed PERMANENTLY: this buy is in an IRA or Roth (Rev. Rul. 2008-5)" if permanent else
                           "deferred, not lost: added to the basis of the shares this buy acquires")
        ctx.conflicts.append(entry)
    washed = sum((t for _, t in matched_allocs), ZERO)
    disallowed = sum((lot["loss_per_share"] * t for lot, t in matched_allocs), ZERO)

    # Open lots below cost: a harvest the user has not declared would be washed by this buy.
    left = shares - (washed if not option else sum((t for t in took.values()), ZERO))
    lot_items, lot_max = open_lot_exposure(ctx, raw["tax_lots"], symbol, price, left, planned_ids, rewash, permanent,
                                           b_late)
    possible += lot_items
    possible_max += lot_max

    others = []
    order_buys, _ = buys_from_orders(ctx, raw["orders"], symbol)
    past = [lot for lot in losses if not lot["planned"]]
    for key, items in (("buys", list(data.get("buys") or []) + order_buys), ("option_buys", data.get("option_buys") or [])):
        for i, ob in enumerate(items):
            where = "%s[%d]" % (key, i)
            oa = last4(ob.get("account_last4"), where + ".account_last4")
            ctx.type_of(oa, where + ".account_last4")
            od, oalt, _ = trade_dates(ob, where)
            for lot in past:
                lw = window_for(lot["date"])
                if any(lw[0] <= c <= lw[1] for c in [od] + ([oalt] if oalt else [])):
                    others.append({"account_last4": oa, "date": od.isoformat(), "shares": ob.get("shares"),
                                   "kind": "option" if key == "option_buys" else "buy",
                                   "near_loss_sale": lot["view"]["date"],
                                   "why": "may already have washed part of that loss; the figures above assume it did "
                                          "not, so they are the maximum"})
                    break
    ctx.unknowns.extend(unclassified)
    loss_dates = [lot["date"] for lot in losses]
    planned_dates = sorted(set(lot["date"] for lot in losses if lot["planned"]))
    earliest = max(loss_dates) + timedelta(days=WINDOW_DAYS + 1) if loss_dates else None
    unc_dates = [parse_day(u["date"], "date") for u in unclassified if u.get("date")]
    unc_dates += [parse_day(u["alt_date"], "alt_date") for u in unclassified if u.get("alt_date")]
    earliest_all = max(loss_dates + unc_dates) + timedelta(days=WINDOW_DAYS + 1) if (loss_dates or unc_dates) else None
    first, why = next_trading_day(earliest, inclusive=True) if earliest else (None, None)
    if why:
        ctx.notes.append(why)
    latest_before = None
    later_planned = [d for d in planned_dates if d > b_late]
    if later_planned:
        latest_before = min(later_planned) - timedelta(days=WINDOW_DAYS + 1)
        if latest_before < as_of:
            ctx.notes.append("buying at least 31 days before the planned sale on %s is no longer possible (that was "
                             "%s)" % (min(later_planned).isoformat(), latest_before.isoformat()))
            latest_before = None
    sell_clean_from = rewash + timedelta(days=1) if planned_dates else None
    if sell_clean_from and any(d.year < sell_clean_from.year for d in planned_dates):
        ctx.notes.append("selling on or after %s instead avoids this buy's window but moves the loss into tax year %d"
                         % (sell_clean_from.isoformat(), sell_clean_from.year))
    ctx.notes.append("this buy opens its own window: a loss sale of %s in any taxable account through %s would be "
                     "washed by it%s" % (symbol, rewash.isoformat(),
                                         ", PERMANENTLY: it is an IRA or Roth buy (Rev. Rul. 2008-5)" if permanent
                                         else ""))
    if bnote:
        ctx.notes.append("planned buy " + bnote)
    if option and not possible and not unclassified:
        ctx.notes.append("no loss sale of %s in the window and no open lot below cost was found; an option to acquire "
                         "bought now would still wash a loss sale through %s" % (symbol, rewash.isoformat()))
    out = {
        "mode": "planned_buy", "symbol": symbol, "as_of": as_of.isoformat(), "buy_date": b_late.isoformat(),
        "buy_account_last4": acct, "buy_shares": qty(shares), "tax_year": b_late.year, "instrument": instrument,
        "window": {"start": w[0].isoformat(), "end": w[1].isoformat()},
        "loss_sales": [lot["view"] for lot in losses if not lot["planned"]],
        "planned_sales_in_window": [lot["view"] for lot in losses if lot["planned"]],
        "planned_sale_dates_washed": sorted(set(c["date"] for c in ctx.conflicts if c.get("source") == "planned_sale")),
        "conflicts": ctx.conflicts, "possible": possible,
        "possible_max_additional_disallowed_usd": money(possible_max),
        "washed_shares": qty(washed),
        "disallowed_total_usd": money(disallowed),
        "permanent_total_usd": money(disallowed if permanent else ZERO),
        "earliest_clean_buy_date": earliest.isoformat() if earliest else None,
        "earliest_clean_buy_date_if_unclassified_are_sales": earliest_all.isoformat() if earliest_all else None,
        "latest_clean_buy_date": latest_before.isoformat() if latest_before else None,
        "sell_clean_from": sell_clean_from.isoformat() if sell_clean_from else None,
        "do_not_buy_until": earliest.isoformat() if earliest else None,
        "first_trading_day_after": first.isoformat() if first else None,
        "rewash_until": rewash.isoformat(),
        "other_buys_near_loss_sales": others,
    }
    if option:
        out["buy_contracts"] = qty(contracts)
    return out


def direction_of_buy(b_early, b_late, loss_date):
    """Where the buy falls relative to the loss sale: before, after or same_day."""
    if b_late < loss_date:
        return "before"
    if b_early > loss_date:
        return "after"
    return "same_day"


def _source_label(c):
    if c.get("source") == "loss_sale":
        return "loss sale"
    if c.get("source") == "planned_sale":
        return "planned loss sale"
    agent = str(c.get("placed_agent") or "").strip().lower()
    if agent in ("recurring", "drip", "agentic", "user"):
        return {"recurring": "recurring buy", "drip": "dividend reinvestment", "agentic": "agent buy",
                "user": "buy"}[agent]
    return "lot acquired" if c.get("source") == "tax_lot" else "buy"


def status_line(out, ctx):
    sym = out["symbol"]
    status = out["status"]
    dis = Decimal(out.get("disallowed_total_usd") or "0")
    perm = Decimal(out.get("permanent_total_usd") or "0")
    if status == "conflict":
        if perm > 0 and perm == dis:
            what = "%s of %s loss permanently disallowed" % (usd(dis), sym)
        elif perm > 0:
            what = "%s of %s loss disallowed (%s of it permanently)" % (usd(dis), sym, usd(perm))
        else:
            what = "%s of %s loss deferred into replacement basis" % (usd(dis), sym)
        if out["mode"] == "sale" and out.get("recurring_blocks_clean_date"):
            what += "; no clean sale date while the recurring buy continues"
        elif out["mode"] == "sale" and out.get("earliest_clean_sale_date"):
            what += " if sold before %s" % out["earliest_clean_sale_date"]
        elif out["mode"] == "planned_buy" and out.get("planned_sale_dates_washed"):
            what += " if you make this buy and sell as planned on %s" % ", ".join(out["planned_sale_dates_washed"])
            ways = []
            if out.get("latest_clean_buy_date"):
                ways.append("buy by %s" % out["latest_clean_buy_date"])
            if out.get("earliest_clean_buy_date"):
                ways.append("buy from %s" % out["earliest_clean_buy_date"])
            if out.get("sell_clean_from"):
                ways.append("sell from %s" % out["sell_clean_from"])
            if ways:
                what += " (clean: %s)" % ", or ".join(ways)
        elif out["mode"] == "planned_buy" and out.get("earliest_clean_buy_date"):
            what += " if bought before %s" % out["earliest_clean_buy_date"]
        return "CONFLICT: Dollars at stake: " + what
    if status == "unknown":
        bits = []
        bad = [mask(a) for a, s in sorted(ctx.status.items()) if s in ("partial", "failed")]
        if bad:
            bits.append("account(s) not fully read: %s" % ", ".join(bad))
        n_unc = sum(1 for u in out["unknowns"] if u.get("kind") == "unclassified_loss")
        if n_unc:
            bits.append("%d loss row(s) not confirmed as share sales" % n_unc)
        n_amb = sum(1 for u in out["unknowns"] if u.get("kind") == "ambiguous_trade_date")
        if n_amb:
            bits.append("%d loss sale(s) filled in the overnight session or on a closed day, whose trade date may fall "
                        "on either side of the window edge" % n_amb)
        n_pend = sum(1 for u in out["unknowns"] if u.get("kind") == "basis_pending")
        if n_pend:
            bits.append("%d lot(s) with cost basis pending" % n_pend)
        amount = out.get("loss_usd_total")
        lead = ("up to %s of %s loss unverified" % (usd(Decimal(amount)), sym)) if amount and Decimal(amount) > 0 \
            else "%s wash status unverified" % sym
        return "UNKNOWN: Dollars at stake: %s (%s)" % (lead, "; ".join(bits) or "coverage incomplete")
    if status == "possible":
        extra = out.get("possible_max_additional_disallowed_usd")
        extra = Decimal(extra) if extra else ZERO
        if out["mode"] == "planned_buy":
            kinds = set(p.get("kind") for p in out["possible"])
            tail = ", permanently (an IRA or Roth buy)" if out.get("buy_account_last4") and \
                ctx.types.get(out["buy_account_last4"]) == "retirement" else ""
            if "option_to_acquire" in kinds:
                lead = ("up to %s of %s loss" % (usd(extra), sym)) if extra > 0 else "not computable"
                return ("POSSIBLE: Dollars at stake: %s if this option counts as acquiring %s (an option to acquire can "
                        "wash a loss sale; not evaluated)%s" % (lead, sym, tail))
            if extra > 0:
                return ("POSSIBLE: Dollars at stake: up to %s of %s loss if you sell your below-cost %s lots at a loss "
                        "through %s: this buy would wash it%s" % (usd(extra), sym, sym, out["rewash_until"], tail))
            return ("POSSIBLE: Dollars at stake: not computable (%d open %s lot(s) with a pending cost or no price; a loss "
                    "sale through %s would be washed by this buy%s)" % (len(out["possible"]), sym, out["rewash_until"],
                                                                       tail))
        if extra > 0:
            return ("POSSIBLE: Dollars at stake: up to %s of %s loss if the possible items below are replacement buys"
                    % (usd(extra), sym))
        return "POSSIBLE: Dollars at stake: not computable (%d possible item(s) for %s; see below)" % (
            len(out["possible"]), sym)
    tail = ""
    if out["mode"] == "planned_buy":
        tail = ("; no loss sale of %s in the 30 days before, but a loss sale through %s would be washed by this buy%s"
                % (sym, out["rewash_until"], ", permanently (an IRA or Roth buy)"
                   if ctx.types.get(out.get("buy_account_last4")) == "retirement" else ""))
    elif (out.get("earlier_loss_sales") or {}).get("buys_already_used"):
        tail = "; the in-window buy(s) already washed an earlier loss sale, so they cannot wash this one"
    if status == "clear_in_scope":
        skipped = [mask(a) for a, s in sorted(ctx.status.items()) if s == "not_in_scope"]
        return ("CLEAR: Dollars at stake: none found for %s in the accounts read (not read, by your choice: %s)%s"
                % (sym, ", ".join(skipped), tail))
    return "CLEAR: Dollars at stake: none found for %s in any of your Robinhood accounts%s" % (sym, tail)


def op_run(data):
    mode = str(data.get("mode", "")).strip().lower()
    if mode not in ("sale", "planned_buy"):
        raise InputError("BAD_VALUE", "mode", "mode must be sale or planned_buy")
    symbol = data.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise InputError("MISSING_FIELD", "symbol", "symbol is required (a ticker string)")
    symbol = symbol.strip().upper()
    for key in ("buys", "realized_losses", "option_buys", "recurring", "drip", "related_buys", "declared_related",
                "orders", "tax_lots", "pnl_rows", "per_account", "planned_sales"):
        value = data.get(key)
        if value is not None and not isinstance(value, list):
            raise InputError("BAD_VALUE", key, "%s must be a list" % key)
        for i, item in enumerate(value or []):
            if key != "declared_related" and not isinstance(item, dict):
                raise InputError("BAD_VALUE", "%s[%d]" % (key, i), "each entry of %s is an object" % key)
    if is_crypto(symbol, data.get("asset_class")):
        return crypto_error(symbol)
    as_of = parse_day(data.get("as_of"), "as_of")
    accounts = data.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise InputError("MISSING_FIELD", "accounts", "list every account from get_accounts with type and "
                                                      "read_status; a check with no accounts can never be clear")
    ctx = Ctx(accounts)
    out = run_sale(ctx, data, symbol, as_of) if mode == "sale" else run_planned_buy(ctx, data, symbol, as_of)
    if out.get("status") == "not_applicable":
        return dict({"ok": True}, **out)
    for l4, s in sorted(ctx.status.items()):
        if s in ("partial", "failed"):
            ctx.unknowns.append({"kind": "account_not_fully_read", "account_last4": l4, "read_status": s,
                                 "why": "a page or call failed or was skipped; buys there are unknown"})
    out["unknowns"] = ctx.unknowns
    if ctx.conflicts:
        status = "conflict"
    elif ctx.unknowns:
        status = "unknown"
    elif out.get("possible"):
        status = "possible"
    elif any(s == "not_in_scope" for s in ctx.status.values()):
        status = "clear_in_scope"
    else:
        status = "clear"
    out["status"] = status
    not_eval = list(NOT_EVALUATED)
    skipped = [mask(a) for a, s in sorted(ctx.status.items()) if s == "not_in_scope"]
    if skipped:
        not_eval.append("accounts you left out: %s" % ", ".join(skipped))
    out.update({
        "accounts_read": [{"account_last4": a, "type": ctx.types[a], "read_status": ctx.status[a],
                           "label": ctx.labels[a] or None} for a in sorted(ctx.types)],
        "ignored_outside_window": len(ctx.ignored),
        "not_evaluated": not_eval,
        "notes": ctx.notes,
        "method": ("61-day window [D-30, D+30] in calendar days on the trade date; replacement buys in every account "
                   "read (IRA and Roth included) matched earliest buy first to loss shares in order of disposition, "
                   "lots sold together earliest acquired first, a buy that washed an earlier loss not reused "
                   "(Treas. Reg. 1.1091-1(b)-(e)); retirement buys disallow permanently (Rev. Rul. 2008-5); status "
                   "is never clear unless every account was read"),
        "rules_as_of": RULES_AS_OF,
    })
    out["status_line"] = status_line(out, ctx)
    top = sorted(ctx.conflicts, key=lambda c: -Decimal(c.get("disallowed_loss_usd") or "0"))[:3]
    out["dollars_at_stake"] = ["%s %s %s: %s disallowed%s" % (
        mask(c["account_last4"]), c.get("date"), _source_label(c), usd(Decimal(c["disallowed_loss_usd"])),
        " (permanent)" if c.get("permanent") else "") for c in top] or ["none found"]
    return dict({"ok": True}, **out)


OPS = {"window": op_window, "classify_pnl": op_classify_pnl, "run": op_run}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected one of %s" % (op, ", ".join(sorted(OPS))))
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
_M = {"type": "string", "pattern": r"^-?\d+\.\d{2}$"}
_MN = {"type": ["string", "null"], "pattern": r"^-?\d+\.\d{2}$"}
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_DATEN = {"type": ["string", "null"], "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_ARR = {"type": "array"}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
_ACCT = {"type": "object", "required": ["last4", "type", "read_status"], "properties": {
    "last4": _S, "type": {"enum": list(ACCOUNT_TYPES)}, "read_status": {"enum": list(READ_STATUSES)}, "label": _S}}
_BUY = {"type": "object", "required": ["account_last4"], "properties": {
    "account_last4": _S, "date": _DATE, "timestamp": _S, "shares": _S, "source": _S, "placed_agent": _S,
    "order_state": _S, "fill_status": {"enum": list(FILL_STATUSES)}, "order_id_short": _S, "lot_id": _S}}
_LOSS = {"type": "object", "required": ["account_last4"], "properties": {
    "account_last4": _S, "date": _DATE, "alt_date": _DATEN, "date_note": _S, "timestamp": _S, "symbol": _S,
    "shares": _S, "loss_per_share": _S, "realized_usd": _S, "acquired": _S,
    "classification": {"enum": ["equity_sale", "user_stated", "unclassified"]}, "reason": _S}}
SCHEMAS = {
    "window": {
        "input": {"type": "object", "required": ["date"], "additionalProperties": False, "properties": {
            "date": _DATE, "as_of": _DATE, "gtc_lookback_days": {"type": ["integer", "string"]},
            "mode": {"enum": ["sale", "planned_buy"]}}},
        "output": {"anyOf": [{"type": "object", "required": [
            "ok", "window", "lookback_start", "created_at_gte", "pnl_span", "do_not_buy_until",
            "first_trading_day_after"], "properties": {
            "ok": {"enum": [True]}, "date": _DATE, "window": {"type": "object"}, "lookback_start": _DATE,
            "created_at_gte": _SN, "recurring_created_at_gte": _SN, "drip_created_at_gte": _SN,
            "pnl_span": {"enum": ["3month", "ytd", "all"]}, "do_not_buy_until": _DATE,
            "first_trading_day_after": _DATEN}}, _ERR]},
    },
    "classify_pnl": {
        "input": {"type": "object", "required": ["rows"], "additionalProperties": False, "properties": {
            "rows": {"type": "array", "items": {"type": "object", "required": ["account_last4", "timestamp"],
                                                "properties": {"account_last4": _S, "timestamp": _S, "symbol": _S,
                                                               "side": _S, "quantity": _S, "price": _S,
                                                               "realized_gain": _SN}}},
            "equity_orders": {"type": "array", "items": {"type": "object", "required": ["account_last4", "side"],
                                                         "properties": {"account_last4": _S, "id": _S,
                                                                        "order_id_short": _S, "symbol": _S,
                                                                        "side": _S, "state": _S,
                                                                        "cumulative_quantity": _S,
                                                                        "average_price": _SN, "created_at": _S,
                                                                        "last_transaction_at": _SN}}},
            "equity_sells": _ARR, "tolerance_seconds": {"type": ["integer", "string"]},
            "price_tolerance_pct": _S}},
        "output": {"anyOf": [{"type": "object", "required": [
            "ok", "rows", "equity_sale_losses", "unclassified_losses", "coverage_by_symbol"], "properties": {
            "ok": {"enum": [True]}, "rows": _ARR, "equity_sale_losses": _ARR, "unclassified_losses": _ARR,
            "unattributed_losses": _ARR, "coverage_by_symbol": {"type": "object"}}}, _ERR]},
    },
    "run": {
        "input": {"type": "object", "required": ["mode", "symbol", "as_of", "accounts"], "additionalProperties": False,
                  "properties": {
                      "mode": {"enum": ["sale", "planned_buy"]}, "symbol": _S,
                      "asset_class": {"enum": ["equity", "crypto"]}, "as_of": _DATE,
                      "sale": {"type": ["object", "null"], "properties": {
                          "date": _DATE, "timestamp": _S, "account_last4": _S, "price_per_share": _S,
                          "shares": _S, "loss_per_share": _S, "realized_usd": _S, "lot_id": _S,
                          "acquired": _S, "open_date": _S,
                          "acquired_lots": {"type": "array", "items": {"type": "object", "required": ["shares"],
                                                                       "properties": {"acquired": _S, "shares": _S}}},
                          "lots_sold": {"type": "array", "items": {"type": "object", "required": ["shares"],
                                                                   "properties": {"lot_id": _S, "open_lot_id": _S,
                                                                                  "open_date": _S, "acquired": _DATE,
                                                                                  "shares": _S, "account_last4": _S,
                                                                                  "cost_per_share": _SN}}}}},
                      "planned_buy": {"type": ["object", "null"], "properties": {
                          "date": _DATE, "timestamp": _S, "account_last4": _S, "shares": _S, "dollar_amount": _S,
                          "price_per_share": _S, "instrument": {"enum": ["shares", "option"]}, "contracts": _S,
                          "structure": _S, "description": _S, "underlying_price": _S}},
                      "planned_sales": {"type": "array", "items": {"type": "object", "required": ["account_last4",
                                                                                                  "date"],
                                        "properties": {"account_last4": _S, "date": _DATE, "symbol": _S,
                                                       "price_per_share": _S, "shares": _S, "loss_per_share": _S,
                                                       "cost_per_share": _S, "lot_id": _S,
                                                       "lots": {"type": "array", "items": {
                                                           "type": "object", "required": ["shares"],
                                                           "properties": {"lot_id": _S, "shares": _S,
                                                                          "cost_per_share": _SN, "acquired": _S}}}}}},
                      "accounts": {"type": "array", "items": _ACCT},
                      "buys": {"type": "array", "items": _BUY},
                      "realized_losses": {"type": "array", "items": _LOSS},
                      "option_buys": _ARR, "recurring": _ARR, "drip": _ARR,
                      "declared_related": {"type": "array", "items": _S}, "related_buys": _ARR,
                      "orders": _ARR, "tax_lots": _ARR, "pnl_rows": _ARR,
                      "per_account": {"type": "array", "items": {"type": "object", "required": ["account_last4"],
                                                                 "properties": {"account_last4": _S, "orders": _ARR,
                                                                                "tax_lots": _ARR,
                                                                                "pnl_rows": _ARR}}},
                      "tolerance_seconds": {"type": ["integer", "string"]}, "price_tolerance_pct": _S}},
        "output": {"anyOf": [{"type": "object", "required": [
            "ok", "status", "status_line", "window", "conflicts", "possible", "unknowns", "disallowed_total_usd",
            "permanent_total_usd", "do_not_buy_until", "first_trading_day_after", "not_evaluated", "rules_as_of"],
            "properties": {
                "ok": {"enum": [True]},
                "status": {"enum": ["conflict", "unknown", "possible", "clear_in_scope", "clear", "not_applicable"]},
                "status_line": _S, "window": {"type": "object"}, "conflicts": _ARR, "possible": _ARR,
                "unknowns": _ARR, "disallowed_total_usd": _M, "permanent_total_usd": _M,
                "loss_shares": _S, "clean_shares": _S, "earliest_clean_sale_date": _DATEN,
                "earliest_clean_buy_date": _DATEN, "do_not_buy_until": _DATEN,
                "first_trading_day_after": _DATEN, "not_evaluated": _ARR, "rules_as_of": _S}},
            {"type": "object", "required": ["ok", "status", "status_line"], "properties": {
                "ok": {"enum": [True]}, "status": {"enum": ["not_applicable"]}}},
            _ERR]},
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
    """Minimal JSON Schema check used by --selftest (type, enum, pattern, required, properties, items, anyOf)."""
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
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs.extend(schema_errors(item, schema["items"], "%s[%d]" % (path, i)))
    return errs


def full_schema():
    ops = {}
    for name, pair in SCHEMAS.items():
        ops[name] = {k: dict(v, **{"$schema": "https://json-schema.org/draft/2020-12/schema"}) for k, v in pair.items()}
    return {"script": SCRIPT, "version": VERSION, "ops": ops}


_HOUSE = [{"last4": "X4F1", "type": "taxable", "read_status": "complete", "label": "Agentic"},
          {"last4": "M7Q5", "type": "taxable", "read_status": "complete", "label": "Individual"},
          {"last4": "P0Z9", "type": "retirement", "read_status": "complete", "label": "Roth IRA"}]

EXAMPLES = [
    ("run", {"mode": "sale", "as_of": "2026-11-16", "symbol": "TSLA",
             "sale": {"date": "2026-11-16", "account_last4": "M7Q5", "price_per_share": "262.00",
                      "lots_sold": [{"lot_id": "L7", "acquired": "2026-06-02", "shares": "20",
                                     "cost_per_share": "340.00"}]},
             "accounts": _HOUSE,
             "buys": [{"account_last4": "P0Z9", "date": "2026-11-06", "shares": "5", "source": "order",
                       "placed_agent": "user", "order_state": "filled", "fill_status": "filled",
                       "order_id_short": "9e2"}]},
     {"ok": True, "status": "conflict", "window": {"start": "2026-10-17", "end": "2026-12-16"}, "loss_shares": "20",
      "loss_per_share": {"L7": "78.00"}, "washed_shares": "5", "clean_shares": "15", "partial_wash": True,
      "disallowed_total_usd": "390.00", "permanent_total_usd": "390.00", "allowed_loss_usd": "1170.00",
      "earliest_clean_sale_date": "2026-12-07", "do_not_buy_until": "2026-12-17",
      "first_trading_day_after": "2026-12-17",
      "conflicts": [{"account_last4": "P0Z9", "account_type": "retirement", "date": "2026-11-06", "shares": "5",
                     "washed_shares": "5", "disallowed_loss_usd": "390.00", "permanent": True,
                     "direction": "before", "source": "order"}],
      "status_line": "CONFLICT: Dollars at stake: $390.00 of TSLA loss permanently disallowed if sold before "
                     "2026-12-07"}),
    ("run", {"mode": "planned_buy", "as_of": "2026-11-16", "symbol": "AMD", "accounts": _HOUSE,
             "planned_buy": {"date": "2026-11-16", "account_last4": "X4F1", "shares": "3"},
             "realized_losses": [{"account_last4": "M7Q5", "date": "2026-11-03", "shares": "10",
                                  "realized_usd": "-412.00", "classification": "equity_sale"}]},
     {"ok": True, "status": "conflict", "washed_shares": "3", "disallowed_total_usd": "123.60",
      "permanent_total_usd": "0.00", "earliest_clean_buy_date": "2026-12-04", "first_trading_day_after": "2026-12-04",
      "conflicts": [{"account_last4": "M7Q5", "date": "2026-11-03", "washed_shares": "3",
                     "disallowed_loss_usd": "123.60", "permanent": False, "direction": "after"}]}),
    ("run", {"mode": "sale", "as_of": "2026-11-16", "symbol": "TSLA", "accounts": _HOUSE,
             "sale": {"date": "2026-11-16", "account_last4": "M7Q5", "price_per_share": "262.00",
                      "lots_sold": [{"lot_id": "L7", "acquired": "2026-06-02", "shares": "20",
                                     "cost_per_share": "340.00"},
                                    {"lot_id": "L1", "acquired": "2025-03-10", "shares": "20",
                                     "cost_per_share": "280.00"}]},
             "buys": [{"account_last4": "P0Z9", "date": "2026-11-06", "shares": "5"}]},
     {"ok": True, "status": "conflict", "disallowed_total_usd": "90.00", "permanent_total_usd": "90.00",
      "allowed_loss_usd": "1830.00", "matching_order": "acquired_earliest_first",
      "per_lot": {"L1": {"washed_shares": "5"}, "L7": {"washed_shares": "0"}}}),
    ("run", {"mode": "planned_buy", "as_of": "2026-12-05", "symbol": "TSLA", "accounts": _HOUSE,
             "planned_buy": {"date": "2026-12-05", "account_last4": "P0Z9", "shares": "5"},
             "planned_sales": [{"account_last4": "M7Q5", "date": "2026-12-31", "shares": "20",
                                "loss_per_share": "78.00"}]},
     {"ok": True, "status": "conflict", "disallowed_total_usd": "390.00", "permanent_total_usd": "390.00",
      "sell_clean_from": "2027-01-05"}),
    ("run", {"mode": "sale", "as_of": "2026-12-31", "symbol": "XYZ", "accounts": _HOUSE,
             "sale": {"date": "2026-12-31", "account_last4": "X4F1", "shares": "10", "loss_per_share": "5.00"}},
     {"ok": True, "status": "clear", "do_not_buy_until": "2027-01-31", "first_trading_day_after": "2027-02-01",
      "tax_year": 2026}),
    ("run", {"mode": "sale", "as_of": "2026-11-16", "symbol": "ETH", "asset_class": "crypto", "accounts": _HOUSE,
             "sale": {"date": "2026-11-16", "account_last4": "X4F1", "shares": "1", "loss_per_share": "10"}},
     {"ok": False, "errors": [{"code": "CRYPTO_NOT_SUBJECT"}]}),
    ("window", {"date": "2026-11-16", "as_of": "2026-11-16", "gtc_lookback_days": 90},
     {"ok": True, "window": {"start": "2026-10-17", "end": "2026-12-16"}, "lookback_start": "2026-07-19",
      "created_at_gte": "2026-07-19T04:00:00Z", "pnl_span": "3month", "do_not_buy_until": "2026-12-17"}),
    ("classify_pnl", {"rows": [
        {"account_last4": "M7Q5", "timestamp": "2026-11-03T15:12:09Z", "symbol": "AMD", "side": "sell",
         "quantity": "10", "price": "120.20", "realized_gain": "-412.00"},
        {"account_last4": "M7Q5", "timestamp": "2026-11-05T15:00:00Z", "symbol": "AMD", "side": "sell",
         "quantity": "1", "price": "3.30", "realized_gain": "-75.00"}],
        "equity_orders": [{"account_last4": "M7Q5", "id": "o1", "symbol": "AMD", "side": "sell", "state": "filled",
                           "cumulative_quantity": "10", "average_price": "120.20",
                           "created_at": "2026-11-03T15:12:01Z", "last_transaction_at": "2026-11-03T15:12:09Z"}]},
     {"ok": True, "equity_sale_losses": [{"date": "2026-11-03", "shares": "10", "loss_per_share": "41.20"}],
      "unclassified_losses": [{"date": "2026-11-05", "realized_usd": "-75.00"}],
      "coverage_by_symbol": {"AMD": {"status": "unknown", "unclassified_loss_rows": 1}}}),
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
    for i, (op, inp, expected) in enumerate(EXAMPLES):
        problems = schema_errors(inp, SCHEMAS[op]["input"])
        out = run(op, inp)
        problems += schema_errors(out, SCHEMAS[op]["output"])
        if not _subset(expected, out):
            problems.append("output mismatch: %s" % json.dumps(out, sort_keys=True))
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: wash_sale.py <window|classify_pnl|run> < input.json")))
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
