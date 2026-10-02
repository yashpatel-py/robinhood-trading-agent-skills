#!/usr/bin/env python3
"""expiry_risk.py - the expiration and assignment radar for held option positions.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: the expensive option surprises happen at the edges of a position's life, and the
connector hands you the pieces without the arithmetic. A long call that finishes a cent in the money
can turn into a share purchase the account cannot pay for; a short call in the money the day before
an ex-dividend date can be assigned early whenever its remaining time value is smaller than the
dividend; a long put exercised without the shares creates a short stock position; a contract that
closes near its strike leaves you guessing whether you will be assigned. Robinhood also force-closes
some positions before expiration: get_option_instruments returns that time as `sellout_datetime`.
This script turns positions + quotes + buying power + shares held + dividend dates into a list of
risks with the dollars attached. It never recommends an action and never exercises anything.

Risk codes (the rules are in references/assignment-and-exercise.md; the formulas in formulas.md):
  AUTO_EXERCISE_CASH_NEED        long call ITM by >= $0.01, expiring in the radar: strike x 100 x qty
  AUTO_EXERCISE_SHARE_DELIVERY   long put ITM, expiring in the radar, shares on hand to deliver
  LONG_PUT_EXERCISE_SHORT_STOCK  long put ITM without the shares: exercise would create a short
  ASSIGNMENT_SHORT_CALL_DELIVERY short call ITM, expiring in the radar: shares to deliver
  ASSIGNMENT_SHORT_PUT_CASH      short put ITM, expiring in the radar: strike x 100 x qty to buy shares
  EARLY_ASSIGNMENT_BEFORE_EX_DIV short call ITM, ex-date on or before expiration and inside the
                                 radar, and extrinsic at the bid = max(0, bid - intrinsic) < dividend
  INDEX_NO_MANUAL_EXERCISE       index option expiring in the radar (cash-settled; exercise_option
                                 rejects index options)
  PIN_RISK                       |underlying - strike| <= max($0.50, 0.5% of strike), expiring within
                                 2 trading days
  EXPIRING_WORTHLESS             long option out of the money, expiring in the radar

Usage:
    python3 expiry_risk.py run < input.json > output.json
    python3 expiry_risk.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash. Prose twin: references/formulas.md.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:  # rh_time.py is synced next to this file (shared/manifest.json); a copied-alone script falls back
    import rh_time as _rh
except ImportError:  # pragma: no cover - exercised by the fallback test with the module hidden
    _rh = None

VERSION = "2.0.0"
SCRIPT = "expiry_risk"
CENT = Decimal("0.01")
STANDARD_MULTIPLIER = Decimal(100)
PIN_MIN_USD = Decimal("0.50")
PIN_PCT = Decimal("0.005")
PIN_TRADING_DAYS = 2
ITM_MIN = Decimal("0.01")
DIV_MATCH_TOL = Decimal("0.2")
DEFAULT_RADAR_DAYS = 7
X = "\u00d7"
MASK = "\u2022\u2022\u2022\u2022"
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ITEM_CODES = ("EARLY_ASSIGNMENT_BEFORE_EX_DIV", "AUTO_EXERCISE_CASH_NEED", "LONG_PUT_EXERCISE_SHORT_STOCK",
              "ASSIGNMENT_SHORT_CALL_DELIVERY", "ASSIGNMENT_SHORT_PUT_CASH", "AUTO_EXERCISE_SHARE_DELIVERY",
              "INDEX_NO_MANUAL_EXERCISE")  # precedence order for the primary `risk` of an item
AUTO_EXERCISE_NOTE = (
    "Robinhood's help article on expiration, exercise and assignment says it generally auto-exercises long options "
    "that are $0.01 or more in the money at expiration if your buying power covers it, and may close positions "
    "before expiration when it does not; check the app for your account. Moneyness here is at the current price "
    "and can change by the close.")


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


# ---------------------------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------------------------
_DEC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def dec(value, field, required=True, minimum=None, positive=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InputError("MISSING_FIELD", field, "%s is required" % field)
        return None
    if isinstance(value, bool):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string" % field)
    text = repr(value) if isinstance(value, float) else str(value).strip()
    if not _DEC_RE.match(text):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string like \"2.20\" (got %r)" % (field, value))
    d = Decimal(text)
    if positive and d <= 0:
        raise InputError("BAD_VALUE", field, "%s must be greater than zero" % field)
    if minimum is not None and d < minimum:
        raise InputError("BAD_VALUE", field, "%s must be at least %s" % (field, minimum))
    return d


def parse_date(value, field, required=True):
    if value is None or value == "":
        if required:
            raise InputError("MISSING_FIELD", field, "%s is required (YYYY-MM-DD)" % field)
        return None
    text = str(value).strip()[:10]
    m = _DATE_RE.match(text)
    if not m:
        raise InputError("BAD_DATE", field, "%s must be YYYY-MM-DD (got %r)" % (field, value))
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise InputError("BAD_DATE", field, "%s is not a real date (%r)" % (field, value)) from None


def parse_ts(value, field):
    if value is None or value == "":
        return None
    text = str(value).strip().replace("z", "Z")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    m = re.match(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)(\.\d+)?([+-]\d{2}:\d{2})$", text)
    if not m:
        raise InputError("BAD_VALUE", field, "%s must be ISO 8601 with an offset or Z (got %r)" % (field, value))
    try:
        dt = datetime.fromisoformat(m.group(1).replace(" ", "T") + (m.group(2) or "")[:7] + m.group(3))
    except ValueError:
        raise InputError("BAD_VALUE", field, "%s is not a real time (%r)" % (field, value)) from None
    return dt.astimezone(timezone.utc)


def whole_qty(value, field):
    d = dec(value, field, positive=True)
    if d != d.to_integral_value():
        raise InputError("BAD_VALUE", field, "%s must be a whole number of contracts" % field)
    return int(d)


def money(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def num(d):
    """Shares and quantities: plain decimal text without trailing zeros."""
    if d is None:
        return None
    text = format(d.normalize(), "f")
    return text


def fmt_money_text(d):
    return "${:,.2f}".format(d.quantize(CENT, rounding=ROUND_HALF_UP))


def contract_label(p):
    return "%s %s %s%s %s%d" % (p["symbol"], p["expiration"].isoformat(), num(p["strike"]),
                               "C" if p["type"] == "call" else "P", X, p["quantity"])


# ---------------------------------------------------------------------------------------------
# Calendar and time (rh_time when present; weekday-only fallback, stated in the output)
# ---------------------------------------------------------------------------------------------
def trading_days_until(as_of, expiration, notes):
    """Trading days after as_of up to and including expiration (0 when it expires today)."""
    if expiration <= as_of:
        return 0
    count, cur = 0, as_of + timedelta(days=1)
    use_rh = _rh is not None
    while cur <= expiration:
        trading = None
        if use_rh:
            try:
                trading = _rh.is_trading_day(cur)
            except Exception:  # CALENDAR_EXPIRED / not covered: fall back for the rest
                use_rh = False
                _note_once(notes, "Holiday calendar does not cover %s: pin-risk day counts use weekdays only."
                           % cur.isoformat())
        if trading is None:
            trading = cur.weekday() < 5
        if trading:
            count += 1
        cur += timedelta(days=1)
    if _rh is None:
        _note_once(notes, "rh_time.py not found next to this script: pin-risk day counts use weekdays only "
                          "(holidays not excluded).")
    return count


def to_et(dt_utc):
    if _rh is not None:
        return _rh.utc_to_et(dt_utc)
    try:
        from zoneinfo import ZoneInfo
        return dt_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return None


def et_text(dt_et):
    hour = dt_et.hour % 12 or 12
    return "%s %s %d:%02d %s ET" % (DAYS[dt_et.weekday()], dt_et.date().isoformat(), hour, dt_et.minute,
                                    "AM" if dt_et.hour < 12 else "PM")


def _note_once(notes, text):
    if text not in notes:
        notes.append(text)


# ---------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------
def parse_position(p, i):
    where = "positions[%d]" % i
    if not isinstance(p, dict):
        raise InputError("BAD_VALUE", where, "each position is an object")
    typ = str(p.get("type", "")).strip().lower()
    side = str(p.get("side", "")).strip().lower()
    if typ not in ("call", "put"):
        raise InputError("BAD_VALUE", where + ".type", "type must be call or put (the instrument's type)")
    if side not in ("long", "short"):
        raise InputError("BAD_VALUE", where + ".side", "side must be long or short (the position's type)")
    last4 = p.get("account_last4")
    if not isinstance(last4, str) or not re.match(r"^[A-Za-z0-9]{4}$", last4.strip()):
        raise InputError("BAD_VALUE", where + ".account_last4", "account_last4 is the last 4 characters of the account")
    symbol = str(p.get("symbol") or p.get("chain_symbol") or "").strip().upper()
    if not symbol:
        raise InputError("MISSING_FIELD", where + ".symbol", "symbol (the underlying) is required")
    underlying_type = str(p.get("underlying_type") or "equity").strip().lower()
    if underlying_type not in ("equity", "index"):
        raise InputError("BAD_VALUE", where + ".underlying_type", "underlying_type is equity or index")
    pending = {}
    if isinstance(p.get("pending"), dict):
        for k, v in sorted(p["pending"].items()):
            q = dec(v, "%s.pending.%s" % (where, k), required=False, minimum=Decimal(0))
            if q is not None and q > 0:
                pending[str(k)] = q
    return {
        "option_id": str(p.get("option_id") or "").strip() or None,
        "account_last4": last4.strip().upper(),
        "symbol": symbol,
        "underlying_key": str(p.get("underlying_symbol") or symbol).strip().upper(),
        "type": typ,
        "side": side,
        "quantity": whole_qty(p.get("quantity"), where + ".quantity"),
        "strike": dec(p.get("strike"), where + ".strike", positive=True),
        "expiration": parse_date(p.get("expiration"), where + ".expiration"),
        "bid": dec(p.get("bid"), where + ".bid", required=False, minimum=Decimal(0)),
        "ask": dec(p.get("ask"), where + ".ask", required=False, minimum=Decimal(0)),
        "underlying_type": underlying_type,
        "multiplier": dec(p.get("multiplier"), where + ".multiplier", required=False, positive=True)
        or STANDARD_MULTIPLIER,
        "sellout": parse_ts(p.get("sellout_datetime"), where + ".sellout_datetime"),
        "pending": pending,
    }


def parse_frequency(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if "semi" in text or "half" in text:
        return 2
    if "quarter" in text:
        return 4
    if "month" in text:
        return 12
    if "annual" in text or "year" in text:
        return 1
    if "week" in text:
        return 52
    return None


def resolve_dividend(entry, price, where):
    """Per-payment dividend and how it was established (see references/assignment-and-exercise.md)."""
    if not isinstance(entry, dict):
        raise InputError("BAD_VALUE", where, "an ex-dividend entry is {ex_date, amount} or {ex_date, "
                                             "dividend_per_share, distribution_frequency?, dividend_yield_pct?}")
    ex_date = parse_date(entry.get("ex_date"), where + ".ex_date", required=False)
    if ex_date is None:
        return None
    if entry.get("amount") is not None:
        amount = dec(entry.get("amount"), where + ".amount", minimum=Decimal(0))
        return {"ex_date": ex_date, "amount": amount, "uncertain": False,
                "basis": str(entry.get("amount_basis") or "per-payment amount as given")}
    dps = dec(entry.get("dividend_per_share"), where + ".dividend_per_share", required=False, minimum=Decimal(0))
    if dps is None:
        return {"ex_date": ex_date, "amount": None, "uncertain": True, "basis": "no dividend amount given"}
    freq = parse_frequency(entry.get("distribution_frequency"))
    yld = dec(entry.get("dividend_yield_pct"), where + ".dividend_yield_pct", required=False, minimum=Decimal(0))
    if price is not None and yld and freq:
        annual = price * yld / 100
        per = annual / freq
        if per > 0 and abs(dps - per) <= DIV_MATCH_TOL * per:
            return {"ex_date": ex_date, "amount": dps, "uncertain": False,
                    "basis": "dividend_per_share matches one payment (checked against the yield: about %s per payment)"
                             % money(per)}
        if freq > 1 and annual > 0 and abs(dps - annual) <= DIV_MATCH_TOL * annual:
            return {"ex_date": ex_date, "amount": dps / freq, "uncertain": False,
                    "basis": "dividend_per_share looks annual (matches the yield); divided by %d payments a year"
                             % freq}
        return {"ex_date": ex_date, "amount": dps, "uncertain": True,
                "basis": "dividend_per_share did not match the yield; used as one payment (unverified)"}
    return {"ex_date": ex_date, "amount": dps, "uncertain": True,
            "basis": "dividend_per_share used as one payment (unverified: no yield or frequency to check it)"}


def price_map(raw, field):
    out = {}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        raise InputError("BAD_VALUE", field, "%s is an object keyed by symbol or account" % field)
    for k, v in raw.items():
        out[str(k).strip().upper()] = dec(v, "%s.%s" % (field, k), required=False, minimum=Decimal(0))
    return out


# ---------------------------------------------------------------------------------------------
# The radar
# ---------------------------------------------------------------------------------------------
def op_run(data):
    as_of = parse_date(data.get("as_of"), "as_of")
    now = parse_ts(data.get("now"), "now")
    radar_raw = data.get("radar_days", DEFAULT_RADAR_DAYS)
    radar_d = dec(radar_raw, "radar_days", minimum=Decimal(1))
    if radar_d != radar_d.to_integral_value():
        raise InputError("BAD_VALUE", "radar_days", "radar_days is a whole number of days")
    radar_days = int(radar_d)
    radar_end = as_of + timedelta(days=radar_days)
    raw_positions = data.get("positions")
    if not isinstance(raw_positions, list):
        raise InputError("MISSING_FIELD", "positions", "positions is a list (it may be empty)")
    positions = [parse_position(p, i) for i, p in enumerate(raw_positions)]
    underlying = price_map(data.get("underlying"), "underlying")
    buying_power = price_map(data.get("buying_power"), "buying_power")
    shares_raw = data.get("shares_held") or {}
    if not isinstance(shares_raw, dict):
        raise InputError("BAD_VALUE", "shares_held", "shares_held is {account_last4: {SYMBOL: shares}}")
    shares_held = {str(a).strip().upper(): price_map(v, "shares_held.%s" % a) for a, v in shares_raw.items()}
    divs_raw = data.get("ex_dividends") or {}
    if not isinstance(divs_raw, dict):
        raise InputError("BAD_VALUE", "ex_dividends", "ex_dividends is {SYMBOL: {ex_date, amount}}")

    notes, unknown, items, pin_risk, worthless, expiring, quiet = [], [], [], [], [], [], []
    divs = {}
    for sym, entry in divs_raw.items():
        key = str(sym).strip().upper()
        divs[key] = resolve_dividend(entry, underlying.get(key), "ex_dividends.%s" % sym)
    share_pool = {}   # (account, symbol) -> shares still uncommitted (short calls / long puts draw on it)
    cash_by_account = {}

    ordered = sorted(positions, key=lambda p: (p["expiration"], p["account_last4"], p["symbol"], p["strike"]))
    for p in ordered:
        label = contract_label(p)
        dte = (p["expiration"] - as_of).days
        in_radar = 0 <= dte <= radar_days
        base = {"option_id": p["option_id"], "account_last4": p["account_last4"], "symbol": p["symbol"],
                "label": label, "type": p["type"], "side": p["side"], "quantity": str(p["quantity"]),
                "strike": money(p["strike"]), "expiration": p["expiration"].isoformat(),
                "expiration_day": DAYS[p["expiration"].weekday()], "dte": dte}
        if dte < 0:
            unknown.append(dict(base, code="EXPIRED_STILL_LISTED",
                                reason="expired %s but still listed: expiration processing may be pending"
                                       % p["expiration"].isoformat()))
            continue
        div = divs.get(p["underlying_key"])
        exdiv_relevant = (p["side"] == "short" and p["type"] == "call" and p["underlying_type"] == "equity"
                          and div is not None and div["ex_date"] is not None
                          and as_of <= div["ex_date"] <= min(p["expiration"], radar_end))
        if not in_radar and not exdiv_relevant:
            continue
        if p["multiplier"] != STANDARD_MULTIPLIER:
            unknown.append(dict(base, code="ADJUSTED_CONTRACT",
                                reason="multiplier %s: an adjusted contract with a non-standard deliverable; exercise "
                                       "and assignment math does not apply, check the deliverable in the app"
                                       % num(p["multiplier"])))
            continue
        spot = underlying.get(p["underlying_key"])
        if spot is None:
            unknown.append(dict(base, code="UNDERLYING_PRICE_MISSING",
                                reason="no underlying price for %s: moneyness unknown" % p["underlying_key"]))
            continue
        # One figure for everything below: the price rounded to cents, as the official close is. Deciding
        # moneyness on 100.005 but printing $100.01 and "OTM by -0.01" would contradict itself.
        spot = spot.quantize(CENT, rounding=ROUND_HALF_UP)
        mult, qty, strike = p["multiplier"], p["quantity"], p["strike"]
        shares = mult * qty
        itm_by = (spot - strike) if p["type"] == "call" else (strike - spot)
        intrinsic = max(Decimal(0), itm_by)
        itm = itm_by >= ITM_MIN
        row = dict(base, underlying_price=money(spot), moneyness="ITM" if itm else "OTM",
                   itm_by=money(itm_by) if itm else None, otm_by=money(-itm_by) if not itm else None,
                   codes=[], warnings=[])
        if p["sellout"] is not None:
            et = to_et(p["sellout"])
            row["sellout_utc"] = p["sellout"].strftime("%Y-%m-%dT%H:%M:%SZ")
            if et is not None:
                row["sellout_et"] = et.isoformat()
                row["sellout_text"] = et_text(et)
            if now is not None:
                hours = (p["sellout"] - now).total_seconds() / 3600.0
                row["hours_until_sellout"] = round(hours, 1)
                row["sellout_passed"] = hours <= 0
        for name, amount in p["pending"].items():
            row["warnings"].append({"code": "PENDING_ACTIVITY", "text": "Robinhood reports %s %s on this position: an "
                                    "order, exercise, assignment or expiration may already be in progress"
                                    % (name, num(amount))})
        pool_key = (p["account_last4"], p["underlying_key"])
        if pool_key not in share_pool:
            held = shares_held.get(p["account_last4"], {}).get(p["underlying_key"])
            share_pool[pool_key] = held
        held = share_pool[pool_key]
        stake = []

        if in_radar and p["underlying_type"] == "index":
            row["codes"].append("INDEX_NO_MANUAL_EXERCISE")
            settle = intrinsic * shares
            row["index_note"] = ("index options settle in cash and cannot be exercised manually (exercise_option "
                                 "rejects them); at the current level this position settles for about %s %s"
                                 % (fmt_money_text(settle), "to you" if p["side"] == "long" else "from you")
                                 if itm else "index options settle in cash and cannot be exercised manually "
                                 "(exercise_option rejects them); out of the money now: settles at $0.00 if it "
                                 "stays there")
            row["settlement_estimate_usd"] = money(settle)
            if itm:
                stake.append(("INDEX_NO_MANUAL_EXERCISE", settle))
        elif in_radar and itm and p["side"] == "long" and p["type"] == "call":
            cash = strike * shares
            bp = buying_power.get(p["account_last4"])
            row["codes"].append("AUTO_EXERCISE_CASH_NEED")
            row["cash_needed_usd"] = money(cash)
            row["buying_power_usd"] = money(bp)
            row["shortfall_usd"] = money(max(Decimal(0), cash - bp)) if bp is not None else None
            row["if_exercised"] = "buy %s %s at %s" % (num(shares), p["underlying_key"], fmt_money_text(strike))
            row["note"] = AUTO_EXERCISE_NOTE
            if bp is None:
                row["warnings"].append({"code": "BUYING_POWER_NOT_READ",
                                        "text": "buying power for %s%s was not read (get_portfolio)"
                                        % (MASK, p["account_last4"])})
            acct = cash_by_account.setdefault(p["account_last4"], {"cash_needed": Decimal(0), "labels": []})
            acct["cash_needed"] += cash
            acct["labels"].append(label)
            stake.append(("AUTO_EXERCISE_CASH_NEED", cash))
        elif in_radar and itm and p["side"] == "long" and p["type"] == "put":
            credit = strike * shares
            if held is None:
                row["codes"].append("LONG_PUT_EXERCISE_SHORT_STOCK")
                row["shares_needed"] = num(shares)
                row["shares_held"] = None
                row["warnings"].append({"code": "SHARES_NOT_READ", "text": "shares of %s in %s%s were not read "
                                        "(get_equity_positions): cannot tell whether exercise would create a short"
                                        % (p["underlying_key"], MASK, p["account_last4"])})
                stake.append(("LONG_PUT_EXERCISE_SHORT_STOCK", spot * shares))
            elif held >= shares:
                share_pool[pool_key] = held - shares
                row["codes"].append("AUTO_EXERCISE_SHARE_DELIVERY")
                row["credit_usd"] = money(credit)
                row["if_exercised"] = "sell %s %s at %s (covered by %s held)" % (
                    num(shares), p["underlying_key"], fmt_money_text(strike), num(held))
                row["note"] = AUTO_EXERCISE_NOTE
                stake.append(("AUTO_EXERCISE_SHARE_DELIVERY", credit))
            else:
                share_pool[pool_key] = Decimal(0)
                short = shares - held
                row["codes"].append("LONG_PUT_EXERCISE_SHORT_STOCK")
                row["shares_needed"] = num(shares)
                row["shares_held"] = num(held)
                row["short_shares_if_exercised"] = num(short)
                row["if_exercised"] = ("sell %s %s at %s; you hold %s in this account, so %s shares would be sold "
                                       "short (an exercise that creates a short needs your explicit choice in the app)"
                                       % (num(shares), p["underlying_key"], fmt_money_text(strike), num(held),
                                          num(short)))
                row["note"] = AUTO_EXERCISE_NOTE
                stake.append(("LONG_PUT_EXERCISE_SHORT_STOCK", spot * short))

        if in_radar and not itm and p["side"] == "long":
            value = (p["bid"] or Decimal(0)) * shares
            worthless.append(dict(base, underlying_price=money(spot), otm_by=money(-itm_by),
                                  value_at_bid_usd=money(value) if p["bid"] is not None else None,
                                  text="expires worthless if it stays out of the money; %s" % (
                                      "the bid now is %s for the position" % fmt_money_text(value)
                                      if p["bid"] is not None else "no bid given")))
            row["codes"].append("EXPIRING_WORTHLESS")

        if itm and p["side"] == "short" and p["type"] == "call" and p["underlying_type"] == "equity":
            need = shares
            covered = held is not None and held >= need
            if held is not None:
                share_pool[pool_key] = max(Decimal(0), held - need)
            if in_radar:
                row["codes"].append("ASSIGNMENT_SHORT_CALL_DELIVERY")
            row["shares_to_deliver"] = num(need)
            row["shares_held"] = num(held) if held is not None else None
            row["covered"] = covered if held is not None else None
            row["proceeds_if_assigned_usd"] = money(strike * need)
            if held is None:
                row["if_assigned"] = "deliver %s %s (shares held not read)" % (num(need), p["underlying_key"])
            elif covered:
                row["if_assigned"] = "deliver %s %s (covered by %s held)" % (num(need), p["underlying_key"], num(held))
            else:
                row["if_assigned"] = ("deliver %s %s; you hold %s in this account, so %s shares would be short unless "
                                      "another leg covers it" % (num(need), p["underlying_key"], num(held),
                                                                 num(need - held)))
            if in_radar:
                stake.append(("ASSIGNMENT_SHORT_CALL_DELIVERY",
                              strike * need if covered else spot * (need - (held or Decimal(0)))))
            if exdiv_relevant:
                if p["bid"] is None:
                    row["warnings"].append({"code": "BID_MISSING", "text": "no bid: extrinsic value unknown, so the "
                                            "early-assignment check could not run"})
                elif div["amount"] is None:
                    row["warnings"].append({"code": "DIVIDEND_UNKNOWN", "text": "ex-dividend %s but no amount: the "
                                            "early-assignment check could not run" % div["ex_date"].isoformat()})
                else:
                    extrinsic_bid = max(Decimal(0), p["bid"] - intrinsic)
                    extrinsic_mid = None
                    if p["ask"] is not None:
                        extrinsic_mid = max(Decimal(0), (p["bid"] + p["ask"]) / 2 - intrinsic)
                    row["ex_date"] = div["ex_date"].isoformat()
                    row["dividend"] = money(div["amount"])
                    row["dividend_basis"] = div["basis"]
                    row["extrinsic_bid"] = money(extrinsic_bid)
                    row["extrinsic_mid"] = money(extrinsic_mid)
                    if extrinsic_bid < div["amount"]:
                        row["codes"].append("EARLY_ASSIGNMENT_BEFORE_EX_DIV")
                        row["label_estimate"] = ("estimate%s: assignment is the option holder's choice; early "
                                                 "exercise tends to happen the day before the ex-date when the time "
                                                 "value left is smaller than the dividend"
                                                 % ("; dividend amount unverified" if div["uncertain"] else ""))
                        stake.append(("EARLY_ASSIGNMENT_BEFORE_EX_DIV", div["amount"] * need))
                        if div["ex_date"] == as_of:
                            row["warnings"].append({"code": "EX_DATE_TODAY", "text": "the ex-date is today: an early "
                                                    "assignment for it would already have been decided last night; "
                                                    "check for an assignment notice"})
        elif itm and p["side"] == "short" and p["type"] == "put" and in_radar and p["underlying_type"] == "equity":
            cash = strike * shares
            row["codes"].append("ASSIGNMENT_SHORT_PUT_CASH")
            row["cash_needed_usd"] = money(cash)
            row["buying_power_usd"] = money(buying_power.get(p["account_last4"]))
            row["if_assigned"] = "buy %s %s at %s" % (num(shares), p["underlying_key"], fmt_money_text(strike))
            row["note"] = ("a cash-secured put normally has this cash set aside already; on a spread, the long leg "
                           "may offset it")
            stake.append(("ASSIGNMENT_SHORT_PUT_CASH", cash))
        elif in_radar and not itm and p["side"] == "short":
            row["short_otm_text"] = ("out of the money now: expires worthless (you keep the premium) if it stays "
                                     "there; assignment is still possible until expiration")

        pinned = False
        if in_radar:
            band = max(PIN_MIN_USD, PIN_PCT * strike)
            tdays = trading_days_until(as_of, p["expiration"], notes)
            row["trading_days_to_expiration"] = tdays
            if abs(spot - strike) <= band and tdays <= PIN_TRADING_DAYS:
                pinned = True
                row["codes"].append("PIN_RISK")
                pin_risk.append(dict(base, underlying_price=money(spot), distance_usd=money(abs(spot - strike)),
                                     band_usd=money(band), trading_days_to_expiration=tdays,
                                     text="the underlying is within %s of the strike with %d trading day(s) left: "
                                          "whether this finishes in the money (and is exercised or assigned) may "
                                          "not be known until after the close" % (fmt_money_text(band), tdays)))
            expiring.append(row)
        primary = next((c for c in ITEM_CODES if c in row["codes"]), None)
        if primary is not None:
            item = {k: v for k, v in row.items() if k not in ("codes",)}
            item["risk"] = primary
            item["also"] = [c for c in row["codes"] if c != primary]
            item["stake"] = [{"code": c, "usd": money(v)} for c, v in stake]
            items.append(item)
        elif in_radar and not pinned and "EXPIRING_WORTHLESS" not in row["codes"]:
            quiet.append({"label": label, "account_last4": p["account_last4"],
                          "text": row.get("short_otm_text") or "no expiration risk found at the current price"})

    cash_summary = []
    for last4, acct in sorted(cash_by_account.items()):
        bp = buying_power.get(last4)
        cash_summary.append({
            "account_last4": last4, "cash_needed_usd": money(acct["cash_needed"]), "buying_power_usd": money(bp),
            "shortfall_usd": money(max(Decimal(0), acct["cash_needed"] - bp)) if bp is not None else None,
            "positions": acct["labels"],
        })
    stakes = []
    for it in items:
        for s in it["stake"]:
            stakes.append({"label": it["label"], "code": s["code"], "usd": s["usd"]})
    for w in worthless:
        if w.get("value_at_bid_usd") and Decimal(w["value_at_bid_usd"]) > 0:
            stakes.append({"label": w["label"], "code": "EXPIRING_WORTHLESS", "usd": w["value_at_bid_usd"]})
    stakes.sort(key=lambda s: Decimal(s["usd"]), reverse=True)
    if any(d["uncertain"] for d in divs.values() if d):
        _note_once(notes, "At least one dividend amount is unverified (see dividend_basis); treat early-assignment "
                          "results for it as rough.")
    notes.append("Moneyness uses the underlying price you passed; it can change by expiration.")
    notes.append("Spreads are read leg by leg here: an assignment on a short leg may be offset by exercising the long "
                 "leg, which needs action or auto-exercise.")
    return {
        "ok": True,
        "as_of": as_of.isoformat(),
        "radar_days": radar_days,
        "radar_end": radar_end.isoformat(),
        "items": items,
        "pin_risk": pin_risk,
        "worthless": worthless,
        "expiring": expiring,
        "quiet": quiet,
        "unknown": unknown,
        "cash_needs_by_account": cash_summary,
        "dollars_at_stake": stakes[:3],
        "counts": {"positions": len(positions), "in_radar": len(expiring), "items": len(items),
                   "unknown": len(unknown)},
        "calendar": "rh_time" if _rh is not None else "weekdays only (rh_time.py not found)",
        "notes": notes,
    }


OPS = {"run": op_run}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected one of %s" % (op, ", ".join(sorted(OPS))))
    if not isinstance(data, dict):
        return _err("BAD_INPUT", "", "input must be a JSON object")
    try:
        return OPS[op](data)
    except InputError as exc:
        return _err(exc.code, exc.field, exc.msg)


# ---------------------------------------------------------------------------------------------
# JSON Schemas and self-test
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_DEC = {"type": ["string", "number"]}
_DECN = {"type": ["string", "number", "null"]}
_M = {"type": ["string", "null"], "pattern": r"^-?\d+\.\d{2}$"}
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
_ITEM = {"type": "object", "required": ["symbol", "label", "risk", "also", "account_last4", "expiration", "dte"],
         "properties": {"symbol": _S, "label": _S, "risk": {"enum": list(ITEM_CODES)},
                        "also": {"type": "array", "items": _S}, "account_last4": _S, "expiration": _DATE,
                        "dte": {"type": "integer"}, "itm_by": _M, "extrinsic_bid": _M, "extrinsic_mid": _M,
                        "dividend": _M, "cash_needed_usd": _M, "buying_power_usd": _M, "shortfall_usd": _M,
                        "if_assigned": _S, "if_exercised": _S, "note": _S}}
SCHEMAS = {
    "run": {
        "input": {
            "type": "object", "required": ["as_of", "positions"], "additionalProperties": False,
            "properties": {
                "as_of": _DATE, "now": _S, "radar_days": {"type": ["integer", "string"]},
                "positions": {"type": "array", "items": {
                    "type": "object",
                    "required": ["account_last4", "symbol", "type", "side", "quantity", "strike", "expiration"],
                    "properties": {
                        "option_id": _S, "account_last4": _S, "symbol": _S, "chain_symbol": _S,
                        "underlying_symbol": _S, "type": {"enum": ["call", "put"]},
                        "side": {"enum": ["long", "short"]}, "quantity": _DEC, "strike": _DEC, "expiration": _DATE,
                        "bid": _DECN, "ask": _DECN, "underlying_type": {"enum": ["equity", "index"]},
                        "multiplier": _DEC, "sellout_datetime": _SN, "pending": {"type": "object"},
                    }}},
                "underlying": {"type": "object"}, "ex_dividends": {"type": "object"},
                "buying_power": {"type": "object"}, "shares_held": {"type": "object"},
            }},
        "output": {"anyOf": [{
            "type": "object",
            "required": ["ok", "as_of", "radar_days", "items", "pin_risk", "worthless", "expiring", "unknown",
                         "cash_needs_by_account", "dollars_at_stake", "notes"],
            "properties": {
                "ok": {"enum": [True]}, "as_of": _DATE, "radar_days": {"type": "integer"}, "radar_end": _DATE,
                "items": {"type": "array", "items": _ITEM}, "pin_risk": {"type": "array"},
                "worthless": {"type": "array"}, "expiring": {"type": "array"}, "quiet": {"type": "array"},
                "unknown": {"type": "array"}, "cash_needs_by_account": {"type": "array"},
                "dollars_at_stake": {"type": "array"}, "counts": {"type": "object"}, "calendar": _S,
                "notes": {"type": "array", "items": _S},
            }}, _ERR]},
    },
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
    """Minimal JSON Schema check (type, enum, pattern, properties, required, additionalProperties,
    items, anyOf) - enough to keep --schema honest in --selftest."""
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
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        errs.append("%s: does not match %s" % (path, schema["pattern"]))
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
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs.extend(schema_errors(item, schema["items"], "%s[%d]" % (path, i)))
    return errs


def full_schema():
    ops = {}
    for name, pair in SCHEMAS.items():
        ops[name] = {k: dict(v, **{"$schema": "https://json-schema.org/draft/2020-12/schema"}) for k, v in pair.items()}
    return {"script": SCRIPT, "version": VERSION, "ops": ops, "risk_codes": list(ITEM_CODES) + [
        "PIN_RISK", "EXPIRING_WORTHLESS"]}


SPEC_INPUT = {
    "as_of": "2026-11-16",
    "radar_days": 7,
    "positions": [
        {"account_last4": "X4F1", "symbol": "KO", "type": "call", "side": "short", "quantity": "1", "strike": "70",
         "expiration": "2026-11-20", "bid": "2.20", "ask": "2.30", "underlying_type": "equity"},
        {"account_last4": "X4F1", "symbol": "SPY", "type": "call", "side": "long", "quantity": "2", "strike": "650",
         "expiration": "2026-11-20", "bid": "21.40", "ask": "21.60", "underlying_type": "equity"},
    ],
    "underlying": {"KO": "72.05", "SPY": "671.20"},
    "ex_dividends": {"KO": {"ex_date": "2026-11-18", "amount": "0.53"}},
    "buying_power": {"X4F1": "2480.00"},
    "shares_held": {"X4F1": {"KO": "100"}},
}
EXAMPLES = [
    ("run", SPEC_INPUT,
     {"ok": True,
      "items": [{"symbol": "KO", "risk": "EARLY_ASSIGNMENT_BEFORE_EX_DIV", "itm_by": "2.05", "extrinsic_bid": "0.15",
                 "extrinsic_mid": "0.20", "dividend": "0.53", "if_assigned": "deliver 100 KO (covered by 100 held)"},
                {"symbol": "SPY", "risk": "AUTO_EXERCISE_CASH_NEED", "cash_needed_usd": "130000.00",
                 "buying_power_usd": "2480.00", "shortfall_usd": "127520.00"}],
      "pin_risk": [], "worthless": []}),
    ("run", {"as_of": "2026-11-18", "radar_days": 7, "positions": [
        {"account_last4": "X4F1", "symbol": "SPX", "type": "call", "side": "long", "quantity": "1", "strike": "6700",
         "expiration": "2026-11-20", "bid": "25.00", "ask": "26.00", "underlying_type": "index"}],
        "underlying": {"SPX": "6710.00"}},
     {"ok": True, "items": [{"risk": "INDEX_NO_MANUAL_EXERCISE", "also": ["PIN_RISK"],
                             "settlement_estimate_usd": "1000.00"}]}),
    ("run", {"as_of": "2026-11-16", "positions": [
        {"account_last4": "X4F1", "symbol": "AMD", "type": "call", "side": "long", "quantity": "1", "strike": "165",
         "expiration": "2026-11-20", "bid": "0.40", "ask": "0.45"}], "underlying": {"AMD": "161.40"}},
     {"ok": True, "items": [], "worthless": [{"symbol": "AMD", "value_at_bid_usd": "40.00"}]}),
    ("run", {"as_of": "2026-11-16", "positions": [
        {"account_last4": "X4F1", "symbol": "AMD", "type": "put", "side": "long", "quantity": "1", "strike": "170",
         "expiration": "2026-11-20", "bid": "8.60", "ask": "8.80"}], "underlying": {"AMD": "161.40"},
        "shares_held": {"X4F1": {"AMD": "12.5"}}},
     {"ok": True, "items": [{"risk": "LONG_PUT_EXERCISE_SHORT_STOCK", "short_shares_if_exercised": "87.5"}]}),
    ("run", {"as_of": "2026-11-16", "positions": [], "radar_days": 0},
     {"ok": False, "errors": [{"code": "BAD_VALUE", "field": "radar_days"}]}),
]


def _subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: expiry_risk.py run < input.json")))
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
