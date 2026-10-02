#!/usr/bin/env python3
"""options_screen.py - structure preflight and one screening pass for robinhood-options-screener.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: the user's criteria ARE the strategy. A screener that quietly skips a rule, reads
one chain page, treats a missing delta as "fine", sizes against mid prices or loosens a threshold to
fill a report turns the user's risk limits into decoration. This script applies every rule the
same way on every run, records why each contract was rejected, and never fills in a value: an
UNSET criterion stops the run. The formulas follow the owner's original options workflow; the
prose twin is references/formulas.md.

Ops:
  preflight  structure + account (+ optional funding) -> may this run continue, which rule failed,
             and the enrollment route (cash accounts go through limited margin before level 3)
  screen     criteria + entry + exits + account + underlyings + contracts + earnings ->
             candidates (cost, max loss/gain, breakeven, required move in the trade's direction or
             the cushion once past breakeven, spread %, account %,
             limit price by the user's entry rule, exit levels from the user's exit rules, and the
             review_option_order parameters minus the account number), rejects with reasons,
             counts by reason, what still has to be fetched, and the report's first line

Every cost gate uses the natural price (buy at the ask, sell at the bid): the worst case, never
mid/mid. Contracts missing delta or open interest are rejected (DELTA_OI_UNAVAILABLE), never passed.

Usage:
    python3 options_screen.py preflight|screen < input.json > output.json
    python3 options_screen.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash. Account numbers are never taken or printed.
"""

import json
import re
import sys
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

VERSION = "2.0.0"
SCRIPT = "options_screen"
CENT = Decimal("0.01")
TENTH = Decimal("0.1")
RAW = Decimal("1e-10")
HUNDRED = Decimal(100)
MULTIPLIER = Decimal(100)
UNSET = "UNSET"
OFF = "OFF"
STRUCTURES = ("long_call", "long_put", "debit_call_spread", "debit_put_spread")
SPREADS = ("debit_call_spread", "debit_put_spread")
SIDE_TYPE = {"long_call": "call", "long_put": "put", "debit_call_spread": "call", "debit_put_spread": "put"}
ACCOUNT_TYPES = ("cash", "margin", "limited_margin")
ENTRY_RULES = ("ask_each_time", "natural", "mid")
EARNINGS_POLICIES = ("avoid", "allow", "require")
SORT_KEYS = ("required_move_pct", "cost_usd", "dte", "spread_pct", "account_pct")
IV_SCALES = ("0-100", "0-1")
SCAN_FILTERS = ("price_band", "avg_volume", "allowlist")
NATURAL_LABEL = {
    "single": "worst-case natural price (the ask): analysis only",
    "spread": "worst-case natural price (long ask − short bid): analysis only",
}
REQUIREMENTS = {
    "long_call": {"legs": 1, "option_level": "option_level_2", "account_types": list(ACCOUNT_TYPES),
                  "retirement_allowed": True},
    "long_put": {"legs": 1, "option_level": "option_level_2", "account_types": list(ACCOUNT_TYPES),
                 "retirement_allowed": True},
    "debit_call_spread": {"legs": 2, "option_level": "option_level_3", "account_types": ["margin", "limited_margin"],
                          "retirement_allowed": False},
    "debit_put_spread": {"legs": 2, "option_level": "option_level_3", "account_types": ["margin", "limited_margin"],
                         "retirement_allowed": False},
}
SETTLEMENT_NOTE = ("cash account: option trades settle T+1; buying again with unsettled proceeds risks a "
                   "good-faith violation, and 5 in 12 months trigger a 90-day restriction")
ROUTE_LIMITED_MARGIN = ["get_limited_margin_upgrade_info", "user completes the limited-margin upgrade",
                        "re-fetch get_accounts"]

# Criteria keys: (kind, extra). kind: dec | int | enum | symbols. Spread widths are required only for
# spread structures; iv_rank_* accept "OFF"; the allow/block lists and sort_by are optional.
CRITERIA_KEYS = {
    "max_position_pct": ("dec", {"gt": 0, "le": 100}),
    "max_concurrent": ("int", {"ge": 1}),
    "max_cost_per_contract_usd": ("dec", {"gt": 0}),
    "reserve_cash_usd": ("dec", {"ge": 0}),
    "price_min": ("dec", {"gt": 0}),
    "price_max": ("dec", {"gt": 0}),
    "min_avg_volume": ("int", {"ge": 0}),
    "symbols_allowlist": ("symbols", {"optional": True}),
    "symbols_blocklist": ("symbols", {"optional": True}),
    "structure": ("enum", {"values": STRUCTURES}),
    "spread_width_min": ("dec", {"gt": 0, "spread_only": True, "off": True}),
    "spread_width_max": ("dec", {"gt": 0, "spread_only": True, "off": True}),
    "dte_min": ("int", {"ge": 0}),
    "dte_max": ("int", {"ge": 0}),
    "delta_min": ("dec", {"ge": 0, "le": 1}),
    "delta_max": ("dec", {"ge": 0, "le": 1}),
    "max_spread_pct": ("dec", {"gt": 0}),
    "min_open_interest": ("int", {"ge": 0}),
    "iv_rank_min": ("dec", {"ge": 0, "le": 100, "off": True}),
    "iv_rank_max": ("dec", {"ge": 0, "le": 100, "off": True}),
    "earnings_policy": ("enum", {"values": EARNINGS_POLICIES}),
    "earnings_buffer_days": ("int", {"ge": 0}),
    "scan_sessions": ("enum", {"values": ("regular_hours_only",)}),
    "sort_by": ("enum", {"values": SORT_KEYS, "optional": True}),
}
ENTRY_KEYS = {
    "entry_price_rule": ("enum", {"values": ENTRY_RULES}),
    "contracts_per_entry": ("int", {"ge": 1}),
}
EXIT_KEYS = {
    "profit_target_pct": ("dec", {"gt": 0}),
    # No upper bound (kitconfig has none either: the shared [options.exits] section also serves short options,
    # which can lose more than their premium). On this skill's long-only structures a stop at 100 or more can
    # fire only at a total loss; the exit plan says so (STOP_AT_TOTAL_LOSS) instead of refusing the config.
    "stop_loss_pct": ("dec", {"gt": 0}),
    "time_stop_dte": ("int", {"ge": 0}),
    "max_hold_days": ("int", {"ge": 1}),
}
# Reasons that mean "the data needed to decide was missing", not "the rule failed".
DATA_CODES = frozenset(["DELTA_OI_UNAVAILABLE", "QUOTE_UNAVAILABLE", "EARNINGS_UNKNOWN", "UNDERLYING_PRICE_UNAVAILABLE",
                        "AVG_VOLUME_UNAVAILABLE", "IV_RANK_UNAVAILABLE", "SHORT_LEG_DELTA_OI_UNAVAILABLE",
                        "SHORT_LEG_QUOTE_UNAVAILABLE"])
MIN_MAX = [("price_min", "price_max"), ("dte_min", "dte_max"), ("delta_min", "delta_max"),
           ("spread_width_min", "spread_width_max"), ("iv_rank_min", "iv_rank_max")]


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


# ---------------------------------------------------------------------------------------------
# Number and date helpers (Decimal on decimal strings throughout)
# ---------------------------------------------------------------------------------------------
_DEC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_INT_RE = re.compile(r"^[+-]?\d+$")


def to_dec(value):
    """Decimal from a decimal string, int or float; None when it is not a plain number."""
    if value is None or isinstance(value, bool):
        return None
    text = repr(value) if isinstance(value, float) else str(value).strip()
    if not _DEC_RE.match(text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def to_int(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INT_RE.match(value.strip()):
        return int(value.strip())
    return None


def money(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def pct(d):
    """(display number rounded to 0.1, full-precision string)."""
    if d is None:
        return None, None
    # "+ 0.0" turns a rounded -0.0 into 0.0; format "f" keeps the raw string out of E-notation.
    shown = float(d.quantize(TENTH, rounding=ROUND_HALF_UP)) + 0.0
    return shown, format(d.quantize(RAW, rounding=ROUND_HALF_UP), "f")


def num_str(d):
    """Decimal as a plain string: at least 2 places ("165.00", "0.80"), more only when present ("0.4512")."""
    if d is None:
        return None
    n = d.normalize()
    if n.as_tuple().exponent >= -2:
        return str(d.quantize(CENT))
    return format(n, "f")


def plain(d):
    """Decimal without trailing zeros or exponent ("165", "162.5", "0.3")."""
    return format(d.normalize(), "f")


def parse_date(value, field):
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}", value.strip()):
        raise InputError("BAD_DATE", field, "%s must start with YYYY-MM-DD (got %r)" % (field, value))
    try:
        return date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        raise InputError("BAD_DATE", field, "%s is not a real calendar date (got %r)" % (field, value)) from None


def parse_level(value):
    """option_level_N -> N; empty, None or option_level_0 -> 0; anything else -> None (unknown)."""
    if value is None:
        return 0
    text = str(value).strip().lower()
    if text in ("", "none", "null"):
        return 0
    m = re.match(r"^option_level_(\d+)$", text)
    return int(m.group(1)) if m else None


def fmt_label(sym, expiration, strike, typ):
    return "%s %s %s%s" % (sym, expiration, plain(strike), "C" if typ == "call" else "P")


# ---------------------------------------------------------------------------------------------
# Config parsing (defense in depth: kitconfig.py validates first; this re-checks what it uses)
# ---------------------------------------------------------------------------------------------
def _check_key(section, key, spec, value, structure):
    """Returns (parsed_value, status) where status is 'ok', 'unset', 'off' or an error message."""
    kind, extra = spec
    name = "%s.%s" % (section, key)
    if value is None or value == UNSET:
        if extra.get("optional"):
            return None, "ok"
        if extra.get("spread_only") and structure not in SPREADS:
            return None, "ok"
        return None, "unset"
    if value == OFF:
        if extra.get("off") and not (extra.get("spread_only") and structure in SPREADS):
            return OFF, "off"
        return None, "%s: OFF is not allowed here%s" % (
            name, "; set a width for a spread structure" if extra.get("spread_only") else "")
    if kind == "dec":
        d = to_dec(value)
        if d is None:
            return None, "%s: expected a decimal string like \"31.24\" (got %r)" % (name, value)
        if "gt" in extra and not d > extra["gt"]:
            return None, "%s: must be greater than %s" % (name, extra["gt"])
        if "ge" in extra and not d >= extra["ge"]:
            return None, "%s: must be at least %s" % (name, extra["ge"])
        if "le" in extra and not d <= extra["le"]:
            return None, "%s: must be at most %s" % (name, extra["le"])
        if "lt" in extra and not d < extra["lt"]:
            return None, "%s: must be less than %s" % (name, extra["lt"])
        return d, "ok"
    if kind == "int":
        i = to_int(value)
        if i is None:
            return None, "%s: expected a whole number as a string, e.g. \"7\" (got %r)" % (name, value)
        if "ge" in extra and i < extra["ge"]:
            return None, "%s: must be at least %d" % (name, extra["ge"])
        return i, "ok"
    if kind == "enum":
        if value not in extra["values"]:
            return None, "%s: must be one of %s (got %r)" % (name, ", ".join(extra["values"]), value)
        return value, "ok"
    if kind == "symbols":
        if not isinstance(value, list) or not all(isinstance(x, str) and re.match(r"^[A-Za-z0-9.\-]{1,15}$", x)
                                                  for x in value):
            return None, "%s: must be a list of ticker strings" % name
        return [x.strip().upper() for x in value], "ok"
    return None, "%s: unknown key type" % name


def parse_config(data):
    """Returns (cfg, unset, invalid). cfg holds parsed values keyed by bare key name."""
    criteria, entry, exits = data.get("criteria"), data.get("entry"), data.get("exits")
    for field, val in (("criteria", criteria), ("entry", entry), ("exits", exits)):
        if not isinstance(val, dict):
            raise InputError("MISSING_FIELD", field, "%s must be the [options.%s] section as an object" % (
                field, {"criteria": "criteria", "entry": "entry", "exits": "exits"}[field]))
    structure = criteria.get("structure")
    cfg, unset, invalid = {}, [], []
    for section, keys, node in (("options.criteria", CRITERIA_KEYS, criteria), ("options.entry", ENTRY_KEYS, entry),
                                ("options.exits", EXIT_KEYS, exits)):
        for key, spec in keys.items():
            parsed, status = _check_key(section, key, spec, node.get(key), structure)
            if status == "unset":
                unset.append("%s.%s" % (section, key))
            elif status in ("ok", "off"):
                cfg[key] = parsed
            else:
                invalid.append(status)
    for lo, hi in MIN_MAX:
        a, b = cfg.get(lo), cfg.get(hi)
        if isinstance(a, (Decimal, int)) and isinstance(b, (Decimal, int)) and a > b:
            invalid.append("options.criteria.%s (%s) is greater than options.criteria.%s (%s)" % (lo, a, hi, b))
    cfg.setdefault("symbols_allowlist", None)
    cfg.setdefault("symbols_blocklist", None)
    if cfg.get("sort_by") is None:
        cfg["sort_by"] = "required_move_pct"
    return cfg, unset, invalid


# ---------------------------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------------------------
def op_preflight(data):
    structure = data.get("structure")
    if structure not in STRUCTURES:
        raise InputError("BAD_VALUE", "structure", "structure must be one of %s" % ", ".join(STRUCTURES))
    acct = data.get("account")
    if not isinstance(acct, dict):
        raise InputError("MISSING_FIELD", "account", "give account: {type, option_level, retirement} from a fresh "
                                                     "get_accounts read of the Agentic account")
    atype = acct.get("type")
    if atype not in ACCOUNT_TYPES:
        raise InputError("BAD_VALUE", "account.type", "account.type must be cash, margin or limited_margin (got %r)"
                         % (atype,))
    level = parse_level(acct.get("option_level"))
    if level is None:
        raise InputError("BAD_VALUE", "account.option_level",
                         "option_level must look like option_level_2 (or be empty); got %r" % acct.get("option_level"))
    retirement = acct.get("retirement")
    if retirement not in (True, False, None):
        raise InputError("BAD_VALUE", "account.retirement", "retirement must be true, false or null (unknown)")
    agentic = acct.get("agentic_allowed")
    req = REQUIREMENTS[structure]
    need = int(req["option_level"][-1])
    is_spread = structure in SPREADS
    checks, route, notes = [], [], []
    stop = None

    def fail(code, msg):
        return {"code": code, "msg": msg}

    if agentic is False:
        checks.append({"check": "agentic_account", "pass": False,
                       "detail": "this account isn't accessible to the agent for orders; the screener simulates only "
                                 "in the Agentic account"})
        stop = stop or fail("NOT_AGENTIC_ACCOUNT", "run the screener against the Agentic account (agentic_allowed)")
    elif agentic is True:
        checks.append({"check": "agentic_account", "pass": True, "detail": "Agentic account"})

    level_ok = level >= need
    checks.append({"check": "options_level", "pass": level_ok,
                   "detail": "%s needs %s; the account has %s" % (
                       structure, req["option_level"], "option_level_%d" % level if level else "no options level")})
    type_ok = atype in req["account_types"]
    checks.append({"check": "account_type", "pass": type_ok,
                   "detail": "%s needs a %s account; this one is %s" % (
                       structure, " or ".join(req["account_types"]), atype)})
    if is_spread:
        if retirement is None:
            checks.append({"check": "retirement", "pass": None,
                           "detail": "unknown whether this is a retirement account; spreads are not available on "
                                     "retirement accounts through these tools"})
        else:
            checks.append({"check": "retirement", "pass": not retirement,
                           "detail": "retirement account" if retirement else "not a retirement account"})

    if is_spread and retirement is True:
        stop = stop or fail("RETIREMENT_ACCOUNT", "debit spreads are not available on retirement accounts through "
                                                  "these tools; a single-leg structure is the only option there")
    elif not type_ok:
        # Only spreads have an account-type requirement: a cash account must switch to limited margin first,
        # then re-read get_accounts, and only then apply for level 3 if the level is still short.
        route = list(ROUTE_LIMITED_MARGIN)
        if not level_ok:
            route.append("get_option_level_upgrade_info (only if the re-read level is still below %s)"
                         % req["option_level"])
        stop = stop or fail("ACCOUNT_TYPE", "%s needs a margin or limited-margin account; this is a cash account. "
                                            "Start with the limited-margin upgrade (no borrowing or leverage), then "
                                            "re-read the account before any level-3 application" % structure)
    elif not level_ok:
        route = ["get_option_level_upgrade_info"]
        stop = stop or fail("OPTIONS_LEVEL", "%s needs %s; the account has %s" % (
            structure, req["option_level"], "option_level_%d" % level if level else "no options access"))
    if is_spread and retirement is None and stop is None:
        stop = fail("RETIREMENT_UNKNOWN", "confirm the account is not a retirement account (brokerage account type "
                                          "from get_accounts, or [accounts].retirement in your config) before a "
                                          "spread run")

    funding = data.get("funding")
    if funding is not None:
        if not isinstance(funding, dict):
            raise InputError("BAD_VALUE", "funding", "funding is {buying_power_usd, max_cost_per_contract_usd, "
                                                     "contracts_per_entry, reserve_cash_usd}")
        bp = _need_dec(funding, "buying_power_usd", "funding.")
        cap = _need_dec(funding, "max_cost_per_contract_usd", "funding.")
        reserve = _need_dec(funding, "reserve_cash_usd", "funding.")
        n = to_int(funding.get("contracts_per_entry"))
        if n is None or n < 1:
            raise InputError("BAD_VALUE", "funding.contracts_per_entry", "contracts_per_entry is a whole number >= 1")
        need_usd = cap * n + reserve
        funded = bp >= need_usd
        checks.append({"check": "funding", "pass": funded,
                       "detail": "buying power %s vs needed %s (max_cost_per_contract_usd %s x %d contracts + "
                                 "reserve_cash_usd %s)" % (money(bp), money(need_usd), money(cap), n, money(reserve))})
        if not funded:
            stop = stop or fail("NOT_FUNDED", "buying power %s is below %s, the most one entry can cost plus your "
                                              "reserve" % (money(bp), money(need_usd)))
    else:
        checks.append({"check": "funding", "pass": None, "detail": "not checked: no funding block given"})

    if atype == "cash":
        notes.append(SETTLEMENT_NOTE)
    return {
        "ok": True,
        "allowed": stop is None,
        "structure": structure,
        "requirement": dict(req),
        "checks": checks,
        "route": route,
        "stop": stop,
        "notes": notes,
    }


def _need_dec(node, key, prefix=""):
    d = to_dec(node.get(key))
    if d is None:
        raise InputError("MISSING_FIELD" if node.get(key) is None else "BAD_VALUE", prefix + key,
                         "%s%s must be a decimal string" % (prefix, key))
    return d


# ---------------------------------------------------------------------------------------------
# screen
# ---------------------------------------------------------------------------------------------
class Contract(object):
    __slots__ = ("raw", "id", "symbol", "chain_symbol", "underlying_type", "type", "strike", "expiration", "bid",
                 "ask", "delta", "oi", "multiplier", "tradability", "state", "can_open", "min_ticks", "updated_at",
                 "dte", "label")


def parse_contracts(raw_list, as_of):
    if raw_list is None:
        raw_list = []
    if not isinstance(raw_list, list):
        raise InputError("BAD_VALUE", "contracts", "contracts must be a list")
    out, seen, dupes = [], set(), 0
    for i, c in enumerate(raw_list):
        where = "contracts[%d]" % i
        if not isinstance(c, dict):
            raise InputError("BAD_VALUE", where, "each contract is an object")
        oid = c.get("option_id")
        if not isinstance(oid, str) or not oid.strip():
            raise InputError("MISSING_FIELD", where + ".option_id",
                             "option_id (from get_option_instruments) is required")
        if oid in seen:
            dupes += 1
            continue
        seen.add(oid)
        k = Contract()
        k.raw, k.id = c, oid
        sym = c.get("symbol")
        if not isinstance(sym, str) or not sym.strip():
            raise InputError("MISSING_FIELD", where + ".symbol", "symbol (the underlying ticker) is required")
        k.symbol = sym.strip().upper()
        k.chain_symbol = (c.get("chain_symbol") or "").strip().upper() or None
        k.underlying_type = (c.get("underlying_type") or "").strip().lower() or None
        typ = str(c.get("type", "")).strip().lower()
        if typ not in ("call", "put"):
            raise InputError("BAD_VALUE", where + ".type", "type must be call or put")
        k.type = typ
        k.strike = to_dec(c.get("strike"))
        if k.strike is None or k.strike <= 0:
            raise InputError("BAD_VALUE", where + ".strike", "strike must be a positive decimal string")
        k.expiration = parse_date(c.get("expiration"), where + ".expiration")
        k.bid = to_dec(c.get("bid"))
        k.ask = to_dec(c.get("ask"))
        k.delta = to_dec(c.get("delta"))
        oi = c.get("open_interest")
        k.oi = to_int(oi) if oi is not None else None
        if oi is not None and k.oi is None:
            dec_oi = to_dec(oi)
            k.oi = int(dec_oi) if dec_oi is not None and dec_oi == dec_oi.to_integral_value() else None
        k.multiplier = to_dec(c.get("multiplier")) if c.get("multiplier") is not None else None
        k.tradability = (c.get("tradability") or None)
        k.state = (c.get("state") or None)
        k.can_open = c.get("can_open_position")
        k.min_ticks = c.get("min_ticks") if isinstance(c.get("min_ticks"), dict) else None
        k.updated_at = c.get("updated_at") if isinstance(c.get("updated_at"), str) else None
        k.dte = (k.expiration - as_of).days
        k.label = fmt_label(k.symbol, k.expiration.isoformat(), k.strike, k.type)
        out.append(k)
    return out, dupes


def mid_of(c):
    if c.bid is None or c.ask is None or c.ask <= 0 or c.bid < 0 or c.bid > c.ask:
        return None
    return (c.bid + c.ask) / 2


def spread_pct_of(c):
    m = mid_of(c)
    if m is None or m == 0:
        return None
    return (c.ask - c.bid) / m * HUNDRED


def tick_for(price, ticks):
    """Price increment for a single-leg limit from get_option_instruments min_ticks; None if unknown."""
    if not ticks:
        return None
    above, below = to_dec(ticks.get("above_tick")), to_dec(ticks.get("below_tick"))
    cutoff = to_dec(ticks.get("cutoff_price"))
    if above is None and below is None:
        return None
    if cutoff is None or cutoff == 0:
        if above is not None and below is not None and above != below:
            return None
        return above or below
    t = above if price >= cutoff else below
    return t if t and t > 0 else None


def round_to_tick(price, tick):
    units = (price / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return units * tick


def leg_liquidity_reasons(c, cfg, prefix=""):
    """Checks every leg must pass (long and short): tradable, standard multiplier, a usable quote,
    open interest present and above the floor, bid/ask spread within the cap."""
    r = []
    if c.multiplier is not None and c.multiplier != MULTIPLIER:
        r.append("%sNON_STANDARD_MULTIPLIER %s (adjusted contract; these formulas assume 100)"
                 % (prefix, num_str(c.multiplier)))
    if (c.tradability and str(c.tradability).lower() != "tradable") or (c.state and str(c.state).lower() != "active") \
            or c.can_open is False:
        why = []
        if c.tradability and str(c.tradability).lower() != "tradable":
            why.append("tradability %s" % c.tradability)
        if c.state and str(c.state).lower() != "active":
            why.append("state %s" % c.state)
        if c.can_open is False:
            why.append("chain is closing-only")
        r.append("%sNOT_TRADABLE %s" % (prefix, ", ".join(why)))
    if mid_of(c) is None:
        r.append("%sQUOTE_UNAVAILABLE bid %s / ask %s" % (prefix, c.raw.get("bid"), c.raw.get("ask")))
    else:
        sp = spread_pct_of(c)
        if sp is not None and sp > cfg["max_spread_pct"]:
            r.append("%sSPREAD_PCT %s > %s" % (prefix, pct(sp)[0], plain(cfg["max_spread_pct"])))
    if c.oi is None:
        r.append("%sDELTA_OI_UNAVAILABLE open_interest missing from the quote" % prefix)
    elif c.oi < cfg["min_open_interest"]:
        r.append("%sOPEN_INTEREST %d < %d" % (prefix, c.oi, cfg["min_open_interest"]))
    return r


def long_leg_reasons(c, cfg, exits):
    r = []
    if c.dte < cfg["dte_min"] or c.dte > cfg["dte_max"]:
        r.append("DTE_OUT_OF_RANGE %d not in %d-%d" % (c.dte, cfg["dte_min"], cfg["dte_max"]))
    elif c.dte <= exits["time_stop_dte"]:
        r.append("INSIDE_TIME_STOP DTE %d <= time_stop_dte %d (your time stop would fire on entry)"
                 % (c.dte, exits["time_stop_dte"]))
    r.extend(leg_liquidity_reasons(c, cfg))
    if c.delta is None:
        missing = "delta and open_interest" if c.oi is None else "delta"
        r = [x for x in r if not x.startswith("DELTA_OI_UNAVAILABLE")]
        r.append("DELTA_OI_UNAVAILABLE %s missing from the quote" % missing)
    else:
        ad = abs(c.delta)
        if ad < cfg["delta_min"] or ad > cfg["delta_max"]:
            r.append("DELTA_OUT_OF_RANGE |%s| not in %s-%s" % (num_str(c.delta).lstrip("-"), num_str(cfg["delta_min"]),
                                                               num_str(cfg["delta_max"])))
    return r


def _earnings_reports(entry):
    """Normalize one symbol's earnings input to (checked, [{date, timing, verified}])."""
    if entry is None:
        return False, []
    if not isinstance(entry, dict):
        raise InputError("BAD_VALUE", "earnings", "each earnings entry is {date, timing, verified} or {reports: [...]}")
    reports = entry.get("reports")
    if reports is None:
        reports = [entry] if entry.get("date") else []
    out = []
    for r in reports:
        if not isinstance(r, dict) or not r.get("date"):
            continue
        out.append({"date": parse_date(r.get("date"), "earnings.date"),
                    "timing": (str(r.get("timing")).lower() if r.get("timing") else None),
                    "verified": r.get("verified") is True})
    return True, out


class ScreenPass(object):
    """One screening pass. Each stage is a method so every rule can be read (and tested) on its own:
    guards (stop conditions) -> underlyings -> contracts (phase 1: every rule but earnings) ->
    earnings (phase 2, survivors only) -> candidates -> summary."""

    def __init__(self, data):
        self.data = data
        as_of_raw = data.get("as_of")
        if as_of_raw is None:
            raise InputError("MISSING_FIELD", "as_of", "as_of is the ET date of this run (now_et from rh_time.py "
                                                       "session)")
        self.as_of = parse_date(as_of_raw, "as_of")
        self.cfg, self.unset, self.invalid = parse_config(data)
        self.notes = []
        self.rejected = []
        self.rejected_unds = []
        self.counts = {}

    # -- stop conditions ------------------------------------------------------------------------
    def stopped(self, code, msg, fields=None):
        stop = {"code": code, "msg": msg}
        if fields:
            stop["fields"] = fields
        return {"ok": True, "status": "stopped", "stopped": stop, "report_status": "STOPPED",
                "status_line": "STOPPED: %s" % msg, "report_only": True, "report_only_reason": "the run stopped",
                "candidates": [], "rejected": [], "rejected_underlyings": [], "counts_by_reason": {}, "counts": {},
                "underlyings_passed": [], "next_fetch": {"chains": [], "earnings": []},
                "earnings_needed_for_cards": [], "sort": None, "dollars_at_stake": [], "notes": self.notes,
                "as_of": self.as_of.isoformat()}

    def guards(self):
        """Returns a stopped result, or None when the pass may run. Reads the account and IV-rank inputs."""
        cfg = self.cfg
        if self.unset:
            short = [u.split(".")[-1] for u in self.unset]
            return self.stopped("CRITERIA_UNSET", "%d %s UNSET: %s" % (
                len(self.unset), "criterion" if len(self.unset) == 1 else "criteria", ", ".join(short)), self.unset)
        if self.invalid:
            return self.stopped("CRITERIA_INVALID", "%d config %s: %s" % (
                len(self.invalid), "problem" if len(self.invalid) == 1 else "problems", "; ".join(self.invalid)))
        self.structure = cfg["structure"]
        self.exits = {k: cfg[k] for k in EXIT_KEYS}
        self.contracts_n = cfg["contracts_per_entry"]
        self.is_spread = self.structure in SPREADS
        self.side_type = SIDE_TYPE[self.structure]

        acct = self.data.get("account")
        if not isinstance(acct, dict):
            raise InputError("MISSING_FIELD", "account", "account is {total_value_usd, buying_power_usd, "
                                                         "open_option_positions}")
        self.total_value = to_dec(acct.get("total_value_usd"))
        self.bp = to_dec(acct.get("buying_power_usd"))
        self.open_pos = to_int(acct.get("open_option_positions"))
        self.account_type = acct.get("type")
        if self.total_value is None or self.total_value <= 0:
            return self.stopped("ACCOUNT_VALUE_UNKNOWN", "the Agentic account's total value is unknown or zero (read "
                                                         "get_portfolio); account % cannot be checked")
        if self.bp is None:
            return self.stopped("BUYING_POWER_UNKNOWN", "buying power is unknown (read get_portfolio buying_power)")
        if self.open_pos is None or self.open_pos < 0:
            return self.stopped("OPEN_POSITIONS_UNKNOWN", "open option positions are unknown (read "
                                                          "get_option_positions with nonzero=true, every page) so "
                                                          "max_concurrent cannot be checked")
        need_usd = cfg["max_cost_per_contract_usd"] * self.contracts_n + cfg["reserve_cash_usd"]
        if self.bp < need_usd:
            return self.stopped("NOT_FUNDED", "buying power %s is below %s (max_cost_per_contract_usd x "
                                              "contracts_per_entry + reserve_cash_usd)" % (money(self.bp),
                                                                                           money(need_usd)))

        unds_raw = self.data.get("underlyings") or {}
        if not isinstance(unds_raw, dict):
            raise InputError("BAD_VALUE", "underlyings", "underlyings is {SYMBOL: {price, avg_volume, iv_rank?}}")
        self.unds = {}
        for sym, u in unds_raw.items():
            if not isinstance(u, dict):
                raise InputError("BAD_VALUE", "underlyings.%s" % sym, "each underlying is {price, avg_volume, "
                                                                      "iv_rank?}")
            self.unds[str(sym).strip().upper()] = u

        self.iv_on = cfg["iv_rank_min"] != OFF or cfg["iv_rank_max"] != OFF
        self.iv_scale = None
        if self.iv_on:
            src = self.data.get("iv_rank_source")
            if not isinstance(src, dict) or not src.get("name") or src.get("kind") not in ("enum_filter",
                                                                                           "expression"):
                return self.stopped("IV_RANK_SOURCE_UNVERIFIED", "IV rank is set in your criteria but no verified "
                                                                 "IV-rank scanner filter or datapoint was found; the "
                                                                 "run stops rather than ignore a limit you set")
            self.iv_scale = src.get("scale")
            if self.iv_scale not in IV_SCALES:
                return self.stopped("IV_RANK_SCALE_UNKNOWN", "the IV-rank source %r does not say whether it runs "
                                                             "0-100 or 0-1; without that the limit cannot be applied"
                                    % src.get("name"))
            vals = [to_dec(u.get("iv_rank")) for u in self.unds.values() if to_dec(u.get("iv_rank")) is not None]
            if self.iv_scale == "0-1" and any(v > 1 for v in vals):
                return self.stopped("IV_RANK_SCALE_MISMATCH", "IV-rank values above 1 arrived from a source declared "
                                                              "as 0-1; check the datapoint's scale before applying "
                                                              "your limit")
            if self.iv_scale == "0-100" and len(vals) >= 5 and max(vals) <= 1:
                return self.stopped("IV_RANK_SCALE_MISMATCH", "every IV-rank value is at or below 1 from a source "
                                                              "declared as 0-100; it is probably a 0-1 fraction. "
                                                              "Check before applying your limit")
        return None

    # -- underlyings ----------------------------------------------------------------------------
    def check_underlyings(self):
        cfg = self.cfg
        applied = self.data.get("scan_filters_applied") or []
        if not isinstance(applied, list) or not all(a in SCAN_FILTERS for a in applied):
            raise InputError("BAD_VALUE", "scan_filters_applied", "scan_filters_applied lists the filters actually "
                                                                  "sent to preview_scan, from: %s"
                             % ", ".join(SCAN_FILTERS))
        allow = cfg["symbols_allowlist"] or []
        block = set(cfg["symbols_blocklist"] or [])
        vol_by_scan = False
        self.passed = []
        for sym in sorted(self.unds):
            u = self.unds[sym]
            r = []
            if sym in block:
                r.append("BLOCKLISTED on your symbols_blocklist")
            if allow and sym not in allow:
                r.append("NOT_IN_ALLOWLIST not on your symbols_allowlist")
            price = to_dec(u.get("price"))
            if price is None or price <= 0:
                r.append("UNDERLYING_PRICE_UNAVAILABLE")
            elif price < cfg["price_min"] or price > cfg["price_max"]:
                r.append("PRICE_OUT_OF_RANGE %s not in %s-%s" % (money(price), money(cfg["price_min"]),
                                                                 money(cfg["price_max"])))
            vol = to_dec(u.get("avg_volume"))
            if vol is None:
                if "avg_volume" in applied:
                    vol_by_scan = True  # enforced server-side by the filter built from the config this run
                else:
                    r.append("AVG_VOLUME_UNAVAILABLE")
            elif vol < cfg["min_avg_volume"]:
                r.append("AVG_VOLUME %s < %d" % (plain(vol), cfg["min_avg_volume"]))
            if self.iv_on:
                r.extend(self._iv_rank_reasons(u))
            if r:
                self.rejected_unds.append({"symbol": sym, "reasons": r})
            else:
                self.passed.append(sym)
        if vol_by_scan:
            self.notes.append("average volume was enforced by the scan's own filter; no column value came back to "
                              "re-check it for every symbol")

    def _iv_rank_reasons(self, u):
        iv = to_dec(u.get("iv_rank"))
        if iv is None:
            return ["IV_RANK_UNAVAILABLE"]
        iv100 = iv * HUNDRED if self.iv_scale == "0-1" else iv
        lo = self.cfg["iv_rank_min"] if self.cfg["iv_rank_min"] != OFF else None
        hi = self.cfg["iv_rank_max"] if self.cfg["iv_rank_max"] != OFF else None
        if (lo is not None and iv100 < lo) or (hi is not None and iv100 > hi):
            return ["IV_RANK_OUT_OF_RANGE %s not in %s-%s" % (pct(iv100)[0], plain(lo) if lo is not None else "0",
                                                              plain(hi) if hi is not None else "100")]
        return []

    # -- contracts, phase 1 ---------------------------------------------------------------------
    def reject(self, label, option_id, reasons, short_id=None):
        row = {"option_id": option_id, "label": label, "reasons": reasons}
        if short_id:
            row["short_option_id"] = short_id
        self.rejected.append(row)

    def account_gates(self, cost_per_unit, legs_n):
        """Capital and concentration rules, always at the natural price."""
        cfg, r = self.cfg, []
        cost_total = cost_per_unit * self.contracts_n
        if cost_per_unit > cfg["max_cost_per_contract_usd"]:
            r.append("COST_PER_CONTRACT %s > %s" % (money(cost_per_unit), money(cfg["max_cost_per_contract_usd"])))
        acct_pct = cost_total / self.total_value * HUNDRED
        if acct_pct > cfg["max_position_pct"]:
            r.append("ACCOUNT_PCT %s > %s" % (pct(acct_pct)[0], plain(cfg["max_position_pct"])))
        if self.bp - cost_total < cfg["reserve_cash_usd"]:
            r.append("RESERVE_CASH buying power %s - cost %s = %s < reserve %s" % (
                money(self.bp), money(cost_total), money(self.bp - cost_total), money(cfg["reserve_cash_usd"])))
        if self.open_pos + legs_n > cfg["max_concurrent"]:
            r.append("MAX_CONCURRENT %d open + %d new > %d" % (self.open_pos, legs_n, cfg["max_concurrent"]))
        return r

    def screen_contracts(self):
        """Phase 1: every rule except earnings. Returns survivors as {"legs": [...], "net": natural price}."""
        self.contracts, dupes = parse_contracts(self.data.get("contracts"), self.as_of)
        if dupes:
            self.notes.append("%d duplicate contract%s ignored (same option_id)" % (dupes, "" if dupes == 1 else "s"))
        fetched = self.data.get("fetched_chains")
        if fetched is None:
            self.fetched = {c.symbol for c in self.contracts}
        elif isinstance(fetched, list):
            self.fetched = {str(s).strip().upper() for s in fetched}
        else:
            raise InputError("BAD_VALUE", "fetched_chains", "fetched_chains is a list of symbols fully fetched")
        rejected_und = {x["symbol"] for x in self.rejected_unds}
        passed = set(self.passed)
        eligible, skipped = [], 0
        for c in self.contracts:
            if c.symbol in rejected_und:
                skipped += 1
            elif c.symbol not in passed:
                self.reject(c.label, c.id, ["NOT_IN_SCAN %s did not come from this run's scan" % c.symbol])
            elif c.type != self.side_type:
                self.reject(c.label, c.id, ["WRONG_TYPE %s contract for a %s structure (fetch type=%s only)"
                                            % (c.type, self.structure, self.side_type)])
            else:
                eligible.append(c)
        self.counts.update({"underlyings_in": len(self.unds), "underlyings_passed": len(self.passed),
                            "contracts_in": len(self.contracts), "contracts_skipped_underlying_rejected": skipped})
        return self._screen_spreads(eligible) if self.is_spread else self._screen_singles(eligible)

    def _screen_singles(self, eligible):
        survivors = []
        for c in eligible:
            r = long_leg_reasons(c, self.cfg, self.exits)
            if mid_of(c) is not None:
                r.extend(self.account_gates(c.ask * MULTIPLIER, 1))
            if r:
                self.reject(c.label, c.id, r)
            else:
                survivors.append({"legs": [c], "net": c.ask})
        return survivors

    def _screen_spreads(self, eligible):
        survivors, groups = [], {}
        for c in eligible:
            groups.setdefault(_chain_key(c), []).append(c)
        wmin, wmax = self.cfg["spread_width_min"], self.cfg["spread_width_max"]
        for c in eligible:
            r = long_leg_reasons(c, self.cfg, self.exits)
            if r:
                self.reject(c.label + " (as long leg)", c.id, r)
                continue
            peers = groups[_chain_key(c)]
            if c.type == "call":  # buy the lower strike, sell a higher one
                shorts = [s for s in peers if s.strike > c.strike and wmin <= s.strike - c.strike <= wmax]
            else:  # buy the higher strike, sell a lower one
                shorts = [s for s in peers if s.strike < c.strike and wmin <= c.strike - s.strike <= wmax]
            if not shorts:
                self.reject(c.label + " (as long leg)", c.id,
                            ["NO_SHORT_LEG_IN_WIDTH no %s strike %s %s within width %s-%s on %s in the fetched chain"
                             % (c.type, "above" if c.type == "call" else "below", plain(c.strike), money(wmin),
                                money(wmax), c.expiration.isoformat())])
                continue
            for s in sorted(shorts, key=lambda x: x.strike):
                pr = leg_liquidity_reasons(s, self.cfg, prefix="SHORT_LEG_")
                if mid_of(s) is not None:
                    net, width = c.ask - s.bid, abs(s.strike - c.strike)
                    if net <= 0:
                        pr.append("NON_POSITIVE_DEBIT long ask %s - short bid %s = %s (quote anomaly)" % (
                            num_str(c.ask), num_str(s.bid), num_str(net)))
                    elif net >= width:
                        pr.append("NO_MAX_GAIN net debit %s >= width %s: no expiration price makes money" % (
                            num_str(net), num_str(width)))
                    else:
                        pr.extend(self.account_gates(net * MULTIPLIER, 2))
                if pr:
                    self.reject(_spread_label([c, s]), c.id, pr, short_id=s.id)
                else:
                    survivors.append({"legs": [c, s], "net": c.ask - s.bid})
        return survivors

    # -- earnings, phase 2 ----------------------------------------------------------------------
    def apply_earnings(self, survivors):
        policy, buf = self.cfg["earnings_policy"], self.cfg["earnings_buffer_days"]
        earn_raw = self.data.get("earnings") or {}
        if not isinstance(earn_raw, dict):
            raise InputError("BAD_VALUE", "earnings", "earnings is {SYMBOL: {date, timing, verified}}")
        earnings = {str(sym).strip().upper(): _earnings_reports(e) for sym, e in earn_raw.items()}
        self.earnings_required, self.earnings_for_cards = set(), set()
        finals = []
        for sv in survivors:
            long_leg = sv["legs"][0]
            sym, exp = long_leg.symbol, long_leg.expiration
            checked, reports = earnings.get(sym, (False, []))
            start, end = self.as_of - timedelta(days=buf), exp + timedelta(days=buf)
            hits = [r for r in reports if start <= r["date"] <= end]
            upcoming = sorted((r for r in reports if r["date"] >= start), key=lambda r: r["date"])
            info = {"policy": policy, "checked": checked, "window": [start.isoformat(), end.isoformat()],
                    "in_window": [_earn_row(r) for r in hits], "next_listed": _earn_row(upcoming[0]) if upcoming
                    else None}
            flags, reasons = [], []
            if not checked:
                if policy == "allow":
                    self.earnings_for_cards.add(sym)
                    flags.append("EARNINGS_NOT_CHECKED")
                else:
                    self.earnings_required.add(sym)
                    reasons.append("EARNINGS_UNKNOWN no get_earnings_results read for %s; unknown is never clear"
                                   % sym)
            else:
                verified = [r for r in hits if r["verified"]]
                unverified = [r for r in hits if not r["verified"]]
                if policy == "avoid":
                    if verified:
                        reasons.append("EARNINGS_IN_WINDOW %s within %s..%s" % (_earn_txt(verified[0]), start, end))
                    elif unverified:
                        reasons.append("EARNINGS_UNVERIFIED_IN_WINDOW %s (unverified counts as a possible hit) "
                                       "within %s..%s" % (_earn_txt(unverified[0]), start, end))
                    elif not upcoming:
                        flags.append("EARNINGS_NONE_LISTED")
                elif policy == "require" and not verified:
                    if unverified:
                        reasons.append("EARNINGS_REQUIRE_UNVERIFIED %s is unverified; your policy requires earnings "
                                       "in the window" % _earn_txt(unverified[0]))
                    else:
                        reasons.append("EARNINGS_NOT_IN_WINDOW no report within %s..%s; your policy requires one"
                                       % (start, end))
                if any(r["date"] == exp and r["timing"] == "pm" for r in hits):
                    flags.append("EARNINGS_AFTER_EXPIRY_CLOSE")
            if reasons:
                if len(sv["legs"]) == 2:
                    self.reject(_spread_label(sv["legs"]), long_leg.id, reasons, short_id=sv["legs"][1].id)
                else:
                    self.reject(long_leg.label, long_leg.id, reasons)
                continue
            sv["earnings"], sv["flags"] = info, flags
            finals.append(sv)
        return finals

    # -- candidates and the report --------------------------------------------------------------
    def session(self):
        in_session = self.data.get("in_regular_session")
        if in_session is True:
            return False, None
        if in_session is False:
            return True, ("outside the regular session (scan_sessions = regular_hours_only): option quotes outside "
                          "regular hours are wide and often stale, so no tickets")
        return True, "session unknown (run rh_time.py session): no tickets until it is known"

    def summarize(self, candidates, report_only, ro_reason):
        counts_by_reason = {}
        for row in self.rejected + self.rejected_unds:
            for reason in row["reasons"]:
                counts_by_reason[_code(reason)] = counts_by_reason.get(_code(reason), 0) + 1
        # A reject whose every reason is missing data was not evaluated, only excluded: it is not a "no".
        unevaluated = [row for row in self.rejected if all(_code(r) in DATA_CODES for r in row["reasons"])]
        unevaluated_unds = [row for row in self.rejected_unds if all(_code(r) in DATA_CODES for r in row["reasons"])]
        self.counts.update({"contracts_rejected": len(self.rejected), "candidates": len(candidates),
                            "contracts_unevaluated": len(unevaluated),
                            "underlyings_unevaluated": len(unevaluated_unds)})
        chains_needed = sorted(s for s in self.passed if s not in self.fetched)
        earnings_needed = sorted(self.earnings_required)
        dollars = [{"label": cd["label"], "max_loss_usd": cd["max_loss_usd"]}
                   for cd in sorted(candidates, key=lambda x: -Decimal(x["max_loss_usd"]))[:3]]
        n_contracts = len(self.contracts)
        if chains_needed or earnings_needed:
            status, report_status = "incomplete", "UNKNOWN"
            missing = []
            if chains_needed:
                missing.append("chains not fetched for %s" % ", ".join(chains_needed))
            if earnings_needed:
                missing.append("earnings not read for %s" % ", ".join(earnings_needed))
            line = "UNKNOWN: data incomplete (%s) · Dollars at stake: none found" % "; ".join(missing)
        elif candidates:
            status, report_status = "candidates", "POSSIBLE"
            line = ("POSSIBLE: %d candidate%s matched your criteria (nothing placed) · Dollars at stake: %s "
                    "(max loss per entry at the natural price)" % (
                        len(candidates), _plural(len(candidates)),
                        ", ".join("$%s %s" % (d["max_loss_usd"], d["label"]) for d in dollars)))
        elif unevaluated or unevaluated_unds:
            status, report_status = "no_candidates", "UNKNOWN"
            gaps = sorted({_code(r) for row in unevaluated + unevaluated_unds for r in row["reasons"]})
            parts = []
            if unevaluated:
                parts.append("%d of %d contract%s could not be checked" % (len(unevaluated), n_contracts,
                                                                            _plural(n_contracts)))
            if unevaluated_unds:
                parts.append("%d underlying%s could not be checked" % (len(unevaluated_unds),
                                                                        _plural(len(unevaluated_unds))))
            line = "UNKNOWN: 0 candidates; %s (%s) · Dollars at stake: none found" % (" and ".join(parts),
                                                                                           ", ".join(gaps))
        else:
            status, report_status = "no_candidates", "NO ACTION"
            line = "NO ACTION: 0 of %d contract%s passed your criteria · Dollars at stake: none found" % (
                n_contracts, _plural(n_contracts))
        if candidates:
            for rows, noun in ((unevaluated, "contract"), (unevaluated_unds, "underlying")):
                if rows:
                    self.notes.append("%d %s%s could not be checked for missing data (%s); %s excluded, not cleared"
                                      % (len(rows), noun, _plural(len(rows)),
                                         ", ".join(sorted({_code(r) for row in rows for r in row["reasons"]})),
                                         "it is" if len(rows) == 1 else "they are"))
        if self.account_type == "cash":
            self.notes.append(SETTLEMENT_NOTE)
        self.notes.append("each contract lists every rule it failed, so the reason counts can add up to more than the "
                          "number of rejected contracts; earnings are checked only for contracts that passed every "
                          "other rule")
        quote_times = sorted(t for cd in candidates for t in cd.get("quote_times", []) if t)
        sort_by = self.cfg["sort_by"]
        return {
            "ok": True,
            "status": status,
            "report_status": report_status,
            "status_line": line,
            "stopped": None,
            "report_only": report_only,
            "report_only_reason": ro_reason,
            "as_of": self.as_of.isoformat(),
            "structure": self.structure,
            "candidates": candidates,
            "rejected": self.rejected,
            "rejected_underlyings": self.rejected_unds,
            "counts_by_reason": dict(sorted(counts_by_reason.items(), key=lambda kv: (-kv[1], kv[0]))),
            "counts": self.counts,
            "underlyings_passed": self.passed,
            "next_fetch": {"chains": chains_needed, "earnings": earnings_needed},
            "earnings_needed_for_cards": sorted(self.earnings_for_cards),
            "sort": {"by": sort_by, "disclosure": _sort_disclosure(sort_by)},
            "dollars_at_stake": dollars,
            "oldest_quote_at": quote_times[0] if quote_times else None,
            "notes": self.notes,
        }

    def run(self):
        stop = self.guards()
        if stop is not None:
            return stop
        self.check_underlyings()
        finals = self.apply_earnings(self.screen_contracts())
        report_only, ro_reason = self.session()
        candidates = [build_candidate(sv, self.cfg, self.exits, self.contracts_n, self.unds, self.total_value,
                                      self.as_of, report_only, self.is_spread) for sv in finals]
        candidates.sort(key=lambda cd: _sort_key(cd, self.cfg["sort_by"]))
        for i, cd in enumerate(candidates, 1):
            cd["rank"] = i
        return self.summarize(candidates, report_only, ro_reason)


def op_screen(data):
    return ScreenPass(data).run()


def _chain_key(c):
    """Legs of one spread share the underlying, the chain (adjusted chains differ), expiration and type."""
    return (c.symbol, c.chain_symbol or c.symbol, c.expiration, c.type)


def _plural(n):
    return "" if n == 1 else "s"


def _earn_row(r):
    return {"date": r["date"].isoformat(), "timing": r["timing"], "verified": r["verified"]}


def _code(reason):
    return reason.split(" ", 1)[0]


def _earn_txt(r):
    return "%s%s" % (r["date"].isoformat(), (" " + r["timing"]) if r["timing"] else "")


def _spread_label(legs):
    c, s = legs
    return "%s %s %s/%s%s debit %s spread" % (c.symbol, c.expiration.isoformat(), plain(c.strike),
                                              plain(s.strike), "C" if c.type == "call" else "P", c.type)


def _sort_disclosure(key):
    what = {
        "required_move_pct": ("the move the stock still needs, in the trade's direction, to reach breakeven by "
                              "expiration, smallest first; candidates already past breakeven come first, the one "
                              "that can absorb the largest move against it first"),
        "cost_usd": "cost per entry at the natural price, lowest first",
        "dte": "days to expiration, fewest first",
        "spread_pct": "bid/ask spread % (widest leg), tightest first",
        "account_pct": "share of account value per entry, smallest first",
    }[key]
    return ("sorted by %s. This is a disclosed ordering, not a ranking of quality; change sort_by in your config "
            "(%s)" % (what, ", ".join(SORT_KEYS)))


def _sort_key(cd, key):
    if key == "required_move_pct":
        # The move still needed in the trade's direction (0 once past breakeven), never the size of the
        # signed price move: abs() would rank a spread that can absorb a 2.2% drop behind one that
        # still needs a 0.7% rise. Past-breakeven ties go to the larger cushion.
        raw = cd.get("move_needed_pct_raw")
        cushion = cd.get("breakeven_cushion_pct_raw")
        primary = (Decimal(raw) if raw is not None else Decimal("1e9"),
                   -Decimal(cushion) if cushion is not None else Decimal(0))
    elif key == "cost_usd":
        primary = Decimal(cd["cost_usd"])
    elif key == "dte":
        primary = Decimal(cd["dte"])
    elif key == "spread_pct":
        primary = Decimal(str(cd["spread_pct"])) if cd["spread_pct"] is not None else Decimal("1e9")
    else:
        primary = Decimal(cd["account_pct_raw"])
    strikes = [Decimal(leg["strike"]) for leg in cd["legs"]]
    return (primary, cd["symbol"], cd["expiration"], strikes)


def build_candidate(sv, cfg, exits, contracts_n, unds, total_value, as_of, report_only, is_spread):
    legs = sv["legs"]
    long_leg = legs[0]
    net = sv["net"]
    spot = to_dec(unds[long_leg.symbol].get("price"))
    qty = Decimal(contracts_n)
    cost_unit = net * MULTIPLIER
    cost = cost_unit * qty
    flags = list(sv.get("flags", []))
    notes = []
    width = None
    if is_spread:
        short = legs[1]
        width = abs(short.strike - long_leg.strike)
        breakeven = long_leg.strike + net if long_leg.type == "call" else long_leg.strike - net
        max_gain = (width - net) * MULTIPLIER * qty
        max_gain_unlimited = False
        risk_reward = {"risk_usd": money(cost), "reward_usd": money(max_gain),
                       "reward_per_dollar_risked": float(((width - net) / net).quantize(CENT, rounding=ROUND_HALF_UP)),
                       "text": "risks $%s to make at most $%s" % (money(cost), money(max_gain))}
        label = _spread_label(legs)
    else:
        breakeven = long_leg.strike + net if long_leg.type == "call" else long_leg.strike - net
        if long_leg.type == "call":
            max_gain, max_gain_unlimited = None, True
        else:
            max_gain = (long_leg.strike - net) * MULTIPLIER * qty if long_leg.strike > net else Decimal(0)
            max_gain_unlimited = False
            notes.append("a long put's maximum gain assumes the stock falls to $0")
        risk_reward = None
        label = long_leg.label
    # Signed price move from the underlying to breakeven (+ a rise, - a fall); the same number as
    # options_math.py payoff. Its sign alone can't say whether the trade still needs the move: an
    # in-the-money debit spread can already be past breakeven, and then the value points AGAINST the
    # trade (a call spread showing "-2.2%" does not need a 2.2% fall; it can absorb one).
    move = (breakeven - spot) / spot * HUNDRED
    move_d, move_raw = pct(move)
    toward = move if long_leg.type == "call" else -move   # measured in the trade's direction
    past_breakeven = toward < 0
    needed_d, needed_raw = pct(toward if toward > 0 else Decimal(0))
    cushion_d, cushion_raw = pct(-toward) if past_breakeven else (None, None)
    if past_breakeven:
        move_text = ("Breakeven already passed: the stock can %s %.1f%% by expiration (%d days) before the "
                     "position loses money" % ("fall" if long_leg.type == "call" else "rise", cushion_d, long_leg.dte))
    else:
        move_text = "Required move %s%.1f%% (a %s) in %d days" % ("+" if long_leg.type == "call" else "-", needed_d,
                                                                 "rise" if long_leg.type == "call" else "fall",
                                                                 long_leg.dte)
    acct_pct = cost / total_value * HUNDRED
    acct_d, acct_raw = pct(acct_pct)
    leg_rows = []
    spreads = []
    for i, c in enumerate(legs):
        sp = spread_pct_of(c)
        spreads.append(sp)
        leg_rows.append({
            "option_id": c.id, "side": "buy" if i == 0 else "sell", "type": c.type, "strike": num_str(c.strike),
            "expiration": c.expiration.isoformat(), "bid": num_str(c.bid), "ask": num_str(c.ask),
            "mid": num_str(mid_of(c).quantize(Decimal("0.001"))) if mid_of(c) is not None else None,
            "delta": num_str(c.delta) if c.delta is not None else None, "open_interest": c.oi,
            "spread_pct": pct(sp)[0], "updated_at": c.updated_at,
        })
    widest = max(spreads)

    # Limit price by the user's saved entry rule (the only source of an option price besides the user).
    rule = cfg["entry_price_rule"]
    limit, basis = None, None
    if rule == "natural":
        limit = net
        basis = ("your entry_price_rule is natural: the limit is the natural price from these quotes "
                 "(%s)" % ("long ask - short bid" if is_spread else "the ask"))
    elif rule == "mid":
        if is_spread:
            raw_mid = mid_of(legs[0]) - mid_of(legs[1])
            limit = raw_mid.quantize(CENT, rounding=ROUND_HALF_UP)
            flags.append("SPREAD_NET_TICK_NOT_CHECKED")
            basis = ("your entry_price_rule is mid: long mid - short mid = %s, rounded half-up to the cent; the "
                     "review rejects a net price off Robinhood's allowed increment" % num_str(raw_mid.normalize()))
        else:
            raw_mid = mid_of(long_leg)
            tick = tick_for(raw_mid, long_leg.min_ticks)
            if tick is None:
                limit = raw_mid.quantize(CENT, rounding=ROUND_HALF_UP)
                flags.append("TICK_NOT_CHECKED")
                basis = ("your entry_price_rule is mid: (bid + ask) / 2 = %s, rounded half-up to the cent (no "
                         "min_ticks given, so the price increment was not checked)" % num_str(raw_mid.normalize()))
            else:
                limit = round_to_tick(raw_mid, tick)
                basis = ("your entry_price_rule is mid: (bid + ask) / 2 = %s, rounded half-up to the %s tick"
                         % (num_str(raw_mid.normalize()), num_str(tick)))
        if limit is not None and limit <= 0:
            limit = None
            flags.append("MID_NOT_POSITIVE")
            basis = "the mid price is not positive for these quotes; ask the user for the limit price"
    else:
        basis = "your entry_price_rule is ask_each_time: ask the user for the limit price before the review"

    exit_basis = limit if limit is not None else net
    pt, sl = exits["profit_target_pct"], exits["stop_loss_pct"]
    target = (exit_basis * (1 + pt / HUNDRED)).quantize(CENT, rounding=ROUND_HALF_UP)
    stop = (exit_basis * (1 - sl / HUNDRED)).quantize(CENT, rounding=ROUND_HALF_UP)
    exit_notes = []
    if sl >= HUNDRED:
        # A bought option or debit spread cannot be worth less than zero, so the most it can lose is what it
        # paid: a stop at 100% or more can fire only at a total loss.
        stop = Decimal("0.00")
        flags.append("STOP_AT_TOTAL_LOSS")
        exit_notes.append("your %s%% stop is at or beyond the whole premium: on this long-only structure the most "
                          "it can lose is what it paid, so the stop can fire only at a total loss" % plain(sl))
    if is_spread and target > width:
        flags.append("TARGET_ABOVE_MAX_VALUE")
        exit_notes.append("the +%s%% target needs the spread worth %s, above its %s maximum value at expiration"
                          % (plain(pt), money(target), money(width)))
    time_stop_date = long_leg.expiration - timedelta(days=exits["time_stop_dte"])
    hard_exit = as_of + timedelta(days=exits["max_hold_days"])
    if hard_exit > long_leg.expiration:
        exit_notes.append("max_hold_days ends after expiration; expiration comes first")
    exits_out = {
        "basis_price": money(exit_basis),
        "basis": ("your limit by entry_price_rule" if limit is not None else
                  "the natural price (no limit yet); recompute from the limit you give"),
        "profit_target_pct": plain(pt), "profit_target_price": money(target),
        "stop_loss_pct": plain(sl), "stop_price": money(stop),
        "time_stop_dte": exits["time_stop_dte"], "time_stop_date": time_stop_date.isoformat(),
        "max_hold_days": exits["max_hold_days"], "hard_exit_date": hard_exit.isoformat(),
        "note": "planned levels from your saved exit rules, per contract; after a fill, recompute them from the "
                "actual fill price (robinhood-options-monitor does)",
        "notes": exit_notes,
    }

    review = {"legs": [{"option_id": legs[0].id, "side": "buy", "position_effect": "open", "ratio_quantity": 1}],
              "quantity": str(contracts_n), "type": "limit", "time_in_force": "gfd", "market_hours": "regular_hours"}
    if is_spread:
        review["legs"].append({"option_id": legs[1].id, "side": "sell", "position_effect": "open",
                               "ratio_quantity": 1})
        review["direction"] = "debit"
    if limit is not None:
        review["price"] = num_str(limit)
    chain_symbol = long_leg.chain_symbol
    if chain_symbol:
        review["chain_symbol"] = chain_symbol
    if long_leg.underlying_type:
        review["underlying_type"] = long_leg.underlying_type
    if not (chain_symbol and long_leg.underlying_type):
        flags.append("SEND_CHAIN_SYMBOL_AND_UNDERLYING_TYPE")

    return {
        "rank": None,
        "label": label,
        "symbol": long_leg.symbol,
        "structure": cfg["structure"],
        "expiration": long_leg.expiration.isoformat(),
        "dte": long_leg.dte,
        "underlying_price": money(spot),
        "legs": leg_rows,
        "contracts": str(contracts_n),
        "natural_price": num_str(net),
        "natural_label": NATURAL_LABEL["spread" if is_spread else "single"],
        "cost_per_contract_usd": money(cost_unit),
        "cost_usd": money(cost),
        "max_loss_usd": money(cost),
        "max_gain_usd": money(max_gain) if max_gain is not None else None,
        "max_gain_unlimited": max_gain_unlimited,
        "width": num_str(width) if width is not None else None,
        "risk_reward": risk_reward,
        "breakeven": money(breakeven),
        "breakeven_basis": "at the natural price",
        "required_move_pct": move_d,
        "required_move_pct_raw": move_raw,
        "past_breakeven": past_breakeven,
        "move_needed_pct": needed_d,
        "move_needed_pct_raw": needed_raw,
        "breakeven_cushion_pct": cushion_d,
        "breakeven_cushion_pct_raw": cushion_raw,
        "move_text": move_text,
        "spread_pct": pct(widest)[0],
        "account_pct": acct_d,
        "account_pct_raw": acct_raw,
        "entry_price_rule": rule,
        "limit_price_by_rule": num_str(limit) if limit is not None else None,
        "limit_price_basis": basis,
        "needs_user_price": limit is None,
        "exits": exits_out,
        "earnings": sv.get("earnings"),
        "flags": sorted(set(flags)),
        "notes": notes,
        "order_params": review,
        "lint_provenance": {"quantity": "user_config", "price": "user_config" if limit is not None else None},
        "ticket_allowed": not report_only,
        "quote_times": [c.updated_at for c in legs if c.updated_at],
    }


# ---------------------------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------------------------
OPS = {"preflight": op_preflight, "screen": op_screen}


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
# JSON Schemas (draft 2020-12 subset) and a small validator for the self-test
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_B = {"type": "boolean"}
_BN = {"type": ["boolean", "null"]}
_I = {"type": "integer"}
_N = {"type": ["number", "null"]}
_M = {"type": ["string", "null"], "pattern": r"^-?\d+\.\d{2}$"}
_NUMSTR = {"type": ["string", "integer", "number"]}
_LIST_S = {"type": "array", "items": _S}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
_CFG_VAL = {"type": ["string", "array"]}
_CONTRACT = {"type": "object", "required": ["option_id", "symbol", "type", "strike", "expiration"],
             "properties": {"option_id": _S, "symbol": _S, "chain_symbol": _S, "underlying_type": _S,
                            "type": {"enum": ["call", "put"]}, "strike": _NUMSTR, "expiration": _S,
                            "bid": {"type": ["string", "number", "null"]},
                            "ask": {"type": ["string", "number", "null"]},
                            "delta": {"type": ["string", "number", "null"]},
                            "open_interest": {"type": ["string", "integer", "null"]},
                            "multiplier": {"type": ["string", "integer", "null"]}, "tradability": _SN, "state": _SN,
                            "can_open_position": _BN, "updated_at": _SN,
                            "min_ticks": {"type": ["object", "null"],
                                          "properties": {"above_tick": _SN, "below_tick": _SN, "cutoff_price": _SN}}}}
_EARN = {"type": "object", "properties": {"date": _SN, "timing": _SN, "verified": _B,
                                          "reports": {"type": "array", "items": {
                                              "type": "object", "required": ["date"],
                                              "properties": {"date": _S, "timing": _SN, "verified": _B}}}}}
_CHECK = {"type": "object", "required": ["check", "pass", "detail"],
          "properties": {"check": _S, "pass": _BN, "detail": _S}}
_STOP = {"type": ["object", "null"], "required": ["code", "msg"],
         "properties": {"code": _S, "msg": _S, "fields": _LIST_S}}
_LEG = {"type": "object", "required": ["option_id", "side", "type", "strike", "expiration", "bid", "ask"],
        "properties": {"option_id": _S, "side": {"enum": ["buy", "sell"]}, "type": {"enum": ["call", "put"]},
                       "strike": _S, "expiration": _S, "bid": _SN, "ask": _SN, "mid": _SN, "delta": _SN,
                       "open_interest": {"type": ["integer", "null"]}, "spread_pct": _N, "updated_at": _SN}}
_CANDIDATE = {"type": "object",
              "required": ["rank", "label", "symbol", "structure", "expiration", "dte", "legs", "natural_price",
                           "natural_label", "cost_usd", "max_loss_usd", "breakeven", "required_move_pct",
                           "past_breakeven", "move_needed_pct", "breakeven_cushion_pct", "move_text",
                           "spread_pct", "account_pct", "limit_price_by_rule", "contracts", "exits",
                           "order_params", "ticket_allowed"],
              "properties": {"rank": _I, "label": _S, "symbol": _S, "structure": {"enum": list(STRUCTURES)},
                             "expiration": _S, "dte": _I, "underlying_price": _M, "legs": {"type": "array",
                                                                                           "items": _LEG},
                             "contracts": _S, "natural_price": _S, "natural_label": _S, "cost_per_contract_usd": _M,
                             "cost_usd": _M, "max_loss_usd": _M, "max_gain_usd": _M, "max_gain_unlimited": _B,
                             "width": _SN, "risk_reward": {"type": ["object", "null"]}, "breakeven": _M,
                             "breakeven_basis": _S, "required_move_pct": _N, "required_move_pct_raw": _SN,
                             "past_breakeven": _B, "move_needed_pct": {"type": "number", "minimum": 0},
                             "move_needed_pct_raw": _S, "breakeven_cushion_pct": _N,
                             "breakeven_cushion_pct_raw": _SN, "move_text": _S,
                             "spread_pct": _N, "account_pct": _N, "account_pct_raw": _SN,
                             "entry_price_rule": {"enum": list(ENTRY_RULES)}, "limit_price_by_rule": _SN,
                             "limit_price_basis": _S, "needs_user_price": _B, "exits": {"type": "object"},
                             "earnings": {"type": ["object", "null"]}, "flags": _LIST_S, "notes": _LIST_S,
                             "order_params": {"type": "object", "required": ["legs", "quantity", "type",
                                                                             "time_in_force", "market_hours"]},
                             "lint_provenance": {"type": "object"}, "ticket_allowed": _B,
                             "quote_times": _LIST_S}}
_REJECT = {"type": "object", "required": ["option_id", "label", "reasons"],
           "properties": {"option_id": _S, "short_option_id": _S, "label": _S, "reasons": _LIST_S}}

SCHEMAS = {
    "preflight": {
        "input": {"type": "object", "required": ["structure", "account"],
                  "properties": {
                      "structure": {"enum": list(STRUCTURES)},
                      "account": {"type": "object", "required": ["type", "option_level"],
                                  "properties": {"type": {"enum": list(ACCOUNT_TYPES)}, "option_level": _SN,
                                                 "retirement": _BN, "agentic_allowed": _B}},
                      "funding": {"type": "object",
                                  "required": ["buying_power_usd", "max_cost_per_contract_usd", "contracts_per_entry",
                                               "reserve_cash_usd"],
                                  "properties": {"buying_power_usd": _S, "max_cost_per_contract_usd": _S,
                                                 "contracts_per_entry": {"type": ["string", "integer"]},
                                                 "reserve_cash_usd": _S}}}},
        "output": {"anyOf": [{"type": "object",
                              "required": ["ok", "allowed", "structure", "requirement", "checks", "route", "stop",
                                           "notes"],
                              "properties": {"ok": {"enum": [True]}, "allowed": _B, "structure": _S,
                                             "requirement": {"type": "object"},
                                             "checks": {"type": "array", "items": _CHECK}, "route": _LIST_S,
                                             "stop": _STOP, "notes": _LIST_S}}, _ERR]},
    },
    "screen": {
        "input": {"type": "object", "required": ["as_of", "criteria", "entry", "exits", "account"],
                  "properties": {
                      "as_of": _S, "in_regular_session": _BN,
                      "criteria": {"type": "object", "additionalProperties": _CFG_VAL},
                      "entry": {"type": "object", "additionalProperties": _CFG_VAL},
                      "exits": {"type": "object", "additionalProperties": _CFG_VAL},
                      "account": {"type": "object", "properties": {
                          "total_value_usd": _S, "buying_power_usd": _S,
                          "open_option_positions": {"type": ["integer", "string"]}, "type": _S}},
                      "underlyings": {"type": "object", "additionalProperties": {
                          "type": "object", "properties": {"price": _NUMSTR, "avg_volume": _NUMSTR,
                                                           "iv_rank": _NUMSTR, "price_as_of": _S}}},
                      "iv_rank_source": {"type": "object", "required": ["kind", "name"],
                                         "properties": {"kind": {"enum": ["enum_filter", "expression"]}, "name": _S,
                                                        "scale": {"enum": list(IV_SCALES)}}},
                      "contracts": {"type": "array", "items": _CONTRACT},
                      "fetched_chains": _LIST_S,
                      "scan_filters_applied": {"type": "array", "items": {"enum": list(SCAN_FILTERS)}},
                      "earnings": {"type": "object", "additionalProperties": _EARN}}},
        "output": {"anyOf": [{"type": "object",
                              "required": ["ok", "status", "report_status", "status_line", "stopped", "report_only",
                                           "candidates", "rejected", "rejected_underlyings", "counts_by_reason",
                                           "next_fetch", "sort", "notes"],
                              "properties": {
                                  "ok": {"enum": [True]},
                                  "status": {"enum": ["stopped", "incomplete", "candidates", "no_candidates"]},
                                  "report_status": {"enum": ["STOPPED", "UNKNOWN", "POSSIBLE", "NO ACTION"]},
                                  "status_line": _S, "stopped": _STOP, "report_only": _B,
                                  "report_only_reason": _SN, "as_of": _S, "structure": _S,
                                  "candidates": {"type": "array", "items": _CANDIDATE},
                                  "rejected": {"type": "array", "items": _REJECT},
                                  "rejected_underlyings": {"type": "array", "items": {
                                      "type": "object", "required": ["symbol", "reasons"],
                                      "properties": {"symbol": _S, "reasons": _LIST_S}}},
                                  "counts_by_reason": {"type": "object", "additionalProperties": _I},
                                  "counts": {"type": "object", "additionalProperties": _I},
                                  "underlyings_passed": _LIST_S,
                                  "next_fetch": {"type": "object", "required": ["chains", "earnings"],
                                                 "properties": {"chains": _LIST_S, "earnings": _LIST_S}},
                                  "earnings_needed_for_cards": _LIST_S,
                                  "sort": {"type": ["object", "null"]},
                                  "dollars_at_stake": {"type": "array"},
                                  "oldest_quote_at": _SN, "notes": _LIST_S}}, _ERR]},
    },
}


def _type_ok(value, t):
    types = t if isinstance(t, list) else [t]
    for x in types:
        if x == "string" and isinstance(value, str):
            return True
        if x == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if x == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if x == "boolean" and isinstance(value, bool):
            return True
        if x == "null" and value is None:
            return True
        if x == "object" and isinstance(value, dict):
            return True
        if x == "array" and isinstance(value, list):
            return True
    return False


def schema_errors(value, schema, path="$"):
    errs = []
    if "anyOf" in schema:
        options = [schema_errors(value, s, path) for s in schema["anyOf"]]
        if all(options):
            errs.append("%s: matches no anyOf branch (%s)" % (path, "; ".join(o[0] for o in options if o)))
        return errs
    if "type" in schema and not _type_ok(value, schema["type"]):
        return ["%s: expected %s" % (path, schema["type"])]
    if "enum" in schema and value not in schema["enum"]:
        errs.append("%s: %r not in %s" % (path, value, schema["enum"]))
    if "pattern" in schema and isinstance(value, str) and not re.match(schema["pattern"], value):
        errs.append("%s: %r does not match %s" % (path, value, schema["pattern"]))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errs.append("%s: missing %s" % (path, key))
        props = schema.get("properties", {})
        for key, sub in value.items():
            if key in props:
                errs.extend(schema_errors(sub, props[key], "%s.%s" % (path, key)))
            elif isinstance(schema.get("additionalProperties"), dict):
                errs.extend(schema_errors(sub, schema["additionalProperties"], "%s.%s" % (path, key)))
            elif schema.get("additionalProperties") is False:
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


# ---------------------------------------------------------------------------------------------
# Self-test: embedded examples (the fixture household's AMD numbers; synthetic, not live data)
# ---------------------------------------------------------------------------------------------
def _example_config(**over):
    criteria = {"max_position_pct": "30", "max_concurrent": "3", "max_cost_per_contract_usd": "700.00",
                "reserve_cash_usd": "200.00", "price_min": "20.00", "price_max": "700.00", "min_avg_volume": "1000000",
                "symbols_allowlist": [], "symbols_blocklist": [], "structure": "long_call", "spread_width_min": "OFF",
                "spread_width_max": "OFF", "dte_min": "14", "dte_max": "45", "delta_min": "0.30", "delta_max": "0.60",
                "max_spread_pct": "5", "min_open_interest": "100", "iv_rank_min": "OFF", "iv_rank_max": "OFF",
                "earnings_policy": "avoid", "earnings_buffer_days": "2", "scan_sessions": "regular_hours_only",
                "sort_by": "required_move_pct"}
    criteria.update(over)
    return criteria


def _example_screen(criteria=None, contracts=None, earnings=None, **extra):
    base = {
        "as_of": "2026-11-16", "in_regular_session": False,
        "criteria": criteria or _example_config(),
        "entry": {"entry_price_rule": "natural", "contracts_per_entry": "1"},
        "exits": {"profit_target_pct": "50", "stop_loss_pct": "40", "time_stop_dte": "7", "max_hold_days": "30"},
        "account": {"total_value_usd": "4100.00", "buying_power_usd": "2480.00", "open_option_positions": 1,
                    "type": "limited_margin"},
        "underlyings": {"AMD": {"price": "161.40", "avg_volume": "45000000"}},
        "contracts": contracts if contracts is not None else [
            {"option_id": "amd-1218-165c", "symbol": "AMD", "chain_symbol": "AMD", "underlying_type": "equity",
             "type": "call", "strike": "165.0000", "expiration": "2026-12-18", "bid": "6.10", "ask": "6.30",
             "delta": "0.4512", "open_interest": 1200}],
        "earnings": earnings if earnings is not None else {"AMD": {"date": "2027-01-27", "timing": "pm",
                                                                   "verified": False}},
    }
    base.update(extra)
    return base


EXAMPLES = [
    ("preflight", {"structure": "debit_call_spread",
                   "account": {"type": "cash", "option_level": "option_level_2", "retirement": False,
                               "agentic_allowed": True}},
     {"ok": True, "allowed": False, "stop": {"code": "ACCOUNT_TYPE"},
      "route": ["get_limited_margin_upgrade_info", "user completes the limited-margin upgrade",
                "re-fetch get_accounts"]}),
    ("preflight", {"structure": "long_call",
                   "account": {"type": "limited_margin", "option_level": "option_level_3", "retirement": False,
                               "agentic_allowed": True},
                   "funding": {"buying_power_usd": "2480.00", "max_cost_per_contract_usd": "700.00",
                               "contracts_per_entry": "1", "reserve_cash_usd": "200.00"}},
     {"ok": True, "allowed": True, "stop": None, "route": []}),
    ("screen", _example_screen(),
     {"ok": True, "status": "candidates", "report_only": True,
      "candidates": [{"label": "AMD 2026-12-18 165C", "breakeven": "171.30", "required_move_pct": 6.1,
                      "cost_usd": "630.00", "max_loss_usd": "630.00", "dte": 32, "limit_price_by_rule": "6.30",
                      "exits": {"profit_target_price": "9.45", "stop_price": "3.78", "time_stop_date": "2026-12-11"},
                      "order_params": {"quantity": "1", "type": "limit", "price": "6.30",
                                        "market_hours": "regular_hours"}}]}),
    ("screen", _example_screen(contracts=[
        {"option_id": "amd-1218-165c", "symbol": "AMD", "type": "call", "strike": "165", "expiration": "2026-12-18",
         "bid": "6.10", "ask": "6.30"}]),
     {"ok": True, "status": "no_candidates", "counts_by_reason": {"DELTA_OI_UNAVAILABLE": 1}}),
    ("screen", _example_screen(criteria=_example_config(delta_min="UNSET", dte_max="UNSET")),
     {"ok": True, "status": "stopped", "stopped": {"code": "CRITERIA_UNSET",
                                                   "fields": ["options.criteria.dte_max",
                                                              "options.criteria.delta_min"]}}),
    ("screen", _example_screen(
        criteria=_example_config(structure="debit_call_spread", spread_width_min="1", spread_width_max="1",
                                 max_spread_pct="10", delta_min="0.20", delta_max="0.60"),
        contracts=[
            {"option_id": "x-l", "symbol": "AMD", "type": "call", "strike": "165", "expiration": "2026-12-18",
             "bid": "2.50", "ask": "2.60", "delta": "0.40", "open_interest": 500},
            {"option_id": "x-s", "symbol": "AMD", "type": "call", "strike": "166", "expiration": "2026-12-18",
             "bid": "1.80", "ask": "1.90", "delta": "0.35", "open_interest": 500}]),
     {"ok": True, "status": "candidates",
      "candidates": [{"natural_price": "0.80", "width": "1.00", "cost_usd": "80.00", "max_gain_usd": "20.00",
                      "breakeven": "165.80", "risk_reward": {"reward_per_dollar_risked": 0.25},
                      "order_params": {"direction": "debit"}}]}),
    # An in-the-money call spread already past breakeven sorts ahead of one that still needs a rise,
    # and is not labeled as needing a fall.
    ("screen", _example_screen(
        criteria=_example_config(structure="debit_call_spread", spread_width_min="5", spread_width_max="5",
                                 max_spread_pct="10", delta_min="0.20", delta_max="0.60"),
        contracts=[
            {"option_id": "c155", "symbol": "AMD", "type": "call", "strike": "155", "expiration": "2026-12-18",
             "bid": "9.80", "ask": "10.00", "delta": "0.60", "open_interest": 500},
            {"option_id": "c160", "symbol": "AMD", "type": "call", "strike": "160", "expiration": "2026-12-18",
             "bid": "7.20", "ask": "7.40", "delta": "0.52", "open_interest": 500},
            {"option_id": "c165", "symbol": "AMD", "type": "call", "strike": "165", "expiration": "2026-12-18",
             "bid": "4.90", "ask": "5.10", "delta": "0.45", "open_interest": 500}]),
     {"ok": True, "status": "candidates",
      "candidates": [{"rank": 1, "breakeven": "157.80", "required_move_pct": -2.2, "past_breakeven": True,
                      "move_needed_pct": 0.0, "breakeven_cushion_pct": 2.2},
                     {"rank": 2, "breakeven": "162.50", "required_move_pct": 0.7, "past_breakeven": False,
                      "move_needed_pct": 0.7, "breakeven_cushion_pct": None}]}),
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
            problems.append("output mismatch: %s" % json.dumps(out, sort_keys=True)[:2000])
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: options_screen.py preflight|screen < input.json")))
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
