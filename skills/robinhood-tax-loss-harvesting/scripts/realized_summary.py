#!/usr/bin/env python3
"""realized_summary.py - year-to-date realized gains and losses by account, with IRAs kept separate.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: "what have I realized this year?" is the question behind every harvest decision, and
the connector answers it in two shapes that do not line up. get_pnl_trade_history lists closing trades
(stocks, options, crypto and prediction markets, with no asset-class and no short/long-term field;
option closes appear under the underlying ticker and some rows have no symbol), and get_realized_pnl
returns bucketed totals. Gains inside an IRA or Roth are not taxable in the year, so adding them to the
taxable total overstates the bill. This script adds up the trade rows per account, keeps retirement
accounts out of the taxable total, reconciles each account against the bucketed total, and says plainly
what the connector does not provide: the short/long-term split (see Robinhood's tax center) and
cross-account wash-sale adjustments (Robinhood's 1099 reports wash sales per account only).

Ops:
  run   taxable total, per-account totals with reconciliation, per-symbol totals (taxable accounts),
        gains and losses separately, retirement accounts excluded, rows with no symbol counted apart,
        and the term split only when the rows carry one.

Usage:
    python3 realized_summary.py run < input.json > output.json
    python3 realized_summary.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0 whenever
JSON was printed, exit 1 only on a crash. Account numbers appear only as their last 4 characters.
"""

import json
import os
import re
import sys
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

VERSION = "2.0.0"
SCRIPT = "realized_summary"
CENT = Decimal("0.01")
ZERO = Decimal(0)
TERM_UNAVAILABLE = "not provided by the connector; see Robinhood's tax center (Account > Tax center) or your 1099-B"
PREDICTION_NOTE = ("The trade list includes prediction-market trades (and adjustments, often with no symbol); "
                   "get_realized_pnl filters by equity, option and crypto only, so the two totals can differ by "
                   "those rows.")
WASH_NOTE = ("Figures are before any wash-sale adjustment across accounts: Robinhood's 1099 adjusts wash sales "
             "within each account only, and no 1099 sees a wash between two of your accounts.")

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


def dec(value, field, required=True):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InputError("MISSING_FIELD", field, "%s is required" % field)
        return None
    if isinstance(value, bool):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string" % field)
    text = repr(value) if isinstance(value, float) else str(value).strip().replace(",", "")
    if not _DEC_RE.match(text):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string (got %r)" % (field, value))
    return Decimal(text)


def money(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def usd(d):
    q = d.quantize(CENT, rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else "+"
    whole, frac = str(abs(q)).split(".")
    parts = []
    while len(whole) > 3:
        parts.insert(0, whole[-3:])
        whole = whole[:-3]
    parts.insert(0, whole)
    return "%s$%s.%s" % (sign, ",".join(parts), frac)


def mask(l4):
    return "••••" + l4


def row_date(row, where):
    """Trade date: a given date, or the US Eastern date of the timestamp (never the UTC date)."""
    if row.get("date"):
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(row["date"]).strip())
        if not m:
            raise InputError("BAD_DATE", where + ".date", "expected YYYY-MM-DD")
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError as exc:
            raise InputError("BAD_DATE", where + ".date", "not a real calendar date: %s" % exc)
    ts = row.get("timestamp")
    if not ts:
        return None
    if rh_time is None:
        raise InputError("RH_TIME_MISSING", where + ".timestamp", "rh_time.py is not next to this script; pass "
                                                                  "trade dates as date")
    out = rh_time.run("to_et", {"utc": ts})
    if not out.get("ok"):
        raise InputError("BAD_TIME", where + ".timestamp", out["errors"][0]["msg"])
    return datetime.strptime(out["date_et"], "%Y-%m-%d").date()


def aggregate_value(value, where):
    """A get_realized_pnl result for one account: a decimal string, or {data_points:[{realized_gain}]}."""
    if isinstance(value, dict):
        points = value.get("data_points")
        if not isinstance(points, list):
            if value.get("total") is not None:
                return dec(value.get("total"), where + ".total"), 0
            raise InputError("BAD_VALUE", where, "give the account's get_realized_pnl data_points or a total")
        total, na = ZERO, 0
        for i, p in enumerate(points):
            g = p.get("realized_gain") if isinstance(p, dict) else None
            if g is None:
                na += 1  # a null bucket means n/a (for example transfer-only), not $0
                continue
            total += dec(g, "%s.data_points[%d].realized_gain" % (where, i))
        return total, na
    return dec(value, where), 0


def op_run(data):
    accounts = {}
    raw_accounts = data.get("accounts") or []
    if not isinstance(raw_accounts, list):
        raise InputError("BAD_VALUE", "accounts", "accounts is a list of {last4, type}")
    for i, a in enumerate(raw_accounts):
        where = "accounts[%d]" % i
        if not isinstance(a, dict):
            raise InputError("BAD_VALUE", where, "each account is an object")
        l4 = str(a.get("last4") or a.get("account_last4") or "").strip()[-4:]
        t = str(a.get("type", "")).strip().lower()
        if not l4 or t not in ("taxable", "retirement"):
            raise InputError("BAD_VALUE", where, "each account needs last4 and type (taxable or retirement)")
        accounts[l4] = {"type": t, "label": str(a.get("label") or "").strip() or None,
                        "read_status": str(a.get("read_status") or "complete").strip().lower()}
    if not accounts:
        raise InputError("MISSING_FIELD", "accounts", "list the accounts read, with type taxable or retirement")
    window = str(data.get("window") or "ytd").strip().lower()
    as_of = data.get("as_of")
    year = None
    if window == "ytd":
        if not as_of:
            raise InputError("MISSING_FIELD", "as_of", "as_of (today, YYYY-MM-DD) is required for a ytd window")
        if not isinstance(as_of, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", as_of.strip()):
            raise InputError("BAD_DATE", "as_of", "as_of must be YYYY-MM-DD")
        year = int(as_of.strip()[:4])
    trades = data.get("trades")
    if not isinstance(trades, list):
        raise InputError("MISSING_FIELD", "trades", "give trades: every page of get_pnl_trade_history, with "
                                                    "account_last4 added to each row")
    per_acct = dict((l4, {"total": ZERO, "gains": ZERO, "losses": ZERO, "count": 0, "na": 0}) for l4 in accounts)
    per_symbol, by_class, term = {}, {}, {"short": ZERO, "long": ZERO, "rows": 0}
    unattributed = {"count": 0, "total": ZERO}
    outside = 0
    for i, r in enumerate(trades):
        where = "trades[%d]" % i
        if not isinstance(r, dict):
            raise InputError("BAD_VALUE", where, "each trade is an object")
        l4 = str(r.get("account_last4") or "").strip()[-4:]
        if l4 not in accounts:
            raise InputError("UNKNOWN_ACCOUNT", where + ".account_last4", "account ending %s is not in accounts" % l4)
        d = row_date(r, where)
        if year is not None and d is not None and d.year != year:
            outside += 1
            continue
        g = r.get("realized_usd", r.get("realized_gain"))
        g = dec(g, where + ".realized_gain", required=False)
        a = per_acct[l4]
        if g is None:
            a["na"] += 1
            continue
        a["total"] += g
        a["count"] += 1
        if g >= 0:
            a["gains"] += g
        else:
            a["losses"] += g
        if accounts[l4]["type"] != "taxable":
            continue
        sym = str(r.get("symbol") or "").strip().upper()
        if not sym:
            unattributed["count"] += 1
            unattributed["total"] += g
        else:
            s = per_symbol.setdefault(sym, {"total": ZERO, "gains": ZERO, "losses": ZERO, "count": 0, "accounts": set()})
            s["total"] += g
            s["count"] += 1
            s["accounts"].add(l4)
            if g >= 0:
                s["gains"] += g
            else:
                s["losses"] += g
        cls = str(r.get("asset_class") or r.get("classification") or "").strip().lower()
        cls = {"equity_sale": "equity"}.get(cls, cls) or "unclassified"
        by_class[cls] = by_class.get(cls, ZERO) + g
        t = str(r.get("term") or "").strip().lower()
        if t in ("short", "long"):
            term[t] += g
            term["rows"] += 1

    aggregates = data.get("aggregates") or {}
    if not isinstance(aggregates, dict):
        raise InputError("BAD_VALUE", "aggregates", "aggregates maps account_last4 to its get_realized_pnl result")
    by_account, taxable_total, retirement = [], ZERO, []
    notes = []
    for l4 in sorted(accounts):
        a, info = per_acct[l4], accounts[l4]
        row = {"account_last4": l4, "label": info["label"], "type": info["type"], "realized_usd": money(a["total"]),
               "gains_usd": money(a["gains"]), "losses_usd": money(a["losses"]), "trades": a["count"],
               "rows_without_amount": a["na"], "read_status": info["read_status"]}
        agg_raw = None
        for k in (l4,) + tuple(k for k in aggregates if str(k).strip()[-4:] == l4):
            if k in aggregates:
                agg_raw = aggregates[k]
                break
        if agg_raw is not None:
            agg, na = aggregate_value(agg_raw, "aggregates.%s" % l4)
            diff = a["total"] - agg
            row.update({"aggregate_usd": money(agg), "aggregate_na_buckets": na,
                        "difference_usd": money(diff), "reconciles": abs(diff) < CENT})
            if abs(diff) >= CENT:
                notes.append("%s: trade rows sum to %s but get_realized_pnl says %s; the difference is usually "
                             "prediction-market rows, a page not read, or a window mismatch" % (
                                 mask(l4), money(a["total"]), money(agg)))
        if info["type"] == "taxable":
            taxable_total += a["total"]
            by_account.append(row)
        else:
            retirement.append(dict(row, why="gains and losses inside an IRA or Roth are not taxable in the year"))
    symbols = [{"symbol": s, "realized_usd": money(v["total"]), "gains_usd": money(v["gains"]),
                "losses_usd": money(v["losses"]), "trades": v["count"], "accounts": sorted(v["accounts"])}
               for s, v in sorted(per_symbol.items(), key=lambda kv: (kv[1]["total"], kv[0]))]
    incomplete = [mask(l4) for l4, v in sorted(accounts.items()) if v["read_status"] in ("partial", "failed")]
    if incomplete:
        notes.append("not fully read: %s; totals are incomplete" % ", ".join(incomplete))
    if outside:
        notes.append("%d row(s) outside %s were left out (span ytd covers this calendar year)" % (outside, year))
    status = "UNKNOWN" if incomplete else "NO ACTION"
    line = "%s: Dollars at stake: %s realized %s in taxable accounts%s" % (
        status, usd(taxable_total), "year to date" if window == "ytd" else "in the window",
        " (%s kept separate)" % ", ".join("%s %s" % (mask(r["account_last4"]), usd(Decimal(r["realized_usd"])))
                                         for r in retirement) if retirement else "")
    if incomplete:
        line += "; incomplete: %s" % ", ".join(incomplete)
    return {
        "ok": True,
        "window": window,
        "year": year,
        "status_line": line,
        "taxable_total_usd": money(taxable_total),
        "taxable_gains_usd": money(sum((per_acct[a]["gains"] for a in accounts if accounts[a]["type"] == "taxable"),
                                       ZERO)),
        "taxable_losses_usd": money(sum((per_acct[a]["losses"] for a in accounts
                                         if accounts[a]["type"] == "taxable"), ZERO)),
        "by_account": by_account,
        "by_symbol": symbols,
        "by_asset_class": dict((k, money(v)) for k, v in sorted(by_class.items())),
        "unattributed_rows": {"count": unattributed["count"], "realized_usd": money(unattributed["total"])},
        "retirement_excluded": retirement,
        "term_split": ({"short_usd": money(term["short"]), "long_usd": money(term["long"]), "rows": term["rows"]}
                       if term["rows"] else TERM_UNAVAILABLE),
        "prediction_markets_note": PREDICTION_NOTE,
        "wash_note": WASH_NOTE,
        "sort": "by_symbol: largest loss first",
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
_M = {"type": "string", "pattern": r"^-?\d+\.\d{2}$"}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
SCHEMAS = {
    "run": {
        "input": {"type": "object", "required": ["accounts", "trades"], "additionalProperties": False, "properties": {
            "accounts": {"type": "array", "items": {"type": "object", "required": ["last4", "type"], "properties": {
                "last4": _S, "type": {"enum": ["taxable", "retirement"]}, "label": _S,
                "read_status": {"enum": ["complete", "partial", "failed", "not_in_scope"]}}}},
            "trades": {"type": "array", "items": {"type": "object", "required": ["account_last4"], "properties": {
                "account_last4": _S, "date": _S, "timestamp": _S, "symbol": _S, "side": _S, "quantity": _S,
                "price": _S, "realized_gain": _SN, "realized_usd": _SN, "asset_class": _S, "classification": _S,
                "term": _S}}},
            "aggregates": {"type": "object"}, "window": {"enum": ["ytd", "custom"]}, "as_of": _S}},
        "output": {"anyOf": [{"type": "object", "required": [
            "ok", "status_line", "taxable_total_usd", "by_account", "by_symbol", "retirement_excluded", "term_split",
            "prediction_markets_note"], "properties": {
            "ok": {"enum": [True]}, "taxable_total_usd": _M, "by_account": {"type": "array"},
            "by_symbol": {"type": "array"}, "retirement_excluded": {"type": "array"},
            "term_split": {"type": ["string", "object"]}, "prediction_markets_note": _S}}, _ERR]},
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
    ("run", {"as_of": "2026-11-16", "window": "ytd",
             "accounts": [{"last4": "X4F1", "type": "taxable", "label": "Agentic"},
                          {"last4": "M7Q5", "type": "taxable", "label": "Individual"},
                          {"last4": "P0Z9", "type": "retirement", "label": "Roth IRA"}],
             "trades": [{"account_last4": "X4F1", "date": "2026-10-02", "symbol": "PLTR", "realized_gain": "462.23"},
                        {"account_last4": "M7Q5", "date": "2026-04-14", "symbol": "MU", "realized_gain": "8362.10"},
                        {"account_last4": "M7Q5", "timestamp": "2026-11-03T15:12:09Z", "symbol": "AMD",
                         "realized_gain": "-412.00"},
                        {"account_last4": "P0Z9", "date": "2026-05-20", "symbol": "VTI", "realized_gain": "1204.00"}],
             "aggregates": {"M7Q5": {"data_points": [{"realized_gain": "8362.10"}, {"realized_gain": None},
                                                     {"realized_gain": "-412.00"}]}}},
     {"ok": True, "taxable_total_usd": "8412.33",
      "by_account": [{"account_last4": "M7Q5", "realized_usd": "7950.10", "reconciles": True,
                      "aggregate_na_buckets": 1}, {"account_last4": "X4F1", "realized_usd": "462.23"}],
      "retirement_excluded": [{"account_last4": "P0Z9", "realized_usd": "1204.00"}],
      "term_split": TERM_UNAVAILABLE}),
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: realized_summary.py run < input.json")))
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
