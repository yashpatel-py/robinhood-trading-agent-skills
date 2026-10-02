#!/usr/bin/env python3
"""earnings_move.py - the move the options market prices in for an earnings report, next to past moves.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: an earnings report inside a held option's remaining life is the single largest
source of overnight gap risk, in both directions. The at-the-money straddle (call mid + put mid on the
first expiration after the report) is the market's price for that move; the stock's own reactions to
its last reports show how often moves like that actually happened. Both are evidence, not a forecast:
this script states the numbers with the sample size and never a direction.

Rules:
  implied move      call mid + put mid at the strike nearest spot, first expiration after the report
                    (pm or unknown timing: strictly after the report date; am: on or after it)
  implied move %    implied move / spot x 100
  past move, pm     report-date close -> next trading day's close
  past move, am     prior trading day's close -> report-date close
  bars              interpolated daily bars are never used; a quarter whose needed bar is missing or
                    interpolated is skipped with the reason. The neighbouring bar must be the adjacent
                    trading day (NYSE table from rh_time.py when it covers the dates, else at most one
                    weekday between them, for a single holiday): a gap is never bridged into a
                    multi-day "reaction"
  exceeded_implied  how many past |moves| were larger than today's implied move %, as "k of n"

Usage:
    python3 earnings_move.py run < input.json > output.json
    python3 earnings_move.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, no file writes; one JSON object out; exit 0
whenever JSON was printed, exit 1 only on a crash. Prose twin: references/formulas.md.
"""

import json
import os
import re
import sys
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:  # rh_time.py is synced next to this file (shared/manifest.json); a copied-alone script falls back
    import rh_time as _rh
except ImportError:  # pragma: no cover - exercised by the fallback test with the module hidden
    _rh = None

VERSION = "2.0.0"
SCRIPT = "earnings_move"
CENT = Decimal("0.01")
TENTH = Decimal("0.1")
RAW4 = Decimal("0.0001")
WIDE_SPREAD_PCT = Decimal("25")
SMALL_SAMPLE = 4
LABEL = "evidence, not a forecast"


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


_DEC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def dec(value, field, required=True, minimum=None, positive=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InputError("MISSING_FIELD", field, "%s is required" % field)
        return None
    if isinstance(value, bool):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string" % field)
    text = repr(value) if isinstance(value, float) else str(value).strip()
    if not _DEC_RE.match(text):
        raise InputError("BAD_VALUE", field, "%s must be a decimal string like \"12.10\" (got %r)" % (field, value))
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
    m = _DATE_RE.match(str(value).strip())
    if not m:
        raise InputError("BAD_DATE", field, "%s must start with YYYY-MM-DD (got %r)" % (field, value))
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise InputError("BAD_DATE", field, "%s is not a real date (%r)" % (field, value)) from None


def timing_of(value):
    text = str(value or "").strip().lower().replace("-", " ").replace("_", " ")
    if text in ("am", "bmo", "before market open", "before open", "pre market", "premarket", "morning"):
        return "am"
    if text in ("pm", "amc", "after market close", "after close", "post market", "postmarket", "evening"):
        return "pm"
    return "unknown"


def money(d):
    return None if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def pct_pair(d):
    if d is None:
        return None, None
    return float(d.quantize(TENTH, rounding=ROUND_HALF_UP)), str(d.quantize(RAW4, rounding=ROUND_HALF_UP))


def quote_side(raw, field):
    if not isinstance(raw, dict):
        raise InputError("MISSING_FIELD", field, "%s is {bid, ask}" % field)
    bid = dec(raw.get("bid"), field + ".bid", minimum=Decimal(0))
    ask = dec(raw.get("ask"), field + ".ask", positive=True)
    if bid > ask:
        raise InputError("CROSSED_QUOTE", field, "%s bid %s is above ask %s" % (field, bid, ask))
    mid = (bid + ask) / 2
    return {"bid": bid, "ask": ask, "mid": mid, "spread_pct": (ask - bid) / mid * 100 if mid > 0 else None}


def parse_bars(raw):
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise InputError("BAD_VALUE", "bars", "bars is a list of {date, close, interpolated}")
    bars, seen = [], set()
    for i, b in enumerate(raw):
        where = "bars[%d]" % i
        if not isinstance(b, dict):
            raise InputError("BAD_VALUE", where, "each bar is {date, close, interpolated}")
        d = parse_date(b.get("date"), where + ".date")
        if d in seen:
            raise InputError("DUPLICATE_BAR", where + ".date", "two bars for %s" % d.isoformat())
        seen.add(d)
        bars.append({"date": d, "close": dec(b.get("close"), where + ".close", positive=True),
                     "interpolated": bool(b.get("interpolated", False))})
    bars.sort(key=lambda b: b["date"])
    return bars


def adjacent_trading_days(before, after):
    """True when `after` is the trading day right after `before`. Uses the NYSE table in rh_time.py when it
    covers both dates; otherwise allows at most one weekday between them (a single exchange holiday)."""
    if after <= before:
        return False
    if _rh is not None:
        try:
            return _rh.next_trading_day(before) == after
        except Exception:  # dates outside the embedded calendar: fall back to weekdays
            pass
    between = sum(1 for k in range(1, (after - before).days) if (before + timedelta(days=k)).weekday() < 5)
    return between <= 1


def closes_from_bars(bars, report_date, timing):
    """Return (before_bar, after_bar, None) or (None, None, reason)."""
    idx = next((i for i, b in enumerate(bars) if b["date"] == report_date), None)
    if idx is None:
        return None, None, "no daily bar on the report date %s" % report_date.isoformat()
    if bars[idx]["interpolated"]:
        return None, None, "the report-date bar is interpolated (no real trading data)"
    if timing == "pm":
        if idx + 1 >= len(bars):
            return None, None, "no bar yet for the trading day after the report"
        before, after = bars[idx], bars[idx + 1]
        if after["interpolated"]:
            return None, None, "the next trading day's bar is interpolated"
    else:
        if idx == 0:
            return None, None, "no bar for the trading day before the report"
        before, after = bars[idx - 1], bars[idx]
        if before["interpolated"]:
            return None, None, "the prior trading day's bar is interpolated"
    if not adjacent_trading_days(before["date"], after["date"]):
        return None, None, ("the bars %s -> %s are not adjacent trading days (a daily bar is missing): a "
                            "multi-day move is not an earnings reaction" % (before["date"].isoformat(),
                                                                           after["date"].isoformat()))
    return before, after, None


def median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        return None
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def op_run(data):
    symbol = str(data.get("symbol") or "").strip().upper()
    if not symbol:
        raise InputError("MISSING_FIELD", "symbol", "symbol is required")
    spot = dec(data.get("spot"), "spot", positive=True)
    report = data.get("report")
    if not isinstance(report, dict):
        raise InputError("MISSING_FIELD", "report", "report is {date, timing, verified}")
    report_date = parse_date(report.get("date"), "report.date")
    timing = timing_of(report.get("timing"))
    verified = report.get("verified")
    straddle = data.get("straddle")
    if not isinstance(straddle, dict):
        raise InputError("MISSING_FIELD", "straddle", "straddle is {expiration, strike, call{bid,ask}, put{bid,ask}}")
    expiration = parse_date(straddle.get("expiration"), "straddle.expiration")
    strike = dec(straddle.get("strike"), "straddle.strike", positive=True)
    if expiration < report_date or (expiration == report_date and timing != "am"):
        raise InputError("STRADDLE_EXPIRES_BEFORE_REPORT", "straddle.expiration",
                         "the straddle must expire after the report (%s, %s): use the first expiration after it across "
                         "all chains" % (report_date.isoformat(), timing))
    call = quote_side(straddle.get("call"), "straddle.call")
    put = quote_side(straddle.get("put"), "straddle.put")
    implied = call["mid"] + put["mid"]
    implied_pct = implied / spot * 100
    implied_pct_1, implied_raw = pct_pair(implied_pct)

    notes = []
    if verified is not True:
        notes.append("The report date is not verified by the company: treat it as a possible hit.")
    if timing == "unknown":
        notes.append("Report timing (before the open or after the close) is unknown.")
    gap = (expiration - report_date).days
    if gap > 1:
        notes.append("The straddle expires %d days after the report, so it also prices ordinary movement over those "
                     "days: the implied move slightly overstates the earnings move alone." % gap)
    if abs(strike - spot) / spot > Decimal("0.025"):
        notes.append("The straddle strike %s is more than 2.5%% from spot %s: the mid-sum is a rougher estimate."
                     % (money(strike), money(spot)))
    for name, side in (("call", call), ("put", put)):
        if side["spread_pct"] is not None and side["spread_pct"] > WIDE_SPREAD_PCT:
            notes.append("The %s's bid/ask spread is %s%% of its mid: the mid may not be a tradable price."
                         % (name, pct_pair(side["spread_pct"])[0]))
        if side["bid"] == 0:
            notes.append("The %s has no bid: its mid is half the ask, a weak estimate." % name)

    bars = parse_bars(data.get("bars"))
    past_raw = data.get("past") or []
    if not isinstance(past_raw, list):
        raise InputError("BAD_VALUE", "past", "past is a list of {date, timing, close_before?, close_after?}")
    past, skipped, moves = [], [], []
    for i, entry in enumerate(past_raw):
        where = "past[%d]" % i
        if not isinstance(entry, dict):
            raise InputError("BAD_VALUE", where, "each past report is {date, timing, close_before?, close_after?}")
        d = parse_date(entry.get("date"), where + ".date")
        t = timing_of(entry.get("timing"))
        if d >= report_date:
            skipped.append({"date": d.isoformat(), "reason": "not before the upcoming report"})
            continue
        before = dec(entry.get("close_before"), where + ".close_before", required=False, positive=True)
        after = dec(entry.get("close_after"), where + ".close_after", required=False, positive=True)
        source = "given closes"
        if before is None or after is None:
            if t == "unknown":
                skipped.append({"date": d.isoformat(), "reason": "timing unknown: cannot tell which closes bracket "
                                                                 "the report"})
                continue
            if bars is None:
                skipped.append({"date": d.isoformat(), "reason": "no closes given and no bars to read them from"})
                continue
            b_bar, a_bar, reason = closes_from_bars(bars, d, t)
            if reason:
                skipped.append({"date": d.isoformat(), "reason": reason})
                continue
            before, after = b_bar["close"], a_bar["close"]
            source = "bars %s -> %s" % (b_bar["date"].isoformat(), a_bar["date"].isoformat())
        move = (after - before) / before * 100
        m1, mraw = pct_pair(move)
        moves.append(move)
        past.append({"date": d.isoformat(), "timing": t, "close_before": money(before), "close_after": money(after),
                     "move_pct": m1, "move_pct_raw": mraw, "source": source})
    abs_moves = [abs(m) for m in moves]
    med = median(abs_moves)
    exceeded = sum(1 for m in abs_moves if m > implied_pct)
    n = len(moves)
    if n == 0:
        notes.append("No past earnings reactions could be measured: the implied move stands alone.")
    elif n < SMALL_SAMPLE:
        notes.append("Only %d past reaction(s) measured: a small sample." % n)

    inside = []
    for i, h in enumerate(data.get("held_expirations") or []):
        where = "held_expirations[%d]" % i
        if not isinstance(h, dict):
            raise InputError("BAD_VALUE", where, "each entry is {label, expiration}")
        exp = parse_date(h.get("expiration"), where + ".expiration")
        if exp > report_date or (exp == report_date and timing == "am"):
            inside.append({"label": str(h.get("label") or exp.isoformat()), "expiration": exp.isoformat(),
                           "inside": "yes"})
        elif exp == report_date and timing == "unknown":
            inside.append({"label": str(h.get("label") or exp.isoformat()), "expiration": exp.isoformat(),
                           "inside": "possible (report timing unknown)"})
    med1, med_raw = pct_pair(med)
    return {
        "ok": True,
        "symbol": symbol,
        "spot": money(spot),
        "report": {"date": report_date.isoformat(), "timing": timing, "verified": verified is True},
        "straddle": {"expiration": expiration.isoformat(), "strike": money(strike), "call_mid": money(call["mid"]),
                     "put_mid": money(put["mid"]), "call_spread_pct": pct_pair(call["spread_pct"])[0],
                     "put_spread_pct": pct_pair(put["spread_pct"])[0]},
        "implied_move_usd": money(implied),
        "implied_move_pct": implied_pct_1,
        "implied_move_pct_raw": implied_raw,
        "implied_range": {"low": money(spot - implied), "high": money(spot + implied),
                          "through": expiration.isoformat()},
        "past_moves": past,
        "past_moves_pct": [p["move_pct"] for p in past],
        "median_abs_move_pct": med1,
        "median_abs_move_pct_raw": med_raw,
        "exceeded_implied": "%d of %d" % (exceeded, n),
        "n": n,
        "skipped": skipped,
        "held_inside_report": inside,
        "label": LABEL,
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
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}"}
_BIDASK = {"type": "object", "required": ["bid", "ask"], "properties": {"bid": _DEC, "ask": _DEC}}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
SCHEMAS = {
    "run": {
        "input": {
            "type": "object", "required": ["symbol", "spot", "report", "straddle"], "additionalProperties": False,
            "properties": {
                "symbol": _S, "spot": _DEC,
                "report": {"type": "object", "required": ["date"],
                           "properties": {"date": _DATE, "timing": _SN, "verified": {"type": ["boolean", "null"]}}},
                "straddle": {"type": "object", "required": ["expiration", "strike", "call", "put"],
                             "properties": {"expiration": _DATE, "strike": _DEC, "call": _BIDASK, "put": _BIDASK}},
                "past": {"type": "array", "items": {"type": "object", "required": ["date"],
                                                    "properties": {"date": _DATE, "timing": _SN,
                                                                   "close_before": _DECN, "close_after": _DECN}}},
                "bars": {"type": "array", "items": {"type": "object", "required": ["date", "close"],
                                                    "properties": {"date": _DATE, "close": _DEC,
                                                                   "interpolated": {"type": ["boolean", "null"]}}}},
                "held_expirations": {"type": "array", "items": {"type": "object", "required": ["expiration"],
                                                                "properties": {"label": _S, "expiration": _DATE}}},
            }},
        "output": {"anyOf": [{
            "type": "object",
            "required": ["ok", "symbol", "implied_move_usd", "implied_move_pct", "past_moves_pct",
                         "median_abs_move_pct", "exceeded_implied", "n", "label", "notes"],
            "properties": {
                "ok": {"enum": [True]}, "symbol": _S, "spot": _M, "report": {"type": "object"},
                "straddle": {"type": "object"}, "implied_move_usd": _M, "implied_move_pct": _PCT,
                "implied_move_pct_raw": _SN, "implied_range": {"type": "object"},
                "past_moves": {"type": "array"}, "past_moves_pct": {"type": "array", "items": {"type": "number"}},
                "median_abs_move_pct": _PCT, "median_abs_move_pct_raw": _SN, "exceeded_implied": _S,
                "n": {"type": "integer"}, "skipped": {"type": "array"}, "held_inside_report": {"type": "array"},
                "label": {"enum": [LABEL]}, "notes": {"type": "array", "items": _S},
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


_NVDA = {"symbol": "NVDA", "spot": "228.10", "report": {"date": "2026-11-18", "timing": "pm", "verified": False},
         "straddle": {"expiration": "2026-11-20", "strike": "230", "call": {"bid": "12.00", "ask": "12.20"},
                      "put": {"bid": "13.60", "ask": "13.80"}}}
EXAMPLES = [
    ("run", dict(_NVDA, past=[
        {"date": "2026-08-26", "timing": "pm", "close_before": "180.00", "close_after": "171.00"},
        {"date": "2026-05-27", "timing": "pm", "close_before": "130.00", "close_after": "149.50"}]),
     {"ok": True, "implied_move_usd": "25.80", "implied_move_pct": 11.3, "past_moves_pct": [-5.0, 15.0],
      "median_abs_move_pct": 10.0, "exceeded_implied": "1 of 2", "n": 2, "label": LABEL}),
    ("run", dict(_NVDA, past=[{"date": "2026-08-26", "timing": "pm"}],
                 bars=[{"date": "2026-08-25", "close": "178.00"}, {"date": "2026-08-26", "close": "180.00"},
                       {"date": "2026-08-27", "close": "171.00"}]),
     {"ok": True, "past_moves": [{"close_before": "180.00", "close_after": "171.00", "move_pct": -5.0}], "n": 1}),
    ("run", dict(_NVDA, past=[{"date": "2026-08-26", "timing": "am"}],
                 bars=[{"date": "2026-08-25", "close": "178.00"}, {"date": "2026-08-26", "close": "180.00"}]),
     {"ok": True, "past_moves": [{"close_before": "178.00", "close_after": "180.00", "move_pct": 1.1}]}),
    ("run", dict(_NVDA, past=[{"date": "2026-08-26", "timing": "pm"}],
                 bars=[{"date": "2026-08-25", "close": "178.00"}, {"date": "2026-08-26", "close": "180.00"},
                       {"date": "2026-09-02", "close": "150.00"}]),
     {"ok": True, "past_moves": [], "n": 0,
      "skipped": [{"date": "2026-08-26", "reason": "the bars 2026-08-26 -> 2026-09-02 are not adjacent trading days (a "
                                                   "daily bar is missing): a multi-day move is not an earnings "
                                                   "reaction"}]}),
    ("run", dict(_NVDA, straddle=dict(_NVDA["straddle"], expiration="2026-11-18")),
     {"ok": False, "errors": [{"code": "STRADDLE_EXPIRES_BEFORE_REPORT"}]}),
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: earnings_move.py run < input.json")))
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
