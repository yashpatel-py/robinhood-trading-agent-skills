#!/usr/bin/env python3
"""exposure.py - household concentration across every Robinhood account the user let us read.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: the Agentic account is usually small next to the user's Individual account and
IRA, so "PLTR is 40% of this account" can be 2% of the household - or the reverse. The connector
returns positions with average cost, not value, and per account, so the agent must price them and
add them up itself. This script does that arithmetic and states its basis: equities and crypto at
the price you pass, options at the bid (long) or ask (short) x 100, grouped under the underlying.
There is no ETF look-through, and a position you could not price is listed as unpriced and makes
the result incomplete (unknown is never "fine").

Usage:
    python3 exposure.py run < input.json > output.json
    python3 exposure.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash. Account numbers are cut to their last 4.
"""

import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

VERSION = "2.0.0"
SCRIPT = "exposure"
CENT = Decimal("0.01")
TENTH = Decimal("0.1")
RAW = Decimal("1e-10")
ASSET_CLASSES = ("equity", "option", "crypto")


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


def money(d):
    return str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def pct(part, whole):
    if whole is None or whole <= 0:
        return None, None
    value = part / whole * 100
    return float(value.quantize(TENTH, rounding=ROUND_HALF_UP)), str(value.quantize(RAW, rounding=ROUND_HALF_UP))


def dec(value, field, required=True, positive=False):
    if value is None:
        if required:
            raise InputError("MISSING_FIELD", field, "%s is required" % field)
        return None
    if isinstance(value, bool):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string" % field)
    text = repr(value) if isinstance(value, float) else str(value).strip()
    if not re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)$", text):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string like \"12.5\" (got %r)" % (field, value))
    d = Decimal(text)
    if positive and d <= 0:
        raise InputError("BAD_VALUE", field, "%s must be greater than zero" % field)
    return d


def last4(value):
    s = str(value or "").strip()
    return s[-4:] if s else "????"


def base_symbol(sym, asset_class):
    s = str(sym or "").strip().upper()
    if asset_class == "crypto" and s.endswith("-USD"):
        s = s[:-4]
    return s


def value_position(pos, i):
    where = "positions[%d]" % i
    ac = str(pos.get("asset_class", "equity")).strip().lower()
    if ac not in ASSET_CLASSES:
        raise InputError("BAD_VALUE", where + ".asset_class", "asset_class must be equity, option or crypto")
    qty = dec(pos.get("quantity"), where + ".quantity")
    group = base_symbol(pos.get("underlying") or pos.get("symbol"), ac)
    if not group:
        raise InputError("MISSING_FIELD", where + ".symbol", "symbol is required")
    if ac == "option":
        side = str(pos.get("side", "long")).strip().lower()
        if side not in ("long", "short"):
            raise InputError("BAD_VALUE", where + ".side", "option side must be long or short")
        mult = dec(pos.get("multiplier"), where + ".multiplier", required=False, positive=True) or Decimal(100)
        key = "bid" if side == "long" else "ask"
        px = dec(pos.get(key), where + "." + key, required=False)
        if px is None:
            px = dec(pos.get("price"), where + ".price", required=False)
        if px is None:
            return group, ac, None, "no %s (or price) for this option" % key
        value = px * mult * abs(qty)
        return group, ac, (value if side == "long" else -value), None
    px = dec(pos.get("price"), where + ".price", required=False)
    if px is None:
        return group, ac, None, "no price"
    return group, ac, px * qty, None


def op_run(data):
    positions = data.get("positions")
    if not isinstance(positions, list):
        raise InputError("MISSING_FIELD", "positions", "give positions: [{account_last4, asset_class, symbol, quantity, "
                                                        "price}] from every account in scope")
    by_symbol, by_account, by_class = {}, {}, {}
    unpriced = []
    for i, pos in enumerate(positions):
        if not isinstance(pos, dict):
            raise InputError("BAD_VALUE", "positions[%d]" % i, "each position is an object")
        group, ac, value, why = value_position(pos, i)
        acct = last4(pos.get("account_last4"))
        if value is None:
            unpriced.append({"symbol": group, "asset_class": ac, "account_last4": acct, "why": why})
            continue
        entry = by_symbol.setdefault(group, {"value": Decimal(0), "accounts": set(), "classes": {}})
        entry["value"] += value
        entry["accounts"].add(acct)
        entry["classes"][ac] = entry["classes"].get(ac, Decimal(0)) + value
        by_account[acct] = by_account.get(acct, Decimal(0)) + value
        by_class[ac] = by_class.get(ac, Decimal(0)) + value

    cash_in = data.get("cash_usd_by_account")
    cash_total = None
    if cash_in is not None:
        if not isinstance(cash_in, dict):
            raise InputError("BAD_VALUE", "cash_usd_by_account", "cash_usd_by_account maps account_last4 to dollars")
        cash_total = Decimal(0)
        for acct, amount in cash_in.items():
            amt = dec(amount, "cash_usd_by_account.%s" % last4(acct))
            cash_total += amt
            by_account[last4(acct)] = by_account.get(last4(acct), Decimal(0)) + amt
    positions_total = sum((e["value"] for e in by_symbol.values()), Decimal(0))
    total = positions_total + (cash_total or Decimal(0))

    rows = []
    for sym, entry in sorted(by_symbol.items(), key=lambda kv: (-kv[1]["value"], kv[0])):
        p, p_raw = pct(entry["value"], total)
        rows.append({
            "symbol": sym,
            "value_usd": money(entry["value"]),
            "pct": p,
            "pct_raw": p_raw,
            "accounts": sorted(entry["accounts"]),
            "by_asset_class": {k: money(v) for k, v in sorted(entry["classes"].items())},
        })

    notes = ["options valued at bid (long) / ask (short) \u00d7 100, grouped under the underlying",
             "no ETF look-through",
             "household total %s cash" % ("includes" if cash_total is not None else "excludes")]
    if unpriced:
        notes.append("%d position(s) could not be priced; percentages are incomplete" % len(unpriced))

    out = {
        "ok": True,
        "household_total_usd": money(total),
        "positions_total_usd": money(positions_total),
        "cash_total_usd": money(cash_total) if cash_total is not None else None,
        "by_symbol": rows,
        "by_account": [{"account_last4": a, "value_usd": money(v)} for a, v in sorted(by_account.items())],
        "by_asset_class": {k: money(v) for k, v in sorted(by_class.items())},
        "unpriced": unpriced,
        "complete": not unpriced,
        "sort": "by value, largest first",
        "notes": notes,
    }

    proposed = data.get("proposed")
    if proposed is not None:
        out["proposed_after"] = proposed_after(proposed, by_symbol, total, cash_total is not None, notes)
    return out


def proposed_after(proposed, by_symbol, total, includes_cash, notes):
    if not isinstance(proposed, dict):
        raise InputError("BAD_VALUE", "proposed", "proposed is {symbol, side, quantity|notional_usd, price}")
    ac = str(proposed.get("asset_class", "equity")).strip().lower()
    sym = base_symbol(proposed.get("symbol"), ac)
    if not sym:
        raise InputError("MISSING_FIELD", "proposed.symbol", "proposed.symbol is required")
    side = str(proposed.get("side", "")).strip().lower()
    if side not in ("buy", "sell"):
        raise InputError("BAD_VALUE", "proposed.side", "proposed.side must be buy or sell")
    notional = dec(proposed.get("notional_usd"), "proposed.notional_usd", required=False, positive=True)
    if notional is None:
        qty = dec(proposed.get("quantity"), "proposed.quantity", positive=True)
        price = dec(proposed.get("price"), "proposed.price", positive=True)
        notional = qty * price
    current = by_symbol.get(sym, {}).get("value", Decimal(0))
    delta = notional if side == "buy" else -notional
    new_total = total if includes_cash else total + delta
    after_value = current + delta
    if after_value < 0:
        notes.append("the proposed sell is larger than the %s value found in the accounts read" % sym)
        after_value = Decimal(0)
    before, before_raw = pct(current, total)
    after, after_raw = pct(after_value, new_total)
    return {"symbol": sym, "side": side, "notional_usd": money(notional), "pct_before": before, "pct_after": after,
            "pct_before_raw": before_raw, "pct_after_raw": after_raw, "value_after_usd": money(after_value)}


OPS = {"run": op_run}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected run" % op)
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
_M = {"type": "string", "pattern": r"^-?\d+\.\d{2}$"}
_PCT = {"type": ["number", "null"]}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
SCHEMAS = {
    "run": {
        "input": {"type": "object", "required": ["positions"], "additionalProperties": False, "properties": {
            "positions": {"type": "array", "items": {"type": "object", "required": ["symbol", "quantity"],
                                                     "properties": {
                                                         "account_last4": _S, "account_type": _S,
                                                         "asset_class": {"enum": list(ASSET_CLASSES)},
                                                         "symbol": _S, "underlying": _S, "quantity": _S, "price": _S,
                                                         "side": {"enum": ["long", "short"]}, "bid": _S, "ask": _S,
                                                         "multiplier": _S}}},
            "cash_usd_by_account": {"type": "object", "additionalProperties": _S},
            "proposed": {"type": "object", "required": ["symbol", "side"], "properties": {
                "symbol": _S, "side": {"enum": ["buy", "sell"]}, "quantity": _S, "notional_usd": _S, "price": _S,
                "asset_class": {"enum": list(ASSET_CLASSES)}}},
        }},
        "output": {"anyOf": [{"type": "object", "additionalProperties": False,
                              "required": ["ok", "household_total_usd", "by_symbol", "unpriced", "complete", "notes"],
                              "properties": {
                                  "ok": {"enum": [True]}, "household_total_usd": _M, "positions_total_usd": _M,
                                  "cash_total_usd": {"type": ["string", "null"]},
                                  "by_symbol": {"type": "array", "items": {
                                      "type": "object", "required": ["symbol", "value_usd", "pct", "accounts"],
                                      "properties": {"symbol": _S, "value_usd": _M, "pct": _PCT,
                                                     "pct_raw": {"type": ["string", "null"]},
                                                     "accounts": {"type": "array", "items": _S},
                                                     "by_asset_class": {"type": "object"}}}},
                                  "by_account": {"type": "array"}, "by_asset_class": {"type": "object"},
                                  "unpriced": {"type": "array"}, "complete": {"type": "boolean"}, "sort": _S,
                                  "notes": {"type": "array", "items": _S},
                                  "proposed_after": {"type": "object", "required": ["symbol", "pct_before",
                                                                                    "pct_after"]}}}, _ERR]},
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


EXAMPLES = [
    ("run", {"positions": [
        {"account_last4": "X4F1", "asset_class": "equity", "symbol": "PLTR", "quantity": "10", "price": "31.20"},
        {"account_last4": "M7Q5", "asset_class": "equity", "symbol": "PLTR", "quantity": "20", "price": "31.20"},
        {"account_last4": "M7Q5", "asset_class": "equity", "symbol": "NVDA", "quantity": "10", "price": "228.10"},
        {"account_last4": "X4F1", "asset_class": "option", "symbol": "AMD 165C", "underlying": "AMD", "side": "long",
         "quantity": "1", "bid": "3.30", "ask": "3.40"},
        {"account_last4": "X4F1", "asset_class": "crypto", "symbol": "BTC-USD", "quantity": "0.01", "price": "60000"}],
        "proposed": {"symbol": "PLTR", "side": "buy", "quantity": "10", "price": "31.24"}},
     {"ok": True, "household_total_usd": "4147.00", "complete": True,
      "by_symbol": [{"symbol": "NVDA", "value_usd": "2281.00", "pct": 55.0},
                    {"symbol": "PLTR", "value_usd": "936.00", "pct": 22.6, "accounts": ["M7Q5", "X4F1"]},
                    {"symbol": "BTC", "value_usd": "600.00"}, {"symbol": "AMD", "value_usd": "330.00", "pct": 8.0}],
      "proposed_after": {"symbol": "PLTR", "pct_before": 22.6, "pct_after": 28.0}}),
    ("run", {"positions": [{"account_last4": "X4F1", "symbol": "KO", "quantity": "5"}]},
     {"ok": True, "complete": False, "unpriced": [{"symbol": "KO", "why": "no price"}]}),
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: exposure.py run < input.json")))
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
