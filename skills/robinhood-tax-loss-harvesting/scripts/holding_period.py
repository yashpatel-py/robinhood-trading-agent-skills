#!/usr/bin/env python3
"""holding_period.py - short- or long-term for each tax lot, and when each short-term lot turns long-term.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: a lot sold one day too early is taxed at short-term (ordinary income) rates, and the
off-by-one is easy to get wrong. IRS Pub 550: the holding period starts the day AFTER you acquire the
shares and long-term means held MORE than one year. So a lot bought 2025-12-03 is long-term when sold
on or after 2026-12-04, not on 2026-12-03. A lot acquired on the LAST day of a month, whatever the month's
length, is long-term from the first day of the 13th month after it (Rev. Rul. 66-7): bought 2027-02-28 ->
long-term from 2028-03-01 (not 2028-02-29, which exists in the leap year); bought 2024-02-29 -> long-term
from 2025-03-01. For every other month-end the two readings agree. Dates are trade dates, never
settlement dates.

Ops:
  run   per lot: term on as_of, long_term_on, the first NYSE trading day on or after it, days until
        long-term, unrealized $ at the price you pass, whether it crosses within horizon_days, and
        whether the broker's reported term agrees. Also a countdown list of short-term lots that turn
        long-term within the horizon (gains first when a price is given).

Usage:
    python3 holding_period.py run < input.json > output.json
    python3 holding_period.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0 whenever
JSON was printed, exit 1 only on a crash.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

VERSION = "2.0.0"
SCRIPT = "holding_period"
CENT = Decimal("0.01")
ZERO = Decimal(0)
DEFAULT_HORIZON = 45

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True  # sibling imports must not leave __pycache__ in a skill folder


def _import_rh_time():
    for folder in (_HERE, os.path.normpath(os.path.join(_HERE, "..", "..", "..", "shared", "scripts"))):
        if os.path.isfile(os.path.join(folder, "rh_time.py")):
            if folder not in sys.path:
                sys.path.insert(0, folder)
            try:
                import rh_time as module  # noqa: E402

                return module
            except Exception:  # pragma: no cover
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


def dec(value, field, required=True, positive=False, nonneg=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InputError("MISSING_FIELD", field, "%s is required" % field)
        return None
    if isinstance(value, bool):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string" % field)
    text = repr(value) if isinstance(value, float) else str(value).strip().replace(",", "")
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


def usd(d):
    q = d.quantize(CENT, rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    whole, frac = str(abs(q)).split(".")
    parts = []
    while len(whole) > 3:
        parts.insert(0, whole[-3:])
        whole = whole[:-3]
    parts.insert(0, whole)
    return "%s$%s.%s" % (sign, ",".join(parts), frac)


def qty(d):
    text = format(d.normalize(), "f")
    return "0" if text in ("-0", "") else text


def parse_day(value, field):
    """A date, or an ISO timestamp converted to its US Eastern calendar date (never read as a UTC date)."""
    if not isinstance(value, str) or not value.strip():
        raise InputError("BAD_DATE", field, "expected YYYY-MM-DD (got %r)" % (value,))
    text = value.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError as exc:
            raise InputError("BAD_DATE", field, "not a real calendar date: %s" % exc)
    if rh_time is None:
        raise InputError("RH_TIME_MISSING", field, "rh_time.py is not next to this script; pass acquisition dates as "
                                                   "YYYY-MM-DD")
    out = rh_time.run("to_et", {"utc": text})
    if not out.get("ok"):
        raise InputError("BAD_DATE", field, out["errors"][0]["msg"])
    return datetime.strptime(out["date_et"], "%Y-%m-%d").date()


def long_term_on(acquired):
    """First day the lot is long-term -> (date, False).

    The day after the one-year anniversary (Pub 550), except that a lot acquired on the last day of a month is
    long-term from the first day of the 13th month after it (Rev. Rul. 66-7): 2027-02-28 -> 2028-03-01,
    2024-02-29 -> 2025-03-01, 2025-01-31 -> 2026-02-01. The second value is the old leap-day flag, kept for
    callers and the output schema; Rev. Rul. 66-7 settles the Feb 29 case, so it is always False.
    """
    nxt = acquired + timedelta(days=1)
    if nxt.day == 1:  # acquired on the last day of its month
        return date(nxt.year + 1, nxt.month, 1), False
    return acquired.replace(year=acquired.year + 1) + timedelta(days=1), False


def month_end_rule_moves_date(acquired):
    """True when Rev. Rul. 66-7 gives a different date than 'the day after the calendar anniversary' would (a lot
    bought Feb 28 before a leap year), or when that anniversary does not exist (a lot bought Feb 29)."""
    if acquired.month != 2 or (acquired + timedelta(days=1)).day != 1:
        return False
    try:
        naive = acquired.replace(year=acquired.year + 1) + timedelta(days=1)
    except ValueError:
        return True
    return naive != long_term_on(acquired)[0]


def normalize_term(value):
    """Robinhood's lot `term` ("long_term", "Long", "short") -> "long" | "short" | the raw text | None."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    return "long" if text.startswith("long") else "short" if text.startswith("short") else text


def first_trading_day(d):
    if rh_time is None:
        return None, "rh_time.py not found next to this script; first trading day not computed"
    out = rh_time.run("next_trading_day", {"date": d.isoformat(), "inclusive": True})
    if not out.get("ok"):
        return None, out["errors"][0]["msg"]
    return out["date"], None


def op_run(data):
    as_of = parse_day(data.get("as_of"), "as_of")
    price = dec(data.get("price"), "price", required=False, positive=True)
    prices = data.get("prices") or {}
    if not isinstance(prices, dict):
        raise InputError("BAD_VALUE", "prices", "prices maps SYMBOL to a decimal string")
    horizon = data.get("horizon_days", DEFAULT_HORIZON)
    try:
        horizon = int(str(horizon).strip())
    except ValueError:
        raise InputError("BAD_VALUE", "horizon_days", "horizon_days must be a whole number of days")
    if horizon < 0:
        raise InputError("BAD_VALUE", "horizon_days", "horizon_days must not be negative")
    lots = data.get("lots")
    if not isinstance(lots, list) or not lots:
        raise InputError("MISSING_FIELD", "lots", "give lots: [{lot_id, acquired, shares, cost_per_share}]")
    out_lots, notes = [], []
    short_u = long_u = ZERO
    priced_any = False
    for i, lot in enumerate(lots):
        where = "lots[%d]" % i
        if not isinstance(lot, dict):
            raise InputError("BAD_VALUE", where, "each lot is an object")
        acquired = parse_day(lot.get("acquired") or lot.get("open_date"), where + ".acquired")
        if acquired > as_of:
            raise InputError("BAD_DATE", where + ".acquired", "acquired %s is after as_of %s" % (acquired, as_of))
        shares = dec(lot.get("shares", lot.get("quantity")), where + ".shares", positive=True)
        cost = dec(lot.get("cost_per_share"), where + ".cost_per_share", required=False, nonneg=True)
        sym = str(lot.get("symbol") or "").strip().upper() or None
        px = price
        if sym and sym in prices:
            px = dec(prices[sym], "prices.%s" % sym, positive=True)
        lt_on, amb = long_term_on(acquired)
        term = "long" if as_of >= lt_on else "short"
        days = max(0, (lt_on - as_of).days)
        row = {"lot_id": str(lot.get("lot_id") or lot.get("open_lot_id") or "lot%d" % (i + 1)),
               "acquired": acquired.isoformat(), "shares": qty(shares), "term": term,
               "long_term_on": lt_on.isoformat(), "days_until_long_term": days,
               "crosses_within_horizon": term == "short" and days <= horizon,
               "leap_day_ambiguous": amb}
        if sym:
            row["symbol"] = sym
        if lot.get("account_last4"):
            row["account_last4"] = str(lot["account_last4"]).strip()[-4:]
        ftd, why = first_trading_day(lt_on)
        row["first_trading_day_long_term"] = ftd
        if why and why not in notes:
            notes.append(why)
        if month_end_rule_moves_date(acquired):
            row["month_end_note"] = ("acquired %s, the last day of February: long-term from the first day of the "
                                     "13th month after it, %s (Rev. Rul. 66-7), not the day after the calendar "
                                     "anniversary" % (acquired.isoformat(), lt_on.isoformat()))
        broker_term = normalize_term(lot.get("term"))
        if broker_term:
            row["broker_term"] = broker_term
            row["term_agrees"] = broker_term == term
            if broker_term != term:
                notes.append("lot %s: Robinhood reports %s-term but the date arithmetic says %s-term on %s; trust "
                             "Robinhood's tax documents and ask them if it matters" % (row["lot_id"], broker_term,
                                                                                    term, as_of.isoformat()))
        if cost is None:
            row["unrealized_usd"] = None
            row["basis_pending"] = True
        elif px is not None:
            u = (px - cost) * shares
            row["unrealized_usd"] = money(u)
            row["unrealized_per_share"] = money(px - cost)
            priced_any = True
            if term == "long":
                long_u += u
            else:
                short_u += u
        out_lots.append(row)
    countdown = [r for r in out_lots if r["crosses_within_horizon"]]

    def gain_first(r):
        u = r.get("unrealized_usd")
        return (0 if (u is not None and Decimal(u) > 0) else 1, r["days_until_long_term"], r["lot_id"])

    countdown.sort(key=gain_first)
    gains = [r for r in countdown if r.get("unrealized_usd") is not None and Decimal(r["unrealized_usd"]) > 0]
    if gains:
        total = sum((Decimal(r["unrealized_usd"]) for r in gains), ZERO)
        nxt = min(gains, key=lambda r: r["days_until_long_term"])
        label = nxt.get("symbol") or "lot %s" % nxt["lot_id"]
        line = ("NO ACTION: Dollars at stake: %s of short-term gain in %d lot(s) turns long-term within %d days "
                "(next: %s on %s, %d days)" % (usd(total), len(gains), horizon, label, nxt["long_term_on"],
                                               nxt["days_until_long_term"]))
    elif countdown:
        line = ("NO ACTION: Dollars at stake: none computed (%d short-term lot(s) turn long-term within %d days; no "
                "price given or no gains)" % (len(countdown), horizon))
    else:
        line = "NO ACTION: Dollars at stake: none found (no short-term lot turns long-term within %d days)" % horizon
    return {
        "ok": True,
        "as_of": as_of.isoformat(),
        "horizon_days": horizon,
        "lots": out_lots,
        "countdown": [r["lot_id"] for r in countdown],
        "countdown_gains": [r["lot_id"] for r in gains],
        "unrealized_short_usd": money(short_u) if priced_any else None,
        "unrealized_long_usd": money(long_u) if priced_any else None,
        "status_line": line,
        "rule": ("holding period starts the day after acquisition; long-term = held more than one year, so from the "
                 "day after the one-year anniversary (IRS Pub 550); a lot acquired on the last day of a month is "
                 "long-term from the first day of the 13th month after it (Rev. Rul. 66-7); trade dates, not "
                 "settlement dates"),
        "notes": notes,
    }


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
    except (TypeError, AttributeError, ValueError, KeyError, IndexError, ArithmeticError) as exc:
        # A field of the wrong JSON type (a list where an object belongs, a number where a string belongs).
        return _err("BAD_INPUT", "", "input has an unexpected shape (%s: %s); compare it with --schema"
                    % (type(exc).__name__, exc))


# ---------------------------------------------------------------------------------------------
# JSON Schemas and self-test
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
_LOT_OUT = {"type": "object", "required": ["lot_id", "term", "long_term_on", "first_trading_day_long_term",
                                           "days_until_long_term", "crosses_within_horizon", "leap_day_ambiguous"],
            "properties": {"lot_id": _S, "acquired": _DATE, "shares": _S, "term": {"enum": ["short", "long"]},
                           "long_term_on": _DATE, "first_trading_day_long_term": _SN,
                           "days_until_long_term": {"type": "integer"}, "unrealized_usd": _SN,
                           "crosses_within_horizon": {"type": "boolean"}, "leap_day_ambiguous": {"type": "boolean"}}}
SCHEMAS = {
    "run": {
        "input": {"type": "object", "required": ["as_of", "lots"], "additionalProperties": False, "properties": {
            "as_of": _DATE, "price": _S, "prices": {"type": "object"}, "horizon_days": {"type": ["integer", "string"]},
            "lots": {"type": "array", "items": {"type": "object", "required": ["acquired"], "properties": {
                "lot_id": _S, "open_lot_id": _S, "symbol": _S, "account_last4": _S, "acquired": _S,
                "open_date": _S, "shares": _S, "quantity": _S, "cost_per_share": _SN, "term": _S}}}}},
        "output": {"anyOf": [{"type": "object", "required": ["ok", "as_of", "lots", "countdown", "status_line", "rule"],
                              "properties": {"ok": {"enum": [True]}, "lots": {"type": "array", "items": _LOT_OUT},
                                             "countdown": {"type": "array"}, "status_line": _S}}, _ERR]},
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


EXAMPLES = [
    ("run", {"as_of": "2026-11-16", "price": "228.10", "horizon_days": 45, "lots": [
        {"lot_id": "N2", "symbol": "NVDA", "acquired": "2025-12-03", "shares": "40", "cost_per_share": "181.50"},
        {"lot_id": "N1", "symbol": "NVDA", "acquired": "2025-03-10", "shares": "100", "cost_per_share": "118.00"}]},
     {"ok": True, "countdown": ["N2"], "lots": [
         {"lot_id": "N2", "term": "short", "long_term_on": "2026-12-04", "first_trading_day_long_term": "2026-12-04",
          "days_until_long_term": 18, "unrealized_usd": "1864.00", "crosses_within_horizon": True},
         {"lot_id": "N1", "term": "long", "long_term_on": "2026-03-11", "days_until_long_term": 0}]}),
    ("run", {"as_of": "2026-09-22", "lots": [{"lot_id": "A", "acquired": "2025-10-03", "shares": "1"}]},
     {"ok": True, "lots": [{"lot_id": "A", "term": "short", "long_term_on": "2026-10-04",
                            "first_trading_day_long_term": "2026-10-05", "leap_day_ambiguous": False}]}),
    ("run", {"as_of": "2026-01-02", "lots": [{"lot_id": "F", "acquired": "2024-02-29", "shares": "1"}]},
     {"ok": True, "lots": [{"lot_id": "F", "term": "long", "long_term_on": "2025-03-01",
                            "leap_day_ambiguous": False}]}),
    ("run", {"as_of": "2027-12-01", "lots": [{"lot_id": "P", "acquired": "2027-02-28", "shares": "1"}]},
     {"ok": True, "lots": [{"lot_id": "P", "term": "short", "long_term_on": "2028-03-01",
                            "leap_day_ambiguous": False}]}),
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: holding_period.py run < input.json")))
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
