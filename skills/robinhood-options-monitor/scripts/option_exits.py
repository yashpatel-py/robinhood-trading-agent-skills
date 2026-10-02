#!/usr/bin/env python3
"""option_exits.py - check held option positions against the user's own exit rules.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: exits are where monitoring earns its keep - an entry can wait for the next scan,
but a stop that fires while nobody is looking cannot. The arithmetic is easy to get subtly wrong:
the sign flips for short positions, every contract multiplies by 100, a spread has to be judged as
one position (its short leg "losing" is the point of the trade), and the price you could exit at
now is the bid for a long and the ask for a short, not the mark. This script applies the user's
four rules (profit target, stop loss, time stop, maximum holding period) the same way every time
and builds a closing spec with a BLANK limit price. It never chooses a price or a quantity.

Rules (all optional; a rule that is UNSET is skipped and named in not_configured, never invented):
  PROFIT_TARGET  pnl_pct >= profit_target_pct
  STOP_LOSS      pnl_pct <= -stop_loss_pct
  TIME_STOP      days to expiration <= time_stop_dte
  MAX_HOLD       days held >= max_hold_days (skipped when the opening date is unknown)
P&L marks longs at the bid and shorts at the ask (what you could exit at now):
  pnl_usd = (exit - open) x multiplier x qty for longs, (open - exit) x multiplier x qty for shorts
  pnl_pct = pnl_usd / (open x multiplier x qty) x 100
Spreads are evaluated as one unit when you pass `groups`; long and short legs on the same underlying
in the same account that are not grouped (or confirmed `standalone`) are reported under
needs_grouping instead of being judged leg by leg.
The open price: Robinhood's `average_price` may be per share or per contract (x100). The unit is
settled by the opening fill (`open_fill_price_per_share`, from get_option_orders): the same trade, so
the right reading is within 2x of it however far the market has moved since. The current quote can't
settle it (a 20x move looks exactly like a 100x unit error), so without a fill an unknown unit is
never guessed: P&L stays blank and PROFIT_TARGET / STOP_LOSS land in rules_not_evaluated, while
TIME_STOP and MAX_HOLD are still applied. A stated unit that only the other reading fits against the
quote is held back the same way (AVG_PRICE_UNIT_CONFLICT) until a fill settles it.

Usage:
    python3 option_exits.py run < input.json > output.json
    python3 option_exits.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash. Prose twin: references/formulas.md.
"""

import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from functools import reduce
from math import gcd

VERSION = "2.0.0"
SCRIPT = "option_exits"
CENT = Decimal("0.01")
TENTH = Decimal("0.1")
RAW4 = Decimal("0.0001")
STANDARD_MULTIPLIER = Decimal(100)
X = "\u00d7"
# average_price unit check (see references/formulas.md). The two readings differ by the multiplier (100x).
FILL_BAND = (Decimal("0.5"), Decimal("2"))       # a reading matches the opening fill when reading / fill is inside
CONFLICT_BAND = (Decimal("0.1"), Decimal("10"))  # unit stated, no fill: hold back when only the OTHER unit fits
PRICE_RULES = ("PROFIT_TARGET", "STOP_LOSS")
STALE_MINUTES = 15
RULE_KEYS = ("profit_target_pct", "stop_loss_pct", "time_stop_dte", "max_hold_days")
RULE_CODES = {"profit_target_pct": "PROFIT_TARGET", "stop_loss_pct": "STOP_LOSS",
              "time_stop_dte": "TIME_STOP", "max_hold_days": "MAX_HOLD"}
UNSET_WORDS = ("", "UNSET", "<UNSET>")


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
        raise InputError("BAD_VALUE", field, "%s must be a decimal string like \"3.30\" (got %r)" % (field, value))
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


def parse_ts(value):
    """Parse an ISO 8601 timestamp with an offset or Z; return an aware UTC datetime or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("z", "Z")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    m = re.match(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)(\.\d+)?([+-]\d{2}:\d{2})$", text)
    if not m:
        return None
    frac = (m.group(2) or "")[:7]
    try:
        dt = datetime.fromisoformat(m.group(1).replace(" ", "T") + frac + m.group(3))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc)


def whole_qty(value, field):
    d = dec(value, field, positive=True)
    if d != d.to_integral_value():
        raise InputError("BAD_VALUE", field, "%s must be a whole number of contracts (got %s)" % (field, value))
    return int(d)


def money(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def price_str(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def pct_pair(d):
    if d is None:
        return None, None
    return float(d.quantize(TENTH, rounding=ROUND_HALF_UP)), str(d.quantize(RAW4, rounding=ROUND_HALF_UP))


def fmt_strike(d):
    text = format(d.normalize(), "f")
    return text


def is_unset(value):
    return value is None or (isinstance(value, str) and value.strip().upper() in UNSET_WORDS)


# ---------------------------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------------------------
def parse_rules(raw):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise InputError("BAD_VALUE", "rules", "rules is an object like {\"profit_target_pct\": \"50\"}")
    rules, not_configured = {}, []
    for key in RULE_KEYS:
        val = raw.get(key)
        if is_unset(val):
            not_configured.append(key)
            continue
        field = "rules." + key
        if key in ("profit_target_pct", "stop_loss_pct"):
            rules[key] = dec(val, field, positive=True)
        else:
            d = dec(val, field, minimum=Decimal(0) if key == "time_stop_dte" else Decimal(1))
            if d != d.to_integral_value():
                raise InputError("BAD_VALUE", field, "%s is a whole number of days" % key)
            rules[key] = int(d)
    epr = raw.get("exit_price_rule")
    exit_price_rule = None
    if not is_unset(epr):
        exit_price_rule = str(epr).strip().lower()
        if exit_price_rule not in ("bid", "mid"):
            raise InputError("BAD_VALUE", "rules.exit_price_rule", "exit_price_rule is \"bid\" or \"mid\" (or UNSET)")
    return rules, not_configured, exit_price_rule


# ---------------------------------------------------------------------------------------------
# Positions and quotes
# ---------------------------------------------------------------------------------------------
def parse_quote(q, where):
    if not isinstance(q, dict):
        raise InputError("BAD_VALUE", where, "a quote is {bid, ask, mark?, adjusted_mark?, prev_close?, ts?}")
    return {
        "bid": dec(q.get("bid"), where + ".bid", required=False, minimum=Decimal(0)),
        "ask": dec(q.get("ask"), where + ".ask", required=False, minimum=Decimal(0)),
        "mark": dec(q.get("mark"), where + ".mark", required=False, minimum=Decimal(0)),
        "adjusted_mark": dec(q.get("adjusted_mark"), where + ".adjusted_mark", required=False, minimum=Decimal(0)),
        "prev_close": dec(q.get("prev_close"), where + ".prev_close", required=False, minimum=Decimal(0)),
        "ts": q.get("ts"),
    }


def parse_min_ticks(raw, where):
    if not isinstance(raw, dict):
        return None
    above = dec(raw.get("above_tick"), where + ".above_tick", required=False, positive=True)
    below = dec(raw.get("below_tick"), where + ".below_tick", required=False, positive=True)
    cutoff = dec(raw.get("cutoff_price"), where + ".cutoff_price", required=False, minimum=Decimal(0))
    if above is None or below is None or cutoff is None:
        return None
    return {"above_tick": above, "below_tick": below, "cutoff_price": cutoff}


def parse_position(p, i):
    where = "positions[%d]" % i
    if not isinstance(p, dict):
        raise InputError("BAD_VALUE", where, "each position is an object")
    oid = p.get("option_id")
    if not isinstance(oid, str) or not oid.strip():
        raise InputError("MISSING_FIELD", where + ".option_id", "option_id (from get_option_positions) is required")
    typ = str(p.get("type", "")).strip().lower()
    side = str(p.get("side", "")).strip().lower()
    if typ not in ("call", "put"):
        raise InputError("BAD_VALUE", where + ".type",
                         "type must be call or put (the instrument's type from get_option_instruments)")
    if side not in ("long", "short"):
        raise InputError("BAD_VALUE", where + ".side",
                         "side must be long or short (the position's type from get_option_positions)")
    last4 = p.get("account_last4")
    if not isinstance(last4, str) or not re.match(r"^[A-Za-z0-9]{4}$", last4.strip()):
        raise InputError("BAD_VALUE", where + ".account_last4", "account_last4 is the last 4 characters of the account")
    symbol = str(p.get("symbol") or p.get("chain_symbol") or "").strip().upper()
    if not symbol:
        raise InputError("MISSING_FIELD", where + ".symbol", "symbol (the underlying / chain symbol) is required")
    opened_raw = p.get("opened_date")
    opened = None
    if not (opened_raw is None or (isinstance(opened_raw, str) and opened_raw.strip().lower() in ("", "unknown"))):
        opened = parse_date(opened_raw, where + ".opened_date")
    underlying_type = str(p.get("underlying_type") or "equity").strip().lower()
    if underlying_type not in ("equity", "index"):
        raise InputError("BAD_VALUE", where + ".underlying_type", "underlying_type is equity or index")
    unit = str(p.get("average_price_unit") or "unknown").strip().lower()
    if unit not in ("per_share", "per_contract", "unknown"):
        raise InputError("BAD_VALUE", where + ".average_price_unit",
                         "average_price_unit is per_share, per_contract or unknown")
    pending = {}
    if isinstance(p.get("pending"), dict):
        for k, v in sorted(p["pending"].items()):
            q = dec(v, "%s.pending.%s" % (where, k), required=False, minimum=Decimal(0))
            if q is not None and q > 0:
                pending[str(k)] = q
    return {
        "option_id": oid.strip(),
        "account_last4": last4.strip().upper(),
        "agentic": bool(p.get("agentic", False)),
        "symbol": symbol,
        "chain_symbol": str(p.get("chain_symbol") or symbol).strip().upper(),
        "type": typ,
        "side": side,
        "sign": 1 if side == "long" else -1,
        "quantity": whole_qty(p.get("quantity"), where + ".quantity"),
        "strike": dec(p.get("strike"), where + ".strike", positive=True),
        "expiration": parse_date(p.get("expiration"), where + ".expiration"),
        "avg_per_share": dec(p.get("avg_open_price_per_share"), where + ".avg_open_price_per_share",
                             required=False, minimum=Decimal(0)),
        "average_price": dec(p.get("average_price"), where + ".average_price", required=False),
        "average_price_unit": unit,
        "open_fill": dec(p.get("open_fill_price_per_share"), where + ".open_fill_price_per_share",
                         required=False, positive=True),
        "opened_date": opened,
        "underlying_type": underlying_type,
        "multiplier": (dec(p.get("multiplier"), where + ".multiplier", required=False, positive=True)
                       or STANDARD_MULTIPLIER),
        "multiplier_given": p.get("multiplier") is not None,
        "pending": pending,
        "min_ticks": parse_min_ticks(p.get("min_ticks"), where + ".min_ticks"),
    }


def reference_price(q):
    """A positive price to sanity-check the average-price unit against: (price, label) from the mark, then the
    bid/ask midpoint, then either side; (None, None) when the quote has no positive price."""
    for key in ("adjusted_mark", "mark"):
        if q.get(key) is not None and q[key] > 0:
            return q[key], "mark"
    bid, ask = q.get("bid"), q.get("ask")
    if bid is not None and ask is not None and ask > 0:
        mid = (bid + ask) / 2
        if mid > 0:
            return mid, "bid/ask midpoint"
    for key in ("ask", "bid"):
        if q.get(key) is not None and q[key] > 0:
            return q[key], key
    return None, None


def _in_band(r, band):
    return r is not None and band[0] <= r <= band[1]


def _unit_text(unit):
    return unit.replace("_", " ")


def _fill_matches(reading, fill):
    return fill is not None and reading > 0 and _in_band(reading / fill, FILL_BAND)


_HOW_TO_SETTLE = ("pass the unit the get_option_positions guide states, or open_fill_price_per_share from the opening "
                  "order in get_option_orders")


def resolve_open_price(pos, q):
    """Settle the per-share open price. Returns {"open", "basis", "warning", "problem"}: `open` is None and
    `problem` is (code, reason) when it cannot be settled without guessing. The current quote is only a sanity
    check: a 20x move and a 100x unit error look alike against it, so it never picks the unit on its own."""
    ref, ref_label = reference_price(q) if q else (None, None)
    fill, mult = pos["open_fill"], pos["multiplier"]

    def done(open_px, basis, warning=None):
        if warning is None and ref is not None and open_px > 0 and not _in_band(open_px / ref, CONFLICT_BAND):
            warning = ("AVG_PRICE_FAR_FROM_MARK", "open price $%s is %sx the current %s ($%s): a large move%s"
                       % (price_str(open_px), (open_px / ref).quantize(TENTH), ref_label, price_str(ref),
                          " (the unit is confirmed by the opening fill)" if "opening fill" in basis
                          else ", or a unit problem worth checking"))
        return {"open": open_px, "basis": basis, "warning": warning, "problem": None}

    def unresolved(code, reason):
        return {"open": None, "basis": None, "warning": None, "problem": (code, reason)}

    if pos["avg_per_share"] is not None:
        open_px = pos["avg_per_share"]
        if fill is not None and open_px > 0:
            if _fill_matches(open_px, fill):
                return done(open_px, "given per share (matches the opening fill $%s)" % price_str(fill))
            if _fill_matches(open_px / mult, fill):
                return unresolved("AVG_PRICE_UNIT_CONFLICT", (
                    "the per-share open price $%s is %sx the opening fill $%s: it looks like a per-contract amount. "
                    "P&L and the price rules are held back until the unit is confirmed"
                    % (price_str(open_px), format(mult.normalize(), "f"), price_str(fill))))
            return done(open_px, "given per share", (
                "OPEN_FILL_MISMATCH", "open price $%s differs from the opening fill $%s by more than 2x (fills at "
                "several prices?)" % (price_str(open_px), price_str(fill))))
        return done(open_px, "given per share")
    if pos["average_price"] is None:
        return unresolved("AVG_PRICE_MISSING", "no average price: pass average_price from get_option_positions")
    raw = abs(pos["average_price"])
    if raw == 0:
        return unresolved("AVG_PRICE_MISSING", "average price is zero (cost basis pending?): P&L and the price rules "
                                               "are not evaluated")
    cands = {"per_share": raw, "per_contract": raw / mult}
    other = {"per_share": "per_contract", "per_contract": "per_share"}
    unit = pos["average_price_unit"]
    readings = "$%s a share if it is per share, or $%s a share if it is per contract (%s / %s)" % (
        price_str(cands["per_share"]), price_str(cands["per_contract"]), format(raw.normalize(), "f"),
        format(mult.normalize(), "f"))
    by_fill = None
    if fill is not None:
        matches = [k for k in ("per_share", "per_contract") if _fill_matches(cands[k], fill)]
        by_fill = matches[0] if len(matches) == 1 else None
    fill_note = "" if fill is None or by_fill else "; the opening fill $%s matches neither reading" % price_str(fill)
    if by_fill is not None:
        if unit != "unknown" and unit != by_fill:
            return unresolved("AVG_PRICE_UNIT_CONFLICT", (
                "average_price %s was passed as %s, but the opening fill $%s matches the %s reading ($%s). P&L and "
                "the price rules are held back until the unit is confirmed"
                % (format(raw.normalize(), "f"), _unit_text(unit), price_str(fill), _unit_text(by_fill),
                   price_str(cands[by_fill]))))
        return done(cands[by_fill], "average_price %s (%s; matches the opening fill $%s)"
                    % (by_fill, "as stated" if unit != "unknown" else "unit from the opening fill", price_str(fill)))
    if unit == "unknown":
        return unresolved("AVG_PRICE_UNIT_AMBIGUOUS", (
            "the unit of average_price is not known: it reads as %s%s. The current quote can't tell a large move "
            "from a unit error, so P&L and the price rules are not evaluated; %s"
            % (readings, fill_note, _HOW_TO_SETTLE)))
    mismatch = None
    if fill is not None:
        mismatch = ("OPEN_FILL_MISMATCH", "the opening fill $%s matches neither reading of average_price %s (fills at "
                    "several prices?); the stated unit (%s) is used" % (price_str(fill), format(raw.normalize(), "f"),
                                                                        _unit_text(unit)))
    if ref is None:
        return done(cands[unit], "average_price %s (not cross-checked: no quote)" % unit, mismatch)
    ratios = {k: v / ref for k, v in cands.items()}
    if _in_band(ratios[unit], CONFLICT_BAND):
        return done(cands[unit], "average_price %s" % unit, mismatch)
    if _in_band(ratios[other[unit]], CONFLICT_BAND):
        return unresolved("AVG_PRICE_UNIT_CONFLICT", (
            "average_price read %s gives an open price of $%s against a current %s of $%s; read %s it gives $%s%s. "
            "A move that large and a unit error look alike, so P&L and the price rules are held back; pass "
            "open_fill_price_per_share from the opening order in get_option_orders to settle it"
            % (_unit_text(unit), price_str(cands[unit]), ref_label, price_str(ref), _unit_text(other[unit]),
               price_str(cands[other[unit]]), fill_note)))
    return done(cands[unit], "average_price %s" % unit, mismatch)


# ---------------------------------------------------------------------------------------------
# Units (single positions or user-confirmed groups)
# ---------------------------------------------------------------------------------------------
def contract_label(pos):
    return "%s %s %s%s" % (pos["symbol"], pos["expiration"].isoformat(), fmt_strike(pos["strike"]),
                           "C" if pos["type"] == "call" else "P")


def round_to_tick(price, ticks, direction):
    """Round a limit price to the contract's tick; direction 'down' when selling, 'up' when buying."""
    if price is None:
        return None, "no price"
    if ticks is None:
        return price.quantize(CENT, rounding=ROUND_HALF_UP), "rounded to cents (tick size not given)"
    tick = ticks["below_tick"] if price < ticks["cutoff_price"] else ticks["above_tick"]
    units = price / tick
    units = units.to_integral_value(rounding=ROUND_FLOOR if direction == "down" else ROUND_CEILING)
    return (units * tick).quantize(CENT, rounding=ROUND_HALF_UP), "rounded %s to the $%s tick" % (direction, tick)


def evaluate_unit(unit, rules, exit_price_rule, as_of, ctx):
    legs = unit["legs"]
    warnings = list(unit.get("warnings", []))
    value_open = Decimal(0)
    value_exit = Decimal(0)
    value_mark = Decimal(0)
    mark_ok = True
    day_change = Decimal(0)
    day_ok = True
    exit_bases = []
    open_bases = []
    open_problems = [(leg["pos"], leg["problem"]) for leg in legs if leg["problem"] is not None]
    open_known = not open_problems
    for leg in legs:
        pos, q, open_px = leg["pos"], leg["quote"], leg["open"]
        qty, mult, sign = pos["quantity"], pos["multiplier"], pos["sign"]
        exit_key = "bid" if sign > 0 else "ask"
        exit_px = q.get(exit_key)
        if exit_px is None:
            return None, ("QUOTE_MISSING", "%s: no %s in the quote" % (contract_label(pos), exit_key))
        leg["exit"] = exit_px
        exit_bases.append(exit_key)
        if open_px is not None:
            open_bases.append(leg["open_basis"])
            value_open += sign * open_px * qty * mult
        value_exit += sign * exit_px * qty * mult
        mark = q.get("adjusted_mark") if q.get("adjusted_mark") is not None else q.get("mark")
        if mark is None:
            mark_ok = False
        else:
            value_mark += sign * mark * qty * mult
        if q.get("mark") is None or q.get("prev_close") is None:
            day_ok = False
        else:
            day_change += sign * (q["mark"] - q["prev_close"]) * qty * mult
        if sign > 0 and exit_px == 0:
            warnings.append({"code": "ZERO_BID", "text": "%s has no bid right now: its exit value is $0.00 until a "
                             "bid appears" % contract_label(pos)})
        if q.get("bid") is not None and q.get("ask") is not None and q["bid"] > q["ask"]:
            warnings.append({"code": "CROSSED_QUOTE", "text": "%s: bid is above ask; the quote is unreliable"
                             % contract_label(pos)})
        ts = parse_ts(q.get("ts"))
        if ctx["now"] is not None and ts is not None and ctx["in_regular_session"] is True:
            age = (ctx["now"] - ts).total_seconds() / 60.0
            if age > STALE_MINUTES:
                warnings.append({"code": "STALE_QUOTE", "text": "%s quote is %d minutes old during the regular session"
                                 % (contract_label(pos), int(age))})
        for name, amount in pos["pending"].items():
            warnings.append({"code": "PENDING_ACTIVITY", "text": "%s: Robinhood reports %s %s; the position may be "
                             "changing (an order, exercise, assignment or expiration in progress)"
                             % (contract_label(pos), name, format(amount.normalize(), "f"))})
        if pos["multiplier"] != STANDARD_MULTIPLIER:
            warnings.append({"code": "ADJUSTED_CONTRACT",
                             "text": "%s has multiplier %s (an adjusted contract); P&L uses it, but check the "
                                     "deliverable in the app" % (contract_label(pos), pos["multiplier"])})
    if not open_known:
        value_open = None
    pnl = None if value_open is None else value_exit - value_open
    pnl_pct_d = None if not value_open else pnl / abs(value_open) * 100
    pnl_pct, pnl_raw = pct_pair(pnl_pct_d)
    mark_pnl = (value_mark - value_open) if (mark_ok and value_open is not None) else None
    mark_pct, _ = pct_pair(None if (mark_pnl is None or value_open == 0) else mark_pnl / abs(value_open) * 100)
    dte = min((leg["pos"]["expiration"] - as_of).days for leg in legs)
    expiration = min(leg["pos"]["expiration"] for leg in legs)
    opened = [leg["pos"]["opened_date"] for leg in legs]
    if all(o is not None for o in opened):
        first_open = min(opened)
        if first_open > as_of:
            raise InputError("BAD_DATE", "opened_date", "opened_date %s is after as_of %s" % (first_open, as_of))
        days_held = (as_of - first_open).days
    else:
        days_held = "unknown"

    fired, skipped = [], []
    if open_known:
        price_skip = ("OPEN_VALUE_ZERO", "open value is zero; percent P&L undefined")
        problem = None
    else:
        code = open_problems[0][1][0]
        price_skip = (code, "; ".join("%s: %s" % (contract_label(p), pr[1]) for p, pr in open_problems))
        problem = {"code": code, "reason": price_skip[1]}
    for key, rule in (("profit_target_pct", "PROFIT_TARGET"), ("stop_loss_pct", "STOP_LOSS")):
        if key not in rules:
            continue
        if pnl_pct_d is None:
            skipped.append({"rule": rule, "code": price_skip[0], "reason": price_skip[1]})
        elif (pnl_pct_d >= rules[key]) if rule == "PROFIT_TARGET" else (pnl_pct_d <= -rules[key]):
            fired.append(rule)
    if "time_stop_dte" in rules and dte <= rules["time_stop_dte"]:
        fired.append("TIME_STOP")
    if "max_hold_days" in rules:
        if days_held == "unknown":
            skipped.append({"rule": "MAX_HOLD", "code": "OPENING_DATE_UNKNOWN",
                            "reason": "opening date unknown (no filled opening order found in get_option_orders); "
                                      "days held is never estimated"})
        elif days_held >= rules["max_hold_days"]:
            fired.append("MAX_HOLD")

    single = len(legs) == 1
    first = legs[0]["pos"]
    row = {
        "kind": "single" if single else "group",
        "option_ids": [leg["pos"]["option_id"] for leg in legs],
        "account_last4": first["account_last4"],
        "agentic": first["agentic"],
        "symbol": first["symbol"],
        "label": unit["label"],
        "rules": fired,
        "pnl_pct": pnl_pct,
        "pnl_pct_raw": pnl_raw,
        "pnl_usd": money(pnl),
        "exit_value_basis": "/".join(sorted(set(exit_bases))),
        "open_value_usd": money(abs(value_open)) if value_open is not None else None,
        "exit_value_usd": money(abs(value_exit)),
        "open_price_basis": "; ".join(sorted(set(open_bases))),
        "mark_pnl_usd": money(mark_pnl),
        "mark_pnl_pct": mark_pct,
        "mark_basis": ("adjusted_mark_price" if any(leg["quote"].get("adjusted_mark") is not None for leg in legs)
                       else "mark_price") if mark_pnl is not None else None,
        "day_change_usd": money(day_change) if day_ok else None,
        "dte": dte,
        "expiration": expiration.isoformat(),
        "days_held": days_held,
        "rule_skips": skipped,
        "open_price_problem": problem,
        "warnings": warnings,
    }
    if single:
        pos, q = first, legs[0]["quote"]
        row["option_id"] = pos["option_id"]
        row["side"] = pos["side"]
        row["quantity"] = str(pos["quantity"])
        row["open_price"] = price_str(legs[0]["open"])
        row["exit_price"] = price_str(legs[0]["exit"])
    else:
        row["group_id"] = unit["group_id"]
        row["open_net"] = (price_str(abs(value_open) / (unit["unit_qty"] * first["multiplier"]))
                           if value_open is not None else None)
        row["open_net_direction"] = None if value_open is None else ("debit" if value_open > 0 else "credit")
    if fired:
        row.update(close_spec(unit, exit_price_rule))
    else:
        row["limit_price"] = None
    return row, None


def close_spec(unit, exit_price_rule):
    legs = unit["legs"]
    first = legs[0]["pos"]
    g = unit["unit_qty"]
    close_legs = []
    nat, mid_net = Decimal(0), Decimal(0)
    mids_ok = True
    for leg in legs:
        pos, q = leg["pos"], leg["quote"]
        ratio = pos["quantity"] // g
        close_legs.append({"option_id": pos["option_id"], "side": "sell" if pos["sign"] > 0 else "buy",
                           "position_effect": "close", "ratio_quantity": ratio})
        bid, ask = q.get("bid"), q.get("ask")
        if pos["sign"] > 0:
            nat += ratio * (bid if bid is not None else Decimal(0))
        else:
            nat -= ratio * (ask if ask is not None else Decimal(0))
        if bid is None or ask is None:
            mids_ok = False
        else:
            mid_net += pos["sign"] * ratio * (bid + ask) / 2
    spec = {
        "close_legs": close_legs,
        "quantity_held": str(g),
        "quantity": None,
        "default_quantity_text": "all %d held (shown, not applied: you choose the quantity)" % g,
        "type": "limit",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "chain_symbol": first["chain_symbol"],
        "underlying_type": first["underlying_type"],
        "limit_price": None,
        "limit_price_source": "blank: needs your limit price",
    }
    rule_price, rule_note = None, None
    if len(legs) == 1:
        pos, q = first, legs[0]["quote"]
        spec["close_leg"] = close_legs[0]
        bid, ask = q.get("bid"), q.get("ask")
        mid = (bid + ask) / 2 if (bid is not None and ask is not None) else None
        selling = pos["sign"] > 0
        spec["reference_prices"] = {"bid": price_str(bid), "ask": price_str(ask), "mid": price_str(mid),
                                    "natural": price_str(bid if selling else ask),
                                    "natural_text": "the %s: what a %s to close gets immediately"
                                    % ("bid" if selling else "ask", "sell" if selling else "buy")}
        if exit_price_rule == "bid":
            natural = bid if selling else ask
            if natural is not None and natural > 0:
                rule_price, rule_note = natural.quantize(CENT, rounding=ROUND_HALF_UP), (
                    "exit_price_rule bid: the natural side (%s)" % ("bid, selling" if selling else "ask, buying"))
            else:
                rule_note = "exit_price_rule bid: no %s to price at" % ("bid" if selling else "ask")
        elif exit_price_rule == "mid":
            if mid is not None and mid > 0:
                rule_price, how = round_to_tick(mid, pos["min_ticks"], "down" if selling else "up")
                rule_note = "exit_price_rule mid: midpoint %s" % how
            else:
                rule_note = "exit_price_rule mid: no two-sided quote"
    else:
        direction = None
        if mids_ok and mid_net != 0 and nat != 0 and (mid_net > 0) == (nat > 0):
            direction = "credit" if mid_net > 0 else "debit"
        elif mids_ok and mid_net != 0 and nat == 0:
            direction = "credit" if mid_net > 0 else "debit"
        spec["direction"] = direction
        if direction is None:
            spec["direction_note"] = ("the closing net is near zero or one-sided: decide debit or credit from the "
                                      "live quotes before any review")
        spec["reference_prices"] = {
            "natural_net": price_str(abs(nat)),
            "natural_net_direction": ("credit" if nat > 0 else "debit") if nat != 0 else None,
            "mid_net": price_str(abs(mid_net)) if mids_ok else None,
            "natural_text": "natural: sells at the bid, buys at the ask (worst case, reference only)",
        }
        ticks = next((leg["pos"]["min_ticks"] for leg in legs if leg["pos"]["min_ticks"]), None)
        if direction is not None and exit_price_rule == "bid" and nat != 0:
            rule_price, rule_note = abs(nat).quantize(CENT, rounding=ROUND_HALF_UP), (
                "exit_price_rule bid: the natural net (%s)" % direction)
        elif direction is not None and exit_price_rule == "mid" and mids_ok:
            rule_price, how = round_to_tick(abs(mid_net), ticks, "down" if direction == "credit" else "up")
            rule_note = "exit_price_rule mid: net midpoint %s" % how
        elif exit_price_rule:
            rule_note = "exit_price_rule %s: cannot price this spread from the quotes" % exit_price_rule
    if exit_price_rule:
        spec["rule_limit_price"] = price_str(rule_price) if rule_price is not None and rule_price > 0 else None
        spec["rule_limit_price_note"] = rule_note
        spec["rule_limit_price_provenance"] = "user_config" if spec["rule_limit_price"] else None
        spec["rule_limit_price_use"] = ("review only when the user asks; the limit on the ticket stays blank until "
                                        "then")
    if not first["agentic"]:
        spec["ticket_kind"] = "manual: this account is read-only to agents (no review possible)"
    else:
        spec["ticket_kind"] = "review_option_order in the Agentic account, only when the user asks"
    return spec


def build_units(positions, quotes, groups_raw, standalone, as_of):
    by_key = {}
    for pos in positions:
        key = (pos["account_last4"], pos["option_id"])
        if key in by_key:
            raise InputError("DUPLICATE_POSITION", "positions", "option_id %s appears twice in account %s (merge pages "
                             "before calling)" % (pos["option_id"], pos["account_last4"]))
        by_key[key] = pos
    grouped, units, skipped = set(), [], []
    for gi, g in enumerate(groups_raw or []):
        where = "groups[%d]" % gi
        if not isinstance(g, dict) or not isinstance(g.get("option_ids"), list) or len(g["option_ids"]) < 2:
            raise InputError("BAD_VALUE", where,
                             "a group is {group_id, option_ids: [2 to 4 ids], account_last4?, label?}")
        ids = [str(x).strip() for x in g["option_ids"]]
        if len(ids) > 4:
            raise InputError("BAD_VALUE", where + ".option_ids", "a group has at most 4 legs")
        want = str(g.get("account_last4") or "").strip().upper() or None
        legs = []
        for oid in ids:
            matches = [p for (acct, o), p in by_key.items() if o == oid and (want is None or acct == want)]
            if not matches:
                raise InputError("UNKNOWN_OPTION_ID", where + ".option_ids", "%s is not in positions" % oid)
            if len(matches) > 1:
                raise InputError("AMBIGUOUS_OPTION_ID", where + ".account_last4",
                                 "%s is held in more than one account; give the group's account_last4" % oid)
            key = (matches[0]["account_last4"], oid)
            if key in grouped:
                raise InputError("BAD_VALUE", where + ".option_ids", "%s is in two groups" % oid)
            grouped.add(key)
            legs.append(matches[0])
        if len({p["account_last4"] for p in legs}) != 1 or len({p["chain_symbol"] for p in legs}) != 1:
            raise InputError("BAD_VALUE", where, "every leg of a group must be in the same account and chain")
        units.append({"group_id": str(g.get("group_id") or "g%d" % (gi + 1)), "positions": legs,
                      "label": str(g.get("label") or " / ".join("%s %s" % ("+" if p["sign"] > 0 else "-",
                                                                           contract_label(p)) for p in legs))})
    singles = [p for p in positions if (p["account_last4"], p["option_id"]) not in grouped]
    buckets = {}
    for p in singles:
        buckets.setdefault((p["account_last4"], p["chain_symbol"]), []).append(p)
    needs_grouping, held_back = [], set()
    for (last4, chain), ps in sorted(buckets.items()):
        cands = [p for p in ps if p["option_id"] not in standalone]
        if any(p["sign"] > 0 for p in cands) and any(p["sign"] < 0 for p in cands):
            ids = [p["option_id"] for p in cands]
            held_back.update(ids)
            needs_grouping.append({
                "account_last4": last4, "chain_symbol": chain, "option_ids": ids,
                "legs": ["%s %s %s%d" % (p["side"], contract_label(p), X, p["quantity"]) for p in cands],
                "question": "Long and short %s options in the same account: are these one spread? If yes, pass them "
                            "as a group so the rules judge the spread; if they are separate trades, pass their ids in "
                            "standalone." % chain,
            })
    for p in singles:
        if p["option_id"] in held_back:
            continue
        label = "%s %s%d (%s)" % (contract_label(p), X, p["quantity"], p["side"])
        units.append({"group_id": None, "positions": [p], "label": label})
    ready = []
    for u in units:
        legs, problem = [], None
        for p in u["positions"]:
            if (p["expiration"] - as_of).days < 0:
                problem = ("EXPIRED_STILL_LISTED", "%s expired on %s but is still listed (expiration processing?)"
                           % (contract_label(p), p["expiration"].isoformat()))
                break
            q = quotes.get(p["option_id"])
            if q is None:
                problem = ("QUOTE_MISSING",
                           "%s: no quote (call get_option_quotes with its option_id)" % contract_label(p))
                break
            res = resolve_open_price(p, q)  # an unsettled open price blanks P&L; time rules still apply
            legs.append({"pos": p, "quote": q, "open": res["open"], "open_basis": res["basis"],
                         "warning": res["warning"], "problem": res["problem"]})
        if problem:
            skipped.append({"option_ids": [p["option_id"] for p in u["positions"]], "label": u["label"],
                            "code": problem[0], "reason": problem[1]})
            continue
        u["legs"] = legs
        u["unit_qty"] = reduce(gcd, [p["quantity"] for p in u["positions"]])
        u["warnings"] = [{"code": leg["warning"][0], "text": leg["warning"][1]} for leg in legs if leg["warning"]]
        ready.append(u)
    return ready, skipped, needs_grouping


def op_run(data):
    as_of = parse_date(data.get("as_of"), "as_of")
    rules, not_configured, exit_price_rule = parse_rules(data.get("rules"))
    raw_positions = data.get("positions")
    if not isinstance(raw_positions, list):
        raise InputError("MISSING_FIELD", "positions", "positions is a list (it may be empty)")
    positions = [parse_position(p, i) for i, p in enumerate(raw_positions)]
    raw_quotes = data.get("quotes") or {}
    if not isinstance(raw_quotes, dict):
        raise InputError("BAD_VALUE", "quotes", "quotes is an object keyed by option_id")
    quotes = {str(k): parse_quote(v, "quotes.%s" % k) for k, v in raw_quotes.items()}
    standalone = set(str(x) for x in (data.get("standalone") or []))
    now = None
    if data.get("now") is not None:
        now = parse_ts(data.get("now"))
        if now is None:
            raise InputError("BAD_VALUE", "now", "now must be ISO 8601 with an offset, e.g. 2026-11-16T20:05:00-05:00")
    irs = data.get("in_regular_session")
    ctx = {"now": now, "in_regular_session": irs if isinstance(irs, bool) else None}
    units, skipped, needs_grouping = build_units(positions, quotes, data.get("groups"), standalone, as_of)
    fired, watch = [], []
    for u in units:
        row, problem = evaluate_unit(u, rules, exit_price_rule, as_of, ctx)
        if problem:
            skipped.append({"option_ids": [p["option_id"] for p in u["positions"]], "label": u["label"],
                            "code": problem[0], "reason": problem[1]})
            continue
        (fired if row["rules"] else watch).append(row)
    fired.sort(key=lambda r: Decimal(r["exit_value_usd"]), reverse=True)
    rules_not_evaluated = [{"label": r["label"], "option_ids": r["option_ids"], "rule": s["rule"], "code": s["code"],
                            "reason": s["reason"]} for r in fired + watch for s in r["rule_skips"]]
    stake = [{"label": r["label"], "rules": r["rules"], "usd": r["exit_value_usd"],
              "what": "position value at the %s (what closing now would take in or cost)" % r["exit_value_basis"]}
             for r in fired[:3]]
    notes = [
        "P&L marks longs at the bid and shorts at the ask (what you could exit at now); mark_pnl uses Robinhood's "
        "mark (what the app shows). Fees are not included.",
        "Closing specs leave the limit price and quantity blank; nothing is reviewed or placed by this script.",
    ]
    if rules_not_evaluated:
        notes.append("rules_not_evaluated lists configured rules that could not be checked (an open price or opening "
                     "date that can't be settled without guessing): the result is incomplete, not clear.")
    if ctx["in_regular_session"] is False:
        notes.append("Outside the regular session: option quotes may be stale or wide, and rules evaluated on them can "
                     "change at the open.")
    if not rules:
        notes.append("No exit rules are configured: nothing can fire. Set [options.exits] to evaluate rules.")
    return {
        "ok": True,
        "as_of": as_of.isoformat(),
        "rules_applied": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in sorted(rules.items())},
        "exit_price_rule": exit_price_rule,
        "fired": fired,
        "watch": watch,
        "not_configured": not_configured,
        "skipped": skipped,
        "rules_not_evaluated": rules_not_evaluated,
        "needs_grouping": needs_grouping,
        "dollars_at_stake": stake,
        "counts": {"positions": len(positions), "evaluated": len(fired) + len(watch), "fired": len(fired),
                   "skipped": len(skipped), "rules_not_evaluated": len(rules_not_evaluated),
                   "needs_grouping": sum(len(n["option_ids"]) for n in needs_grouping)},
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
_PCT = {"type": ["number", "null"]}
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
_LEG = {"type": "object", "required": ["option_id", "side", "position_effect", "ratio_quantity"],
        "properties": {"option_id": _S, "side": {"enum": ["buy", "sell"]}, "position_effect": {"enum": ["close"]},
                       "ratio_quantity": {"type": "integer"}}}
_WARN = {"type": "array", "items": {"type": "object", "required": ["code", "text"],
                                    "properties": {"code": _S, "text": _S}}}
_ROW = {"type": "object",
        "required": ["kind", "option_ids", "account_last4", "symbol", "label", "rules", "pnl_pct", "pnl_pct_raw",
                     "pnl_usd", "exit_value_basis", "dte", "days_held", "limit_price", "rule_skips", "open_price_problem", "warnings"],
        "properties": {
            "kind": {"enum": ["single", "group"]}, "option_ids": {"type": "array", "items": _S},
            "option_id": _S, "group_id": _S, "account_last4": _S, "agentic": {"type": "boolean"}, "symbol": _S,
            "label": _S, "rules": {"type": "array", "items": {"enum": list(RULE_CODES.values())}},
            "pnl_pct": _PCT, "pnl_pct_raw": _SN, "pnl_usd": _M, "exit_value_basis": _S, "open_value_usd": _M,
            "exit_value_usd": _M, "mark_pnl_usd": _M, "mark_pnl_pct": _PCT, "day_change_usd": _M,
            "dte": {"type": "integer"}, "days_held": {"type": ["integer", "string"]}, "expiration": _DATE,
            "limit_price": {"type": "null"}, "close_leg": _LEG, "close_legs": {"type": "array", "items": _LEG},
            "direction": {"enum": ["credit", "debit", None]}, "warnings": _WARN,
            "open_price": _SN, "open_net": _SN, "open_net_direction": {"enum": ["credit", "debit", None]},
            "rule_skips": {"type": "array", "items": {"type": "object", "required": ["rule", "code", "reason"],
                                                      "properties": {"rule": _S, "code": _S, "reason": _S}}},
            "open_price_problem": {"anyOf": [{"type": "null"}, {"type": "object", "required": ["code", "reason"],
                                                                "properties": {"code": _S, "reason": _S}}]},
        }}
SCHEMAS = {
    "run": {
        "input": {
            "type": "object", "required": ["as_of", "positions"], "additionalProperties": False,
            "properties": {
                "as_of": _DATE, "now": _S, "in_regular_session": {"type": "boolean"},
                "rules": {"type": "object", "additionalProperties": False,
                          "properties": {"profit_target_pct": _DECN, "stop_loss_pct": _DECN, "time_stop_dte": _DECN,
                                         "max_hold_days": _DECN, "exit_price_rule": _SN}},
                "positions": {"type": "array", "items": {
                    "type": "object", "required": ["option_id", "account_last4", "symbol", "type", "side", "quantity",
                                                   "strike", "expiration"],
                    "properties": {
                        "option_id": _S, "account_last4": _S, "agentic": {"type": "boolean"}, "symbol": _S,
                        "chain_symbol": _S, "type": {"enum": ["call", "put"]}, "side": {"enum": ["long", "short"]},
                        "quantity": _DEC, "strike": _DEC, "expiration": _DATE, "avg_open_price_per_share": _DEC,
                        "average_price": _DEC, "average_price_unit": {"enum": ["per_share", "per_contract", "unknown"]},
                        "open_fill_price_per_share": _DEC,
                        "opened_date": _SN, "underlying_type": {"enum": ["equity", "index"]}, "multiplier": _DEC,
                        "pending": {"type": "object"}, "min_ticks": {"type": "object"},
                    }}},
                "quotes": {"type": "object"},
                "groups": {"type": "array", "items": {"type": "object", "required": ["option_ids"],
                                                      "properties": {"group_id": _S, "label": _S, "account_last4": _S,
                                                                     "option_ids": {"type": "array", "items": _S}}}},
                "standalone": {"type": "array", "items": _S},
            }},
        "output": {"anyOf": [{
            "type": "object",
            "required": ["ok", "as_of", "fired", "watch", "not_configured", "skipped", "rules_not_evaluated",
                         "needs_grouping", "dollars_at_stake", "notes"],
            "properties": {
                "ok": {"enum": [True]}, "as_of": _DATE, "rules_applied": {"type": "object"},
                "exit_price_rule": {"enum": ["bid", "mid", None]},
                "fired": {"type": "array", "items": _ROW}, "watch": {"type": "array", "items": _ROW},
                "not_configured": {"type": "array", "items": {"enum": list(RULE_KEYS)}},
                "skipped": {"type": "array", "items": {"type": "object", "required": ["option_ids", "code", "reason"]}},
                "rules_not_evaluated": {"type": "array", "items": {
                    "type": "object", "required": ["label", "option_ids", "rule", "code", "reason"]}},
                "needs_grouping": {"type": "array"}, "dollars_at_stake": {"type": "array"},
                "counts": {"type": "object"}, "notes": {"type": "array", "items": _S},
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
    return {"script": SCRIPT, "version": VERSION, "ops": ops}


_AMD = {"option_id": "o1", "account_last4": "X4F1", "agentic": True, "symbol": "AMD", "type": "call", "side": "long",
        "quantity": "1", "strike": "165", "expiration": "2026-11-27", "avg_open_price_per_share": "2.05",
        "opened_date": "2026-10-20", "chain_symbol": "AMD", "underlying_type": "equity"}
EXAMPLES = [
    ("run", {"as_of": "2026-11-16",
             "rules": {"profit_target_pct": "50", "stop_loss_pct": "40", "time_stop_dte": "7", "max_hold_days": "30"},
             "positions": [_AMD], "quotes": {"o1": {"bid": "3.30", "ask": "3.40", "ts": "2026-11-17T01:04:00Z"}}},
     {"ok": True, "fired": [{"option_id": "o1", "rules": ["PROFIT_TARGET"], "pnl_pct": 61.0, "pnl_pct_raw": "60.9756",
                             "pnl_usd": "125.00", "exit_value_basis": "bid", "dte": 11, "days_held": 27,
                             "close_leg": {"option_id": "o1", "side": "sell", "position_effect": "close",
                                           "ratio_quantity": 1}, "limit_price": None}],
      "watch": [], "not_configured": [], "skipped": []}),
    ("run", {"as_of": "2026-11-16", "rules": {"profit_target_pct": "50"},
             "positions": [dict(_AMD, option_id="s1", symbol="SPY", chain_symbol="SPY", strike="450", quantity="2",
                                avg_open_price_per_share="4.05", expiration="2026-12-18")],
             "quotes": {"s1": {"bid": "6.10", "ask": "6.25"}}},
     {"ok": True, "fired": [{"pnl_usd": "410.00", "pnl_pct": 50.6, "rules": ["PROFIT_TARGET"]}],
      "not_configured": ["stop_loss_pct", "time_stop_dte", "max_hold_days"]}),
    ("run", {"as_of": "2026-11-16", "rules": {"stop_loss_pct": "40", "max_hold_days": "30"},
             "positions": [dict(_AMD, option_id="k1", symbol="KO", chain_symbol="KO", side="short", strike="70",
                                avg_open_price_per_share="1.10", expiration="2026-11-20", opened_date=None)],
             "quotes": {"k1": {"bid": "2.20", "ask": "2.30"}}},
     {"ok": True, "fired": [{"rules": ["STOP_LOSS"], "pnl_usd": "-120.00", "pnl_pct": -109.1, "exit_value_basis": "ask",
                             "days_held": "unknown", "close_leg": {"side": "buy"}}],
      "skipped": []}),
    ("run", {"as_of": "2026-11-16", "positions": [dict(_AMD, avg_open_price_per_share=None, average_price="205.0000",
                                                       average_price_unit="unknown", open_fill_price_per_share="2.05")],
             "quotes": {"o1": {"bid": "3.30", "ask": "3.40", "mark": "3.35"}}, "rules": {"profit_target_pct": "50"}},
     {"ok": True, "fired": [{"pnl_usd": "125.00", "open_price": "2.05"}]}),
    ("run", {"as_of": "2026-11-16", "positions": [dict(_AMD, side="short", avg_open_price_per_share=None,
                                                       average_price="-10.0000", average_price_unit="unknown")],
             "quotes": {"o1": {"bid": "1.95", "ask": "2.05", "mark": "2.00"}},
             "rules": {"profit_target_pct": "50", "stop_loss_pct": "40"}},
     {"ok": True, "fired": [], "watch": [{"pnl_usd": None, "open_price": None, "rules": []}],
      "rules_not_evaluated": [{"rule": "PROFIT_TARGET", "code": "AVG_PRICE_UNIT_AMBIGUOUS"},
                              {"rule": "STOP_LOSS", "code": "AVG_PRICE_UNIT_AMBIGUOUS"}]}),
    ("run", {"as_of": "2026-11-16", "positions": [dict(_AMD, side="short", avg_open_price_per_share=None,
                                                       average_price="-10.0000", average_price_unit="unknown",
                                                       open_fill_price_per_share="0.10")],
             "quotes": {"o1": {"bid": "1.95", "ask": "2.05", "mark": "2.00"}},
             "rules": {"profit_target_pct": "50", "stop_loss_pct": "40"}},
     {"ok": True, "fired": [{"rules": ["STOP_LOSS"], "pnl_usd": "-195.00", "pnl_pct": -1950.0, "open_price": "0.10"}],
      "rules_not_evaluated": []}),
    ("run", {"as_of": "2026-11-16", "positions": [_AMD], "quotes": {}},
     {"ok": True, "fired": [], "skipped": [{"code": "QUOTE_MISSING"}],
      "not_configured": ["profit_target_pct", "stop_loss_pct", "time_stop_dte", "max_hold_days"]}),
    ("run", {"as_of": "2026-11-16", "positions": [dict(_AMD, quantity="1.5")], "quotes": {}},
     {"ok": False, "errors": [{"code": "BAD_VALUE", "field": "positions[0].quantity"}]}),
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
        clean = json.loads(json.dumps(inp))
        for p in clean.get("positions", []):
            for k in [k for k, v in p.items() if v is None]:
                del p[k]
        problems = schema_errors(clean, SCHEMAS[op]["input"])
        out = run(op, clean)
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: option_exits.py run < input.json")))
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
