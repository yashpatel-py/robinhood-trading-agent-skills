#!/usr/bin/env python3
"""policy_check.py - check one order against the user's own [policy] limits.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: an agent that can simulate orders all day needs guardrails the user chose in
advance - a maximum order size, a concentration cap, symbols it must never touch, no trades just
before earnings. These are SOFT limits: this kit checks them before a review (and the optional
confirm gate checks them again before a live order); Robinhood does not enforce them. A limit the
user has not set ("UNSET" or absent) is listed under not_configured, never invented. A limit that
is set but cannot be evaluated (missing estimate, missing earnings date) fails: unknown is never
a pass.

Rules: max_order_usd, max_symbol_pct_household (after the order), symbol_allowlist,
symbol_denylist, earnings_blackout_days (an unverified date counts), max_orders_per_day,
allow_options, allow_crypto, allowed_sessions, max_option_contracts.

Usage:
    python3 policy_check.py run < input.json > output.json
    python3 policy_check.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash.
"""

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

VERSION = "2.0.0"
SCRIPT = "policy_check"
UNSET = "UNSET"
CENT = Decimal("0.01")
TENTH = Decimal("0.1")
SOFT_NOTE = ("These are your own soft limits, checked by this kit before a review; Robinhood does not "
             "enforce them.")
FAMILY = {
    "review_equity_order": "equity", "place_equity_order": "equity",
    "review_advanced_order": "oco", "place_advanced_order": "oco",
    "review_option_order": "option", "place_option_order": "option",
    "preview_crypto_order": "crypto", "place_crypto_order": "crypto",
}
RULES = ["max_order_usd", "max_symbol_pct_household", "symbol_allowlist", "symbol_denylist",
         "earnings_blackout_days", "max_orders_per_day", "allow_options", "allow_crypto", "allowed_sessions",
         "max_option_contracts"]
CAP_RULES = ("max_order_usd", "max_symbol_pct_household", "max_orders_per_day", "max_option_contracts")


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


class BadValue(Exception):
    pass


def _dec(value):
    if value is None or isinstance(value, bool):
        raise BadValue("expected a decimal")
    text = repr(value) if isinstance(value, float) else str(value).strip()
    if not re.match(r"^[+-]?(\d+(\.\d*)?|\.\d+)$", text):
        raise BadValue("expected a decimal, got %r" % (value,))
    return Decimal(text)


def _int(value):
    if isinstance(value, bool):
        raise BadValue("expected a whole number")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.match(r"^\s*[+-]?\d+\s*$", value):
        return int(value)
    raise BadValue("expected a whole number, got %r" % (value,))


def money(d):
    return str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def pct1(d):
    return float(d.quantize(TENTH, rounding=ROUND_HALF_UP))


def is_unset(value):
    return value is None or value == UNSET or (isinstance(value, list) and not value)


def base_symbol(sym, family):
    s = str(sym or "").strip().upper()
    if family == "crypto" and s.endswith("-USD"):
        s = s[:-4]
    return s


def policy_sha(policy):
    text = json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class Checker(object):
    def __init__(self, data):
        self.data = data
        self.policy = data.get("policy") or {}
        order = data.get("order") or {}
        self.tool = str(order.get("tool", "")).rsplit("__", 1)[-1]
        if self.tool not in FAMILY:
            raise InputError("UNKNOWN_TOOL", "order.tool", "order.tool must be a review/preview/place order tool")
        self.family = FAMILY[self.tool]
        self.params = order.get("params") or {}
        if not isinstance(self.params, dict):
            raise InputError("BAD_INPUT", "order.params", "order.params must be an object")
        raw_symbol = order.get("symbol") or self.params.get("symbol") or self.params.get("chain_symbol")
        self.symbol = base_symbol(raw_symbol, self.family) if raw_symbol else None
        self.side = self._side()
        self.checks, self.violations, self.not_configured = [], [], []

    def _side(self):
        if self.family in ("equity", "oco", "crypto"):
            side = str(self.params.get("side", "")).strip().lower()
            return side if side in ("buy", "sell") else None
        direction = str(self.params.get("direction", "")).strip().lower()
        if direction == "debit":
            return "buy"
        if direction == "credit":
            return "sell"
        legs = self.params.get("legs") or []
        if len(legs) == 1 and isinstance(legs[0], dict):
            side = str(legs[0].get("side", "")).strip().lower()
            return side if side in ("buy", "sell") else None
        return None

    def add(self, rule, limit, actual, passed, code=None, msg=None):
        self.checks.append({"rule": rule, "limit": limit, "actual": actual, "pass": passed})
        if not passed:
            if code is None:
                code = "POLICY_CAP" if rule in CAP_RULES else "POLICY_DENY"
            self.violations.append({"rule": rule, "code": code, "msg": msg or rule})

    def unknown(self, rule, limit, why):
        self.add(rule, limit, None, False, "POLICY_UNKNOWN", "cannot evaluate %s: %s (unknown is not a pass)" % (rule, why))

    def bad(self, rule, exc):
        self.add(rule, self.policy.get(rule), None, False, "BAD_POLICY_VALUE",
                 "your [policy] %s value is malformed (%s); fix it in the config" % (rule, exc))

    def run(self):
        for rule in RULES:
            value = self.policy.get(rule)
            if is_unset(value):
                self.not_configured.append(rule)
                continue
            try:
                getattr(self, "rule_" + rule)(value)
            except BadValue as exc:
                self.bad(rule, exc)

    # -- rules ---------------------------------------------------------------------------------
    def estimate(self):
        raw = self.data.get("estimate_usd")
        if raw is None:
            return None
        try:
            return _dec(raw)
        except BadValue:
            return None

    def rule_max_order_usd(self, value):
        limit = _dec(value)
        est = self.estimate()
        if est is None:
            return self.unknown("max_order_usd", money(limit), "no estimate_usd for this order")
        self.add("max_order_usd", money(limit), money(est), est <= limit,
                 msg="order estimate $%s is over your max order of $%s" % (money(est), money(limit)))

    def rule_max_symbol_pct_household(self, value):
        limit = _dec(value)
        if self.side == "sell":
            self.add("max_symbol_pct_household", str(limit), "sell (reduces this symbol)", True)
            return
        household = self.data.get("household") or {}
        est = self.estimate()
        if not self.symbol:
            return self.unknown("max_symbol_pct_household", str(limit), "order symbol unknown")
        if est is None or household.get("total_usd") is None:
            return self.unknown("max_symbol_pct_household", str(limit), "needs estimate_usd and household.total_usd")
        try:
            total = _dec(household["total_usd"])
            by_symbol = household.get("by_symbol") or {}
            current = _dec(by_symbol.get(self.symbol, "0"))
        except BadValue as exc:
            return self.unknown("max_symbol_pct_household", str(limit), "household values malformed (%s)" % exc)
        new_total = total if household.get("includes_cash") is True else total + est
        if new_total <= 0:
            return self.unknown("max_symbol_pct_household", str(limit), "household total is zero")
        after = (current + est) / new_total * 100
        self.add("max_symbol_pct_household", str(limit), "%s%%" % pct1(after), after <= limit,
                 msg="%s would be %.1f%% of the household after this order; your max is %s%%"
                     % (self.symbol, pct1(after), limit))

    def rule_symbol_allowlist(self, value):
        allowed = {base_symbol(s, self.family) for s in value}
        if not self.symbol:
            return self.unknown("symbol_allowlist", sorted(allowed), "order symbol unknown")
        self.add("symbol_allowlist", sorted(allowed), self.symbol, self.symbol in allowed,
                 msg="%s is not on your symbol allowlist" % self.symbol)

    def rule_symbol_denylist(self, value):
        denied = {base_symbol(s, self.family) for s in value}
        if not self.symbol:
            return self.unknown("symbol_denylist", sorted(denied), "order symbol unknown")
        self.add("symbol_denylist", sorted(denied), self.symbol, self.symbol not in denied,
                 msg="%s is on your symbol denylist" % self.symbol)

    def rule_earnings_blackout_days(self, value):
        days = _int(value)
        if days < 0:
            raise BadValue("must be 0 or more")
        if self.family == "crypto":
            self.add("earnings_blackout_days", days, "crypto (no earnings)", True)
            return
        if not self.symbol:
            return self.unknown("earnings_blackout_days", days, "order symbol unknown")
        earnings = (self.data.get("earnings") or {}).get(self.symbol)
        if not isinstance(earnings, dict):
            return self.unknown("earnings_blackout_days", days,
                                "no earnings entry for %s (get_earnings_results {symbol})" % self.symbol)
        as_of_raw = self.data.get("as_of")
        try:
            as_of = datetime.strptime(as_of_raw, "%Y-%m-%d").date() if as_of_raw else \
                datetime.now(timezone.utc).date()
        except (TypeError, ValueError):
            return self.unknown("earnings_blackout_days", days, "as_of is not YYYY-MM-DD")
        report = earnings.get("date")
        if report in (None, ""):
            self.add("earnings_blackout_days", days, "no upcoming report", True)
            return
        try:
            report_date = datetime.strptime(str(report), "%Y-%m-%d").date()
        except ValueError:
            return self.unknown("earnings_blackout_days", days, "earnings date is not YYYY-MM-DD")
        until = (report_date - as_of).days
        verified = earnings.get("verified") is True
        hit = 0 <= until <= days
        label = "report %s in %d days%s" % (report_date.isoformat(), until, "" if verified else " (unverified date)")
        self.add("earnings_blackout_days", days, label, not hit,
                 msg="%s reports %s, inside your %d-day earnings blackout%s" % (
                     self.symbol, report_date.isoformat(), days,
                     "" if verified else "; the date is unverified and counts as a hit"))

    def rule_max_orders_per_day(self, value):
        limit = _int(value)
        today = self.data.get("orders_today")
        if today is None or isinstance(today, bool):
            return self.unknown("max_orders_per_day", limit, "orders_today not given")
        try:
            count = _int(today)
        except BadValue:
            return self.unknown("max_orders_per_day", limit, "orders_today is not a whole number")
        self.add("max_orders_per_day", limit, count + 1, count + 1 <= limit,
                 msg="this would be order %d today; your max is %d" % (count + 1, limit))

    def rule_allow_options(self, value):
        if not isinstance(value, bool):
            raise BadValue("expected true or false")
        passed = value or self.family != "option"
        self.add("allow_options", value, self.family, passed, msg="your policy does not allow option orders")

    def rule_allow_crypto(self, value):
        if not isinstance(value, bool):
            raise BadValue("expected true or false")
        passed = value or self.family != "crypto"
        self.add("allow_crypto", value, self.family, passed, msg="your policy does not allow crypto orders")

    def rule_allowed_sessions(self, value):
        if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
            raise BadValue("expected a list of session names")
        allowed = [s.strip().lower() for s in value]
        if self.family == "crypto":
            self.add("allowed_sessions", allowed, "crypto (no market_hours)", True)
            return
        session = str(self.params.get("market_hours") or "regular_hours").strip().lower()
        self.add("allowed_sessions", allowed, session, session in allowed,
                 msg="market_hours %s is not in your allowed sessions" % session)

    def rule_max_option_contracts(self, value):
        limit = _int(value)
        if self.family != "option":
            self.add("max_option_contracts", limit, "not an option order", True)
            return
        try:
            qty = _int(self.params.get("quantity"))
        except BadValue:
            return self.unknown("max_option_contracts", limit, "option quantity is not a whole number")
        ratios = [leg.get("ratio_quantity", 1) for leg in (self.params.get("legs") or []) if isinstance(leg, dict)]
        ratios = [r for r in ratios if isinstance(r, int) and not isinstance(r, bool)] or [1]
        contracts = qty * max(ratios)
        self.add("max_option_contracts", limit, contracts, contracts <= limit,
                 msg="%d contracts per leg is over your max of %d" % (contracts, limit))


def op_run(data):
    policy = data.get("policy")
    if policy is not None and not isinstance(policy, dict):
        raise InputError("BAD_INPUT", "policy", "policy must be the [policy] section as an object")
    if not isinstance(data.get("order"), dict):
        raise InputError("MISSING_FIELD", "order", "give order {tool, params}")
    checker = Checker(data)
    checker.run()
    return {
        "ok": True,
        "pass": not checker.violations,
        "checks": checker.checks,
        "violations": checker.violations,
        "not_configured": checker.not_configured,
        "policy_sha": policy_sha(policy or {}),
        "symbol": checker.symbol,
        "note": SOFT_NOTE,
    }


def check(data):
    """Library entry point (hooks/lib and the confirm gate import this)."""
    return run("run", data)


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
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
SCHEMAS = {
    "run": {
        "input": {"type": "object", "required": ["order"], "additionalProperties": False, "properties": {
            "policy": {"type": "object"},
            "order": {"type": "object", "required": ["tool", "params"],
                      "properties": {"tool": _S, "params": {"type": "object"}, "symbol": _S}},
            "estimate_usd": {"type": ["string", "null"]},
            "household": {"type": "object", "properties": {"total_usd": _S, "by_symbol": {"type": "object"},
                                                           "includes_cash": {"type": "boolean"}}},
            "earnings": {"type": "object"},
            "session": _S,
            "orders_today": {"type": ["integer", "null"]},
            "as_of": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        }},
        "output": {"anyOf": [{"type": "object", "additionalProperties": False,
                              "required": ["ok", "pass", "checks", "violations", "not_configured", "policy_sha"],
                              "properties": {
                                  "ok": {"enum": [True]}, "pass": {"type": "boolean"},
                                  "checks": {"type": "array", "items": {"type": "object",
                                                                        "required": ["rule", "limit", "actual", "pass"]}},
                                  "violations": {"type": "array", "items": {"type": "object",
                                                                            "required": ["rule", "code", "msg"]}},
                                  "not_configured": {"type": "array", "items": {"enum": RULES}},
                                  "policy_sha": {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"},
                                  "symbol": {"type": ["string", "null"]}, "note": _S}}, _ERR]},
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
    return {"script": SCRIPT, "version": VERSION, "ops": ops, "rules": RULES}


_ORDER = {"tool": "review_equity_order", "params": {"account_number": "demo-X4F1", "symbol": "PLTR", "side": "buy",
                                                    "type": "limit", "quantity": "10", "limit_price": "31.24",
                                                    "market_hours": "all_day_hours"}}
EXAMPLES = [
    ("run", {"policy": {"max_order_usd": "500.00", "max_symbol_pct_household": "10", "max_orders_per_day": "UNSET"},
             "order": _ORDER, "estimate_usd": "312.40",
             "household": {"total_usd": "14880.00", "by_symbol": {"PLTR": "238.00"}},
             "earnings": {"PLTR": {"date": "2027-02-02", "verified": False}}, "as_of": "2026-11-16"},
     {"ok": True, "pass": True,
      "checks": [{"rule": "max_order_usd", "limit": "500.00", "actual": "312.40", "pass": True},
                 {"rule": "max_symbol_pct_household", "actual": "3.6%", "pass": True}],
      "not_configured": ["symbol_allowlist", "symbol_denylist", "earnings_blackout_days", "max_orders_per_day",
                         "allow_options", "allow_crypto", "allowed_sessions", "max_option_contracts"]}),
    ("run", {"policy": {"max_order_usd": "300", "earnings_blackout_days": "5", "allowed_sessions": ["regular_hours"]},
             "order": _ORDER, "estimate_usd": "312.40",
             "earnings": {"PLTR": {"date": "2026-11-18", "verified": False}}, "as_of": "2026-11-16"},
     {"ok": True, "pass": False,
      "violations": [{"rule": "max_order_usd", "code": "POLICY_CAP"},
                     {"rule": "earnings_blackout_days", "code": "POLICY_DENY"},
                     {"rule": "allowed_sessions", "code": "POLICY_DENY"}]}),
    ("run", {"policy": {"max_order_usd": "500"}, "order": _ORDER},
     {"ok": True, "pass": False, "violations": [{"rule": "max_order_usd", "code": "POLICY_UNKNOWN"}]}),
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: policy_check.py run < input.json")))
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
