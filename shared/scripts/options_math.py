#!/usr/bin/env python3
"""options_math.py - expiration payoff, breakevens and P&L for option structures.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: the numbers that decide whether an option trade is sane are mostly not returned
by any tool. "Required move" is the signed distance from the stock price to the breakeven. For a
debit position that is not yet past its breakeven it is the move the stock still needs, and on cheap
out-of-the-money contracts it is routinely a double-digit percentage within weeks. For a credit or
short position (short put, short call, credit spread), and for a debit position already past its
breakeven, it is the opposite: the cushion before the position starts losing. `profits_when` (above,
below, between, outside) and `required_move_meaning` (needed, cushion) say which, from the payoff. Spreads cap the gain as well as the loss, and a spread costing $0.80 on a $1.00
width risks $80 to make $20 - visible only when the ratio is stated. Every leg multiplies by the
order quantity (quantity=2 on an iron condor is 8 contracts). These formulas follow the owner's
tables in the original options workflow; formulas.md is the prose twin.

Ops:
  payoff  legs + quantity + underlying price -> structure, natural net price (buys at ask, sells at
          bid: worst case, analysis only, never a limit price), cost or credit, max loss, max gain,
          breakevens, required move and which side of the breakeven profits, bid/ask spread % per
          leg, risk/reward. Legs with different expirations get no breakevens or max gain; their max
          loss is the debit only for a two-leg debit calendar or diagonal whose long leg expires later
          and is at or further in the money, an upper bound (debit + adverse strike width) for other
          two-leg debit calendars and diagonals, unlimited when a short call is ever left uncovered,
          and otherwise not computed.
  pnl     open price + current bid/ask -> P&L marked conservatively (longs at the bid, shorts at the
          ask), x multiplier x quantity; the sign flips for shorts

Usage:
    python3 options_math.py payoff|pnl < input.json > output.json
    python3 options_math.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash.
"""

import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

VERSION = "2.0.0"
SCRIPT = "options_math"
CENT = Decimal("0.01")
TENTH = Decimal("0.1")
RAW = Decimal("1e-10")
STANDARD_MULTIPLIER = Decimal(100)
NATURAL_LABEL = ("worst-case natural price: buys at ask, sells at bid \u2014 analysis only, not your limit price")


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


def money(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def usd(d):
    """$10,000.00 for notes (JSON money fields stay plain decimal strings)."""
    whole, frac = money(d).lstrip("-").split(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return "%s$%s.%s" % ("-" if d < 0 else "", ",".join(groups), frac)


def price_str(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def pct(d):
    if d is None:
        return None, None
    return float(d.quantize(TENTH, rounding=ROUND_HALF_UP)), str(d.quantize(RAW, rounding=ROUND_HALF_UP))


def dec(value, field, required=True, minimum=None, positive=False):
    if value is None:
        if required:
            raise InputError("MISSING_FIELD", field, "%s is required" % field)
        return None
    if isinstance(value, bool):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string" % field)
    text = repr(value) if isinstance(value, float) else str(value).strip()
    if not re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)$", text):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string like \"6.30\" (got %r)" % (field, value))
    d = Decimal(text)
    if positive and d <= 0:
        raise InputError("BAD_VALUE", field, "%s must be greater than zero" % field)
    if minimum is not None and d < minimum:
        raise InputError("BAD_VALUE", field, "%s must be at least %s" % (field, minimum))
    return d


def pos_int(value, field):
    if isinstance(value, bool):
        raise InputError("BAD_VALUE", field, "%s must be a positive whole number" % field)
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str) and re.match(r"^\s*[1-9]\d*\s*$", value):
        return int(value)
    raise InputError("BAD_VALUE", field, "%s must be a positive whole number (as a string, e.g. \"2\")" % field)


def multiplier(data, field="multiplier"):
    mult = dec(data.get(field), field, required=False, positive=True)
    if mult is not None and mult != STANDARD_MULTIPLIER:
        raise InputError("ADJUSTED_CONTRACT", field,
                         "multiplier %s is not 100: an adjusted contract with a non-standard deliverable; these "
                         "formulas do not apply, check the deliverable in the app" % mult)
    return STANDARD_MULTIPLIER


# ---------------------------------------------------------------------------------------------
# payoff
# ---------------------------------------------------------------------------------------------
def parse_legs(raw_legs):
    if not isinstance(raw_legs, list) or not 1 <= len(raw_legs) <= 4:
        raise InputError("LEGS_1_TO_4", "legs", "give 1 to 4 legs")
    legs = []
    for i, leg in enumerate(raw_legs):
        where = "legs[%d]" % i
        if not isinstance(leg, dict):
            raise InputError("BAD_VALUE", where, "each leg is {type, side, strike, ratio_quantity, bid, ask}")
        typ = str(leg.get("type", "")).strip().lower()
        side = str(leg.get("side", "")).strip().lower()
        if typ not in ("call", "put"):
            raise InputError("BAD_VALUE", where + ".type", "type must be call or put")
        if side not in ("buy", "sell"):
            raise InputError("BAD_VALUE", where + ".side", "side must be buy or sell")
        ratio = leg.get("ratio_quantity", 1)
        if isinstance(ratio, bool) or not isinstance(ratio, int) or ratio < 1:
            raise InputError("BAD_VALUE", where + ".ratio_quantity", "ratio_quantity is a positive integer")
        legs.append({
            "type": typ, "side": side, "sign": 1 if side == "buy" else -1, "ratio": ratio,
            "strike": dec(leg.get("strike"), where + ".strike", positive=True),
            "bid": dec(leg.get("bid"), where + ".bid", required=False, minimum=Decimal(0)),
            "ask": dec(leg.get("ask"), where + ".ask", required=False, minimum=Decimal(0)),
            "expiration": str(leg["expiration"]).strip() if leg.get("expiration") else None,
        })
    return legs


def classify(legs):
    n = len(legs)
    exps = {l["expiration"] for l in legs if l["expiration"]}
    same_exp = len(exps) <= 1
    if n == 1:
        l = legs[0]
        return ("long_" if l["side"] == "buy" else "short_") + l["type"]
    if n == 2:
        a, b = legs
        if a["type"] == b["type"] and a["side"] != b["side"] and a["ratio"] == b["ratio"]:
            if a["strike"] == b["strike"] and len(exps) == 2:
                return "calendar"
            if a["strike"] != b["strike"] and same_exp:
                buy = a if a["side"] == "buy" else b
                sell = b if buy is a else a
                if a["type"] == "call":
                    return "debit_call_spread" if buy["strike"] < sell["strike"] else "credit_call_spread"
                return "debit_put_spread" if buy["strike"] > sell["strike"] else "credit_put_spread"
        return "custom"
    if n == 4 and same_exp and len({l["ratio"] for l in legs}) == 1:
        puts = [l for l in legs if l["type"] == "put"]
        calls = [l for l in legs if l["type"] == "call"]
        if len(puts) == 2 and len(calls) == 2:
            ps = [l for l in puts if l["side"] == "sell"]
            pb = [l for l in puts if l["side"] == "buy"]
            cs = [l for l in calls if l["side"] == "sell"]
            cb = [l for l in calls if l["side"] == "buy"]
            if len(ps) == len(pb) == len(cs) == len(cb) == 1:
                if pb[0]["strike"] < ps[0]["strike"] < cs[0]["strike"] < cb[0]["strike"]:
                    return "iron_condor"
    return "custom"


def payoff_per_unit(legs, s, net):
    """P&L per unit of quantity, per share, at expiration price s."""
    total = Decimal(0)
    for l in legs:
        intrinsic = max(Decimal(0), s - l["strike"]) if l["type"] == "call" else max(Decimal(0), l["strike"] - s)
        total += l["sign"] * l["ratio"] * intrinsic
    return total - net


def analyze_expiry(legs, net):
    points = sorted({Decimal(0)} | {l["strike"] for l in legs})
    values = [payoff_per_unit(legs, p, net) for p in points]
    right_slope = sum((l["sign"] * l["ratio"] for l in legs if l["type"] == "call"), 0)
    breakevens = []

    def add(x):
        if x > 0 and x not in breakevens:
            breakevens.append(x)

    for i in range(len(points) - 1):
        v0, v1 = values[i], values[i + 1]
        if v0 == 0:
            add(points[i])
        if v0 * v1 < 0:
            add(points[i] + (-v0) * (points[i + 1] - points[i]) / (v1 - v0))
    if values[-1] == 0:
        add(points[-1])
    if right_slope != 0 and values[-1] * right_slope < 0:
        add(points[-1] - values[-1] / right_slope)
    max_gain = None if right_slope > 0 else max(values)
    max_loss = None if right_slope < 0 else min(values)
    return sorted(breakevens), max_gain, max_loss


def profit_side(legs, net, breakevens):
    """Which side of the breakeven(s) makes money at expiration: above, below, between, outside or None.
    Read from the payoff itself (sampled inside each region), never from debit/credit."""
    if not breakevens or len(breakevens) > 2:
        return None
    lo, hi = breakevens[0], breakevens[-1]
    below = payoff_per_unit(legs, lo / 2, net)
    above = payoff_per_unit(legs, hi * 2 + 1, net)
    if len(breakevens) == 1:
        if above > 0 >= below:
            return "above"
        if below > 0 >= above:
            return "below"
        return None
    middle = payoff_per_unit(legs, (lo + hi) / 2, net)
    if middle > 0 and below <= 0 and above <= 0:
        return "between"
    if middle <= 0 and below > 0 and above > 0:
        return "outside"
    return None


def multi_expiration_risk(legs, net, scale):
    """Max loss for legs that expire on different dates -> (max_loss_usd or None, unlimited, basis, note).

    The payoff at the first expiration depends on the later legs' remaining time value, so only these cases
    get a figure:
      * a short call left uncovered at any point (by a nearer long leg expiring first, or by ratio): unlimited;
      * a two-leg debit calendar or diagonal (same type and ratio, the long leg expiring later) whose long leg
        is at or further in the money than the short leg: about the debit, if both legs are closed together
        at the first expiration;
      * the same with the long leg further out of the money: an upper bound of the debit plus the strike width,
        because at the first expiration the short leg can owe up to that much more than the long leg's
        intrinsic value (its remaining time value makes the real figure smaller);
    Anything else, including a short put left uncovered, is not computed and says why."""
    keys = [l["expiration"] for l in legs]
    if any(k is None or not _ISO_DAY.match(k) for k in keys):
        return None, False, None, ("max loss not computed: every leg needs an expiration date (YYYY-MM-DD) to "
                                   "order the legs in time")
    exps = sorted(set(keys))
    points = [None] + exps[:-1]  # before the first expiration, then just after each one but the last
    uncovered_call = uncovered_put = False
    for after in points:
        alive = [l for l in legs if after is None or l["expiration"] > after]
        if sum(l["sign"] * l["ratio"] for l in alive if l["type"] == "call") < 0:
            uncovered_call = True
        if sum(l["sign"] * l["ratio"] for l in alive if l["type"] == "put") < 0:
            uncovered_put = True
    if uncovered_call:
        return None, True, "unlimited", ("a short call is uncovered at some point (after a nearer long leg expires, "
                                         "or by ratio): max loss is unlimited")
    if uncovered_put:
        worst = sum((l["strike"] * l["ratio"] for l in legs if l["type"] == "put" and l["sign"] < 0), Decimal(0))
        return None, False, None, ("a short put is uncovered at some point (after a nearer long leg expires, or by "
                                   "ratio): the loss can reach about %s (strikes x 100 x contracts) plus the debit or "
                                   "minus the credit; max loss not computed" % usd(worst * scale))
    if len(legs) == 2 and net > 0:
        a, b = legs
        if a["type"] == b["type"] and a["side"] != b["side"] and a["ratio"] == b["ratio"]:
            long_leg = a if a["side"] == "buy" else b
            short_leg = b if long_leg is a else a
            if long_leg["expiration"] > short_leg["expiration"]:
                if long_leg["type"] == "call":
                    adverse = long_leg["strike"] - short_leg["strike"]
                else:
                    adverse = short_leg["strike"] - long_leg["strike"]
                if adverse <= 0:
                    return (money(net * scale), False, "debit",
                            "about the debit, if both legs are closed together at the first expiration (the long leg "
                            "expires later and is at or further in the money than the short leg)")
                return (money((net + adverse * long_leg["ratio"]) * scale), False, "upper_bound",
                        "an upper bound: the long leg is %s further out of the money, so at the first expiration the "
                        "short leg can owe up to that much more than the long leg is worth at intrinsic value; the "
                        "long leg's remaining time value makes the real loss smaller" % price_str(adverse))
    return None, False, None, ("max loss not computed for this multi-expiration structure; it can exceed the debit "
                               "(up to about the strike width plus the debit)")


def op_payoff(data):
    legs = parse_legs(data.get("legs"))
    qty = pos_int(data.get("quantity"), "quantity")
    mult = multiplier(data)
    spot = dec(data.get("underlying_price"), "underlying_price", positive=True)
    notes = []

    natural = Decimal(0)
    natural_ok = True
    for l in legs:
        px = l["ask"] if l["side"] == "buy" else l["bid"]
        if px is None:
            natural_ok = False
            break
        natural += l["sign"] * l["ratio"] * px
    natural = natural if natural_ok else None

    direction = data.get("direction")
    direction = str(direction).strip().lower() if direction is not None else None
    if direction not in (None, "debit", "credit"):
        raise InputError("BAD_VALUE", "direction", "direction must be debit or credit")
    user_price = dec(data.get("user_net_price"), "user_net_price", required=False, positive=True)
    if user_price is not None:
        if len(legs) == 1:
            derived = "debit" if legs[0]["side"] == "buy" else "credit"
            if direction and direction != derived:
                raise InputError("BAD_VALUE", "direction", "a single %s leg is a %s" % (legs[0]["side"], derived))
            direction = derived
        elif direction is None:
            raise InputError("MISSING_FIELD", "direction", "with 2+ legs say whether user_net_price is a debit or credit")
        net = user_price if direction == "debit" else -user_price
        basis = "user_net_price"
    elif natural is not None:
        net = natural
        basis = "natural"
        direction = "debit" if natural > 0 else "credit" if natural < 0 else "even"
    else:
        raise InputError("MISSING_FIELD", "legs", "give bid/ask on every leg (ask for buys, bid for sells) or "
                                                  "user_net_price")

    structure = classify(legs)
    scale = mult * qty
    contracts = [qty * l["ratio"] for l in legs]
    exps = {l["expiration"] for l in legs if l["expiration"]}
    if any(l["expiration"] is None for l in legs) and len(legs) > 1:
        notes.append("no expirations given; legs assumed to share one expiration")

    spreads = []
    for l in legs:
        if l["bid"] is None or l["ask"] is None or l["bid"] + l["ask"] == 0:
            spreads.append(None)
        else:
            mid = (l["bid"] + l["ask"]) / 2
            spreads.append(pct((l["ask"] - l["bid"]) / mid * 100)[0])

    out = {
        "ok": True,
        "structure": structure,
        "contracts_per_leg": [str(c) for c in contracts],
        "total_contracts": str(sum(contracts)),
        "natural_net": price_str(abs(natural)) if natural is not None else None,
        "natural_direction": (None if natural is None else "debit" if natural > 0 else "credit" if natural < 0 else "even"),
        "natural_label": NATURAL_LABEL,
        "price_basis": basis,
        "net_price": price_str(abs(net)),
        "direction": direction,
        "cost_usd": money(net * scale) if net > 0 else None,
        "credit_usd": money(-net * scale) if net < 0 else None,
        "max_loss_usd": None,
        "max_gain_usd": None,
        "max_loss_unlimited": False,
        "max_gain_unlimited": False,
        "breakevens": [],
        "required_move_pct": None,
        "required_move_pct_raw": None,
        "required_move_meaning": None,
        "profits_when": None,
        "breakeven_moves_pct": [],
        "spread_pct_each_leg": spreads,
        "risk_reward": None,
    }

    if len(exps) > 1:
        loss_usd, unlimited, basis, why = multi_expiration_risk(legs, net, scale)
        out["max_loss_usd"], out["max_loss_unlimited"], out["max_loss_basis"] = loss_usd, unlimited, basis
        notes.append("legs expire on different dates: the payoff depends on volatility at the first expiration, "
                     "so max gain and breakevens are not computed")
        notes.append(why)
        out["notes"] = notes
        return out

    breakevens, gain, loss = analyze_expiry(legs, net)
    out["max_gain_unlimited"] = gain is None
    out["max_loss_unlimited"] = loss is None
    out["max_gain_usd"] = money(gain * scale) if gain is not None else None
    out["max_loss_usd"] = money(-loss * scale) if loss is not None and loss < 0 else ("0.00" if loss is not None else None)
    out["breakevens"] = [price_str(b) for b in breakevens]
    moves = [pct((b - spot) / spot * 100) for b in breakevens]
    out["breakeven_moves_pct"] = [m[0] for m in moves]
    side = profit_side(legs, net, breakevens)
    out["profits_when"] = side
    if len(breakevens) == 1:
        out["required_move_pct"], out["required_move_pct_raw"] = moves[0]
        if side is not None:
            profitable_now = (spot > breakevens[0]) if side == "above" else (spot < breakevens[0])
            out["required_move_meaning"] = "cushion" if profitable_now else "needed"
            if profitable_now:
                notes.append("the position profits at the current price (it makes money %s %s at expiration): "
                             "required_move_pct is the cushion before it starts losing, not a move it needs"
                             % (side, price_str(breakevens[0])))
    elif len(breakevens) > 1:
        notes.append("more than one breakeven: the position profits %s them; see breakeven_moves_pct"
                     % (side if side in ("between", "outside") else "between (or outside)"))
    if gain is not None and loss is not None and loss < 0 and gain > 0:
        ratio = (gain / -loss).quantize(CENT, rounding=ROUND_HALF_UP)
        out["risk_reward"] = {"risk_usd": money(-loss * scale), "reward_usd": money(gain * scale),
                              "reward_per_dollar_risked": float(ratio)}
    if gain is not None and gain <= 0:
        notes.append("no expiration price makes money at this net price")
    if structure.endswith("_spread"):
        width = abs(legs[0]["strike"] - legs[1]["strike"])
        out["width"] = price_str(width)
    if structure == "short_call":
        notes.append("max loss is unlimited unless the call is covered by 100 shares per contract")
    if notes:
        out["notes"] = notes
    return out


# ---------------------------------------------------------------------------------------------
# pnl
# ---------------------------------------------------------------------------------------------
def op_pnl(data):
    positions = data.get("positions")
    if not isinstance(positions, list) or not positions:
        raise InputError("MISSING_FIELD", "positions", "give positions: [{side, quantity, open_price, bid, ask}]")
    basis_mode = str(data.get("basis", "conservative")).strip().lower()
    if basis_mode not in ("conservative", "mark"):
        raise InputError("BAD_VALUE", "basis", "basis is conservative (bid for longs, ask for shorts) or mark")
    rows = []
    total = Decimal(0)
    for i, p in enumerate(positions):
        where = "positions[%d]" % i
        if not isinstance(p, dict):
            raise InputError("BAD_VALUE", where, "each position is an object")
        side = str(p.get("side", "")).strip().lower()
        if side not in ("long", "short"):
            raise InputError("BAD_VALUE", where + ".side", "side must be long or short")
        qty = pos_int(p.get("quantity"), where + ".quantity")
        mult = multiplier(p)
        open_px = dec(p.get("open_price"), where + ".open_price", positive=True)
        if basis_mode == "mark":
            exit_px, basis = dec(p.get("mark"), where + ".mark", minimum=Decimal(0)), "mark"
        else:
            key = "bid" if side == "long" else "ask"
            exit_px, basis = dec(p.get(key), where + "." + key, minimum=Decimal(0)), key
        per_share = (exit_px - open_px) if side == "long" else (open_px - exit_px)
        pnl = per_share * mult * qty
        total += pnl
        p1, raw = pct(per_share / open_px * 100)
        rows.append({
            "id": p.get("id"),
            "side": side,
            "quantity": str(qty),
            "open_price": price_str(open_px),
            "exit_value_basis": basis,
            "exit_price": price_str(exit_px),
            "value_usd": money(exit_px * mult * qty),
            "pnl_usd": money(pnl),
            "pnl_pct": p1,
            "pnl_pct_raw": raw,
        })
    return {
        "ok": True,
        "positions": rows,
        "total_pnl_usd": money(total),
        "convention": ("longs marked at the bid and shorts at the ask (the price you could exit at now); "
                       "pnl = (exit - open) x 100 x qty for longs, (open - exit) x 100 x qty for shorts"
                       if basis_mode == "conservative" else "marked at mark_price (not an executable price)"),
    }


OPS = {"payoff": op_payoff, "pnl": op_pnl}


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
_M = {"type": ["string", "null"], "pattern": r"^-?\d+\.\d{2}$"}
_PCT = {"type": ["number", "null"]}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
STRUCTURES = ["long_call", "long_put", "short_call", "short_put", "debit_call_spread", "debit_put_spread",
              "credit_call_spread", "credit_put_spread", "iron_condor", "calendar", "custom"]
SCHEMAS = {
    "payoff": {
        "input": {"type": "object", "required": ["legs", "quantity", "underlying_price"], "additionalProperties": False,
                  "properties": {
                      "legs": {"type": "array", "items": {"type": "object", "required": ["type", "side", "strike"],
                                                          "properties": {"type": {"enum": ["call", "put"]},
                                                                         "side": {"enum": ["buy", "sell"]},
                                                                         "strike": _S, "ratio_quantity": {"type": "integer"},
                                                                         "bid": _S, "ask": _S, "expiration": _S,
                                                                         "option_id": _S}}},
                      "quantity": _S, "underlying_price": _S, "user_net_price": _S,
                      "direction": {"enum": ["debit", "credit"]}, "multiplier": _S}},
        "output": {"anyOf": [{"type": "object", "additionalProperties": False,
                              "required": ["ok", "structure", "contracts_per_leg", "natural_net", "natural_label",
                                           "cost_usd", "max_loss_usd", "max_gain_usd", "breakevens",
                                           "required_move_pct", "spread_pct_each_leg", "risk_reward"],
                              "properties": {
                                  "ok": {"enum": [True]}, "structure": {"enum": STRUCTURES},
                                  "contracts_per_leg": {"type": "array", "items": _S}, "total_contracts": _S,
                                  "natural_net": _SN, "natural_direction": _SN, "natural_label": _S,
                                  "price_basis": {"enum": ["user_net_price", "natural"]}, "net_price": _S,
                                  "direction": _SN, "cost_usd": _M, "credit_usd": _M, "max_loss_usd": _M,
                                  "max_gain_usd": _M, "max_loss_unlimited": {"type": "boolean"},
                                  "max_gain_unlimited": {"type": "boolean"},
                                  "breakevens": {"type": "array", "items": _S}, "required_move_pct": _PCT,
                                  "required_move_pct_raw": _SN, "breakeven_moves_pct": {"type": "array"},
                                  "required_move_meaning": {"enum": ["needed", "cushion", None]},
                                  "profits_when": {"enum": ["above", "below", "between", "outside", None]},
                                  "max_loss_basis": {"enum": ["debit", "upper_bound", "unlimited", None]},
                                  "spread_pct_each_leg": {"type": "array", "items": _PCT},
                                  "risk_reward": {"type": ["object", "null"]}, "width": _S,
                                  "notes": {"type": "array", "items": _S}}}, _ERR]},
    },
    "pnl": {
        "input": {"type": "object", "required": ["positions"], "additionalProperties": False, "properties": {
            "basis": {"enum": ["conservative", "mark"]},
            "positions": {"type": "array", "items": {"type": "object", "required": ["side", "quantity", "open_price"],
                                                     "properties": {"id": _S, "side": {"enum": ["long", "short"]},
                                                                    "quantity": _S, "open_price": _S, "bid": _S,
                                                                    "ask": _S, "mark": _S, "multiplier": _S}}}}},
        "output": {"anyOf": [{"type": "object", "additionalProperties": False,
                              "required": ["ok", "positions", "total_pnl_usd", "convention"],
                              "properties": {"ok": {"enum": [True]}, "total_pnl_usd": _M, "convention": _S,
                                             "positions": {"type": "array", "items": {
                                                 "type": "object",
                                                 "required": ["side", "exit_value_basis", "pnl_usd", "pnl_pct"],
                                                 "properties": {"pnl_usd": _M, "pnl_pct": _PCT,
                                                                "exit_value_basis": {"enum": ["bid", "ask", "mark"]}}}}}},
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
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs.extend(schema_errors(item, schema["items"], "%s[%d]" % (path, i)))
    return errs


def full_schema():
    ops = {}
    for name, pair in SCHEMAS.items():
        ops[name] = {k: dict(v, **{"$schema": "https://json-schema.org/draft/2020-12/schema"}) for k, v in pair.items()}
    return {"script": SCRIPT, "version": VERSION, "ops": ops, "structures": STRUCTURES}


EXAMPLES = [
    ("payoff", {"legs": [{"type": "call", "side": "buy", "strike": "165", "ratio_quantity": 1, "bid": "6.10",
                          "ask": "6.30", "expiration": "2026-12-18"}],
                "quantity": "1", "underlying_price": "161.40"},
     {"ok": True, "structure": "long_call", "contracts_per_leg": ["1"], "natural_net": "6.30", "cost_usd": "630.00",
      "max_loss_usd": "630.00", "max_gain_usd": None, "max_gain_unlimited": True, "breakevens": ["171.30"],
      "required_move_pct": 6.1, "spread_pct_each_leg": [3.2], "risk_reward": None}),
    ("payoff", {"legs": [{"type": "put", "side": "sell", "strike": "95", "bid": "1.00", "ask": "1.05"},
                         {"type": "put", "side": "buy", "strike": "90", "bid": "0.45", "ask": "0.50"},
                         {"type": "call", "side": "sell", "strike": "105", "bid": "1.10", "ask": "1.15"},
                         {"type": "call", "side": "buy", "strike": "110", "bid": "0.50", "ask": "0.55"}],
                "quantity": "2", "underlying_price": "100"},
     {"ok": True, "structure": "iron_condor", "contracts_per_leg": ["2", "2", "2", "2"], "total_contracts": "8",
      "credit_usd": "210.00", "max_gain_usd": "210.00", "max_loss_usd": "790.00", "breakevens": ["93.95", "106.05"]}),
    ("pnl", {"positions": [{"id": "SPY 450C", "side": "long", "quantity": "2", "open_price": "4.05", "bid": "6.10",
                            "ask": "6.25"}]},
     {"ok": True, "positions": [{"exit_value_basis": "bid", "pnl_usd": "410.00", "pnl_pct": 50.6}],
      "total_pnl_usd": "410.00"}),
    ("payoff", {"legs": [{"type": "call", "side": "buy", "strike": "165", "bid": "6.10", "ask": "6.30"}],
                "quantity": "1", "underlying_price": "161.40", "multiplier": "106"},
     {"ok": False, "errors": [{"code": "ADJUSTED_CONTRACT"}]}),
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: options_math.py payoff|pnl < input.json")))
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
