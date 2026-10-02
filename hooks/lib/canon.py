#!/usr/bin/env python3
# synced from shared/scripts/canon.py; do not edit
"""canon.py - canonical form and fingerprint of an order ticket (review or place parameters).

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: a reviewed order and the order later placed must be provably the same order.
The review and place tools spell the same order differently (review_option_order takes
chain_symbol and underlying_type, which place_option_order rejects; defaults may be omitted or
explicit; "10.00" vs "10"; legs in any order). This script removes those differences and hashes
what is left, so "same order" becomes "same fingerprint". The confirm gate, the audit ledger and
the ticket_id shown to the user all use it. An identical copy lives at hooks/lib/canon.py.

Canonicalization:
  1. drop ref_id, chain_symbol, underlying_type (and keys whose value is null)
  2. fill documented defaults - equity: time_in_force gfd, market_hours regular_hours;
     option: type limit, time_in_force gfd, market_hours regular_hours, leg ratio_quantity 1;
     OCO: market_hours regular_hours; crypto: time_in_force gtc for market/limit,
     gfd for stop_loss/stop_limit
  3. lowercase enum strings; 4. uppercase equity/OCO symbols (crypto symbols stay as given:
     BTC and BTC-USD are different inputs and need a fresh review)
  5. normalize decimals ("10.00" -> "10"); 6. sort legs by option_id, tax_lots by open_lot_id
  7. sort keys, compact JSON; 8. fingerprint = sha256(family + "\\n" + canonical_json)
The account number is part of the hash input but is never printed (outputs show ****last4).

Usage:
    python3 canon.py run|compare < input.json > output.json
    python3 canon.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash.
"""

import copy
import hashlib
import json
import re
import sys
from decimal import Decimal, InvalidOperation

VERSION = "2.0.0"
SCRIPT = "canon"

FAMILY_BY_TOOL = {
    "review_equity_order": "equity",
    "place_equity_order": "equity",
    "review_advanced_order": "oco",
    "place_advanced_order": "oco",
    "review_option_order": "option",
    "place_option_order": "option",
    "preview_crypto_order": "crypto",
    "place_crypto_order": "crypto",
}
PLACE_TWIN = {
    "review_equity_order": "place_equity_order",
    "review_advanced_order": "place_advanced_order",
    "review_option_order": "place_option_order",
    "preview_crypto_order": "place_crypto_order",
}
REVIEW_TWIN = {v: k for k, v in PLACE_TWIN.items()}

DROP_KEYS = ("ref_id", "chain_symbol", "underlying_type")
ENUM_KEYS = ("side", "type", "time_in_force", "market_hours", "direction")
LEG_ENUM_KEYS = ("side", "position_effect")
DECIMAL_KEYS = ("quantity", "dollar_amount", "limit_price", "stop_price", "price",
                "take_profit_limit_price", "stop_loss_stop_price")
ACCOUNT_KEYS = ("account_number", "rhs_account_number")
MASK = "\u2022\u2022\u2022\u2022"


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


def bare_tool_name(tool):
    """mcp__<server>__review_equity_order -> review_equity_order."""
    return tool.rsplit("__", 1)[-1] if isinstance(tool, str) else tool


def mask_account(value):
    s = str(value).strip()
    return MASK + s[-4:] if s else s


def normalize_decimal(value):
    """"10.00" -> "10", "0.50" -> "0.5", "1E+2" -> "100". Non-decimal strings pass through."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        value = repr(value)
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return value
    text = value.strip()
    try:
        d = Decimal(text)
    except InvalidOperation:
        return value
    if not d.is_finite():
        return value
    out = format(d, "f")
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    if out in ("-0", ""):
        out = "0"
    return out


def _lower(value):
    return value.strip().lower() if isinstance(value, str) else value


def _ratio(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and re.match(r"^\s*\d+\s*$", value):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _sort_key(item, key):
    if isinstance(item, dict):
        return (str(item.get(key, "")), json.dumps(item, sort_keys=True))
    return ("", json.dumps(item, sort_keys=True))


def canonicalize(tool, params):
    """Returns (family, canonical_params). The canonical dict still holds full account numbers;
    use masked_view() before printing it."""
    bare = bare_tool_name(tool)
    if bare not in FAMILY_BY_TOOL:
        raise InputError("UNKNOWN_TOOL", "tool", "canon covers review/place/preview order tools only, not %r" % tool)
    if not isinstance(params, dict):
        raise InputError("BAD_INPUT", "params", "params must be an object")
    family = FAMILY_BY_TOOL[bare]
    p = {k: copy.deepcopy(v) for k, v in params.items() if v is not None and k not in DROP_KEYS}

    for k in ENUM_KEYS:
        if k in p:
            p[k] = _lower(p[k])
    for k in ACCOUNT_KEYS:
        if isinstance(p.get(k), str):
            p[k] = p[k].strip()
    for k in DECIMAL_KEYS:
        if k in p:
            p[k] = normalize_decimal(p[k])

    if family in ("equity", "oco") and isinstance(p.get("symbol"), str):
        p["symbol"] = p["symbol"].strip().upper()

    if family == "equity":
        p.setdefault("time_in_force", "gfd")
        p.setdefault("market_hours", "regular_hours")
    elif family == "oco":
        p.setdefault("market_hours", "regular_hours")
    elif family == "option":
        p.setdefault("type", "limit")
        p.setdefault("time_in_force", "gfd")
        p.setdefault("market_hours", "regular_hours")
    elif family == "crypto":
        if "time_in_force" not in p:
            if p.get("type") in ("stop_loss", "stop_limit"):
                p["time_in_force"] = "gfd"
            elif p.get("type") in ("market", "limit"):
                p["time_in_force"] = "gtc"

    if isinstance(p.get("legs"), list):
        legs = []
        for leg in p["legs"]:
            if isinstance(leg, dict):
                leg = {k: v for k, v in leg.items() if v is not None}
                for k in LEG_ENUM_KEYS:
                    if k in leg:
                        leg[k] = _lower(leg[k])
                leg["ratio_quantity"] = _ratio(leg.get("ratio_quantity", 1))
                if isinstance(leg.get("option_id"), str):
                    leg["option_id"] = leg["option_id"].strip().lower()
            legs.append(leg)
        p["legs"] = sorted(legs, key=lambda x: _sort_key(x, "option_id"))

    if isinstance(p.get("tax_lots"), list):
        lots = []
        for lot in p["tax_lots"]:
            if isinstance(lot, dict):
                lot = {k: v for k, v in lot.items() if v is not None}
                if "quantity" in lot:
                    lot["quantity"] = normalize_decimal(lot["quantity"])
                if isinstance(lot.get("open_lot_id"), str):
                    lot["open_lot_id"] = lot["open_lot_id"].strip()
            lots.append(lot)
        p["tax_lots"] = sorted(lots, key=lambda x: _sort_key(x, "open_lot_id"))
    return family, p


def canonical_json(canonical):
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fingerprint(tool, params):
    """Library entry point used by order_lint.py, hooks/audit_log.py and the confirm gate."""
    family, canonical = canonicalize(tool, params)
    digest = hashlib.sha256((family + "\n" + canonical_json(canonical)).encode("utf-8")).hexdigest()
    return {"family": family, "canonical": canonical, "fingerprint": "sha256:" + digest, "ticket_id": digest[:6]}


def masked_view(canonical):
    out = copy.deepcopy(canonical)
    for k in ACCOUNT_KEYS:
        if k in out and isinstance(out[k], str):
            out[k] = mask_account(out[k])
    return out


def diff_fields(canon_a, canon_b):
    """Top-level keys whose canonical values differ (legs/tax_lots compared as whole lists)."""
    keys = sorted(set(canon_a) | set(canon_b))
    return [k for k in keys if canon_a.get(k) != canon_b.get(k)]


def op_run(data):
    tool = data.get("tool")
    if not tool:
        raise InputError("MISSING_FIELD", "tool", "give the order tool name, e.g. review_equity_order")
    fp = fingerprint(tool, data.get("params", {}))
    bare = bare_tool_name(tool)
    return {
        "ok": True,
        "tool": bare,
        "family": fp["family"],
        "canonical": masked_view(fp["canonical"]),
        "fingerprint": fp["fingerprint"],
        "ticket_id": fp["ticket_id"],
        "place_tool": PLACE_TWIN.get(bare, bare),
        "review_tool": REVIEW_TWIN.get(bare, bare),
    }


def op_compare(data):
    a, b = data.get("a"), data.get("b")
    if not isinstance(a, dict) or not isinstance(b, dict):
        raise InputError("MISSING_FIELD", "a", "give a and b, each {tool, params}")
    fa = fingerprint(a.get("tool"), a.get("params", {}))
    fb = fingerprint(b.get("tool"), b.get("params", {}))
    diffs = diff_fields(fa["canonical"], fb["canonical"])
    if fa["family"] != fb["family"]:
        diffs = ["family"] + diffs
    return {
        "ok": True,
        "equal": fa["fingerprint"] == fb["fingerprint"],
        "fingerprint_a": fa["fingerprint"],
        "fingerprint_b": fb["fingerprint"],
        "diff_fields": diffs,
    }


OPS = {"run": op_run, "compare": op_compare}


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
_FP = {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"}
_ORDER = {"type": "object", "required": ["tool", "params"],
          "properties": {"tool": {"type": "string"}, "params": {"type": "object"}}}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}

SCHEMAS = {
    "run": {
        "input": _ORDER,
        "output": {"anyOf": [{"type": "object", "additionalProperties": False,
                              "required": ["ok", "family", "canonical", "fingerprint", "ticket_id"],
                              "properties": {"ok": {"enum": [True]}, "tool": _S,
                                             "family": {"enum": ["equity", "oco", "option", "crypto"]},
                                             "canonical": {"type": "object"}, "fingerprint": _FP,
                                             "ticket_id": {"type": "string", "pattern": r"^[0-9a-f]{6}$"},
                                             "place_tool": _S, "review_tool": _S}}, _ERR]},
    },
    "compare": {
        "input": {"type": "object", "required": ["a", "b"], "properties": {"a": _ORDER, "b": _ORDER}},
        "output": {"anyOf": [{"type": "object", "additionalProperties": False,
                              "required": ["ok", "equal", "diff_fields"],
                              "properties": {"ok": {"enum": [True]}, "equal": {"type": "boolean"},
                                             "fingerprint_a": _FP, "fingerprint_b": _FP,
                                             "diff_fields": {"type": "array", "items": _S}}}, _ERR]},
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
    return {"script": SCRIPT, "version": VERSION, "ops": ops}


_ACCT = "demo-X4F1"
_REVIEW_OPT = {"tool": "review_option_order", "params": {
    "account_number": _ACCT, "chain_symbol": "AMD", "underlying_type": "equity", "quantity": "2", "price": "1.50",
    "direction": "debit",
    "legs": [{"option_id": "bbb-leg", "side": "sell", "position_effect": "open"},
             {"option_id": "aaa-leg", "side": "BUY", "position_effect": "open", "ratio_quantity": 1}]}}
_PLACE_OPT = {"tool": "place_option_order", "params": {
    "account_number": _ACCT, "ref_id": "ref-selftest-1", "quantity": "2", "price": "1.5",
    "type": "limit", "time_in_force": "gfd", "market_hours": "regular_hours", "direction": "debit",
    "legs": [{"option_id": "aaa-leg", "side": "buy", "position_effect": "open", "ratio_quantity": 1},
             {"option_id": "bbb-leg", "side": "sell", "position_effect": "open", "ratio_quantity": 1}]}}

EXAMPLES = [
    ("compare", {"a": _REVIEW_OPT, "b": _PLACE_OPT}, {"ok": True, "equal": True, "diff_fields": []}),
    ("compare", {"a": {"tool": "review_equity_order", "params": {"account_number": _ACCT, "symbol": "pltr",
                                                                  "side": "buy", "type": "limit", "quantity": "10.00",
                                                                  "limit_price": "31.240"}},
                 "b": {"tool": "mcp__robinhood-trading__place_equity_order",
                       "params": {"account_number": _ACCT, "symbol": "PLTR", "side": "buy", "type": "limit",
                                  "quantity": "10", "limit_price": "31.24", "time_in_force": "GFD",
                                  "market_hours": "regular_hours", "ref_id": "x"}}},
     {"ok": True, "equal": True}),
    ("compare", {"a": {"tool": "preview_crypto_order", "params": {"rhs_account_number": "demo-5555",
                                                                   "symbol": "BTC", "side": "sell",
                                                                   "type": "stop_loss", "quantity": "0.01",
                                                                   "stop_price": "60000"}},
                 "b": {"tool": "place_crypto_order", "params": {"rhs_account_number": "demo-5555",
                                                                 "symbol": "BTC-USD", "side": "sell",
                                                                 "type": "stop_loss", "quantity": "0.01",
                                                                 "stop_price": "60000", "time_in_force": "gfd"}}},
     {"ok": True, "equal": False, "diff_fields": ["symbol"]}),
    ("run", {"tool": "review_equity_order", "params": {"account_number": _ACCT, "symbol": "PLTR", "side": "buy",
                                                       "type": "limit", "quantity": "10", "limit_price": "31.24"}},
     {"ok": True, "family": "equity", "place_tool": "place_equity_order",
      "canonical": {"account_number": MASK + "X4F1", "time_in_force": "gfd", "market_hours": "regular_hours"}}),
    ("run", {"tool": "get_accounts", "params": {}}, {"ok": False, "errors": [{"code": "UNKNOWN_TOOL"}]}),
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
        if "demo-X4F1" in json.dumps(out):
            problems.append("full account number printed")
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: canon.py run|compare < input.json")))
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
