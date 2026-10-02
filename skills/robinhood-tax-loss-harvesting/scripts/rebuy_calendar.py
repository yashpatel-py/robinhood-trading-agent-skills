#!/usr/bin/env python3
"""rebuy_calendar.py - an .ics calendar of "don't buy this back yet" windows and "OK to buy again" dates.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: after a harvest, the loss survives only if no account (IRA and Roth included, and any
recurring buy or dividend reinvestment) buys the same stock for 30 calendar days. People forget, and the
agent is not there on day 12. A calendar entry on the user's phone is the reminder that does not depend on
this conversation. Each item becomes an all-day "don't buy" block from the sale date through the day before
the rebuy date, and an all-day "OK to buy again" event on the rebuy date (with a 09:00 reminder), so the
calendar says the same thing the wash-sale check said.

Ops:
  run   items -> RFC 5545 text (CRLF line endings, lines folded at 75 octets, TEXT values escaped).
        Nothing is written unless --out <path> is given; an existing file is never overwritten without
        --force. Put no account numbers in item text: the calendar may sync to other devices.

Usage:
    python3 rebuy_calendar.py run < input.json > output.json
    python3 rebuy_calendar.py run --out ~/Desktop/rebuy-dates.ics < input.json
    python3 rebuy_calendar.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network; no file writes without --out; one JSON object out;
exit 0 whenever JSON was printed, exit 1 only on a crash.
"""

import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

VERSION = "2.0.0"
SCRIPT = "rebuy_calendar"
PRODID = "-//yashpatel-py//Preflight rebuy calendar %s//EN" % VERSION
DEFAULT_NAME = "Wash-sale rebuy dates (Preflight, unofficial)"
DEFAULT_SCOPE = "all Robinhood accounts incl. IRA and Roth, recurring buys and dividend reinvestment"


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


def parse_day(value, field):
    if isinstance(value, str):
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value.strip())
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError as exc:
                raise InputError("BAD_DATE", field, "not a real calendar date: %s" % exc)
    raise InputError("BAD_DATE", field, "expected YYYY-MM-DD (got %r)" % (value,))


def ics_date(d):
    return d.strftime("%Y%m%d")


def escape_text(s):
    """RFC 5545 section 3.3.11: backslash, semicolon, comma and newline are escaped in TEXT values."""
    return (str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def fold(line):
    """RFC 5545 section 3.1: lines longer than 75 octets are split, continuation lines start with a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    parts, cur, limit = [], b"", 75
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > limit:
            parts.append(cur.decode("utf-8"))
            cur, limit = b"", 74  # continuation lines carry a leading space
        cur += b
    parts.append(cur.decode("utf-8"))
    return [parts[0]] + [" " + p for p in parts[1:]]


def uid(*bits):
    return hashlib.sha256("|".join(bits).encode("utf-8")).hexdigest()[:24] + "@rebuy.preflight"


def dtstamp(value):
    if value in (None, ""):
        now = datetime.now(timezone.utc)
    else:
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})$", str(value))
        if not m:
            raise InputError("BAD_TIME", "dtstamp", "dtstamp must be ISO 8601 with Z or an offset")
        y, mo, d, h, mi, s, off = m.groups()
        now = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
        if off == "Z":
            now = now.replace(tzinfo=timezone.utc)
        else:
            sign = -1 if off[0] == "-" else 1
            digits = off[1:].replace(":", "")
            now = now.replace(tzinfo=timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))))
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def op_run(data):
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise InputError("MISSING_FIELD", "items", "give items: [{symbol, do_not_buy_until, sale_date?, scope?}]")
    name = str(data.get("calendar_name") or DEFAULT_NAME)
    stamp = dtstamp(data.get("dtstamp"))
    reminders = data.get("reminders", True) is not False
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:" + PRODID, "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:" + escape_text(name)]
    events = []
    for i, it in enumerate(items):
        where = "items[%d]" % i
        if not isinstance(it, dict):
            raise InputError("BAD_VALUE", where, "each item is an object")
        sym = str(it.get("symbol") or "").strip().upper()
        if not re.match(r"^[A-Z][A-Z0-9.\-]{0,9}$", sym):
            raise InputError("BAD_VALUE", where + ".symbol", "symbol must be a ticker such as TSLA")
        ok_day = parse_day(it.get("do_not_buy_until"), where + ".do_not_buy_until")
        sale = parse_day(it.get("sale_date"), where + ".sale_date") if it.get("sale_date") else None
        if sale is not None and sale >= ok_day:
            raise InputError("BAD_DATE", where + ".sale_date", "sale_date must be before do_not_buy_until")
        first = parse_day(it.get("first_trading_day_after"), where + ".first_trading_day_after") \
            if it.get("first_trading_day_after") else None
        scope = str(it.get("scope") or DEFAULT_SCOPE)
        extra = str(it.get("note") or "").strip()
        if sale is not None:
            desc = ("Wash-sale window for the %s loss sold %s. A buy of %s in %s before %s washes that loss "
                    "(permanently if the buy is in an IRA or Roth). Checked by the Preflight kit (unofficial); "
                    "not tax advice." % (sym, sale.isoformat(), sym, scope, ok_day.isoformat()))
            if extra:
                desc += " " + extra
            lines += ["BEGIN:VEVENT", "UID:" + uid("block", sym, sale.isoformat(), ok_day.isoformat(), scope),
                      "DTSTAMP:" + stamp, "DTSTART;VALUE=DATE:" + ics_date(sale),
                      "DTEND;VALUE=DATE:" + ics_date(ok_day),
                      "SUMMARY:" + escape_text("Don't buy %s (wash-sale window, every account)" % sym),
                      "DESCRIPTION:" + escape_text(desc), "TRANSP:TRANSPARENT", "END:VEVENT"]
            events.append({"kind": "do_not_buy", "symbol": sym, "start": sale.isoformat(),
                           "end_exclusive": ok_day.isoformat()})
        when = ok_day.isoformat()
        desc = ("First calendar day a buy of %s no longer falls in the 30-day window after the loss sale (%s)." %
                (sym, scope))
        if first is not None and first != ok_day:
            desc += " The market is closed that day; the first trading day is %s." % first.isoformat()
        if "recurring" not in scope.lower():
            desc += " Recurring buys and dividend reinvestment count too."
        desc += " Not tax advice."
        if extra:
            desc += " " + extra
        ev = ["BEGIN:VEVENT", "UID:" + uid("ok", sym, when, scope), "DTSTAMP:" + stamp,
              "DTSTART;VALUE=DATE:" + ics_date(ok_day), "DTEND;VALUE=DATE:" + ics_date(ok_day + timedelta(days=1)),
              "SUMMARY:" + escape_text("OK to buy %s again (wash-sale window over)" % sym),
              "DESCRIPTION:" + escape_text(desc), "TRANSP:TRANSPARENT"]
        if reminders:
            ev += ["BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:PT9H",
                   "DESCRIPTION:" + escape_text("OK to buy %s again" % sym), "END:VALARM"]
        lines += ev + ["END:VEVENT"]
        events.append({"kind": "ok_to_buy", "symbol": sym, "date": when,
                       "first_trading_day": (first or ok_day).isoformat()})
    lines.append("END:VCALENDAR")
    folded = []
    for ln in lines:
        folded.extend(fold(ln))
    return {"ok": True, "ics": "\r\n".join(folded) + "\r\n", "events": len(events), "event_list": events,
            "calendar_name": name, "written_to": None,
            "notes": ["all-day events in your calendar's own time zone; import the file or pass --out <path>"]}


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


def write_out(result, path, force):
    """Write the .ics text to the path the user named. Never overwrites without --force."""
    target = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(target):
        return _err("BAD_PATH", "--out", "%s is a folder; name a file such as rebuy-dates.ics" % target)
    if os.path.exists(target) and not force:
        return _err("FILE_EXISTS", "--out", "%s exists; pass --force to replace it" % target)
    folder = os.path.dirname(target)
    if folder and not os.path.isdir(folder):
        return _err("BAD_PATH", "--out", "folder %s does not exist" % folder)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(result["ics"])
    out = dict(result)
    out["written_to"] = target
    return out


# ---------------------------------------------------------------------------------------------
# JSON Schemas and self-test
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_ERR = {"type": "object", "required": ["ok", "errors"],
        "properties": {"ok": {"enum": [False]},
                       "errors": {"type": "array", "items": {"type": "object", "required": ["code", "field", "msg"],
                                                             "properties": {"code": _S, "field": _S, "msg": _S}}}}}
SCHEMAS = {
    "run": {
        "input": {"type": "object", "required": ["items"], "additionalProperties": False, "properties": {
            "items": {"type": "array", "items": {"type": "object", "required": ["symbol", "do_not_buy_until"],
                                                 "properties": {"symbol": _S, "do_not_buy_until": _DATE,
                                                                "sale_date": _DATE, "first_trading_day_after": _DATE,
                                                                "scope": _S, "note": _S}}},
            "calendar_name": _S, "dtstamp": _S, "reminders": {"type": "boolean"}}},
        "output": {"anyOf": [{"type": "object", "required": ["ok", "ics", "events", "written_to"], "properties": {
            "ok": {"enum": [True]}, "ics": {"type": "string", "pattern": "^BEGIN:VCALENDAR\r\n"},
            "events": {"type": "integer"}, "written_to": {"type": ["string", "null"]}}}, _ERR]},
    },
}


def _type_ok(value, t):
    return {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
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


def ics_problems(ics):
    """Structural checks an importer relies on."""
    problems = []
    if not ics.endswith("\r\n") or "\n" in ics.replace("\r\n", ""):
        problems.append("lines must end with CRLF")
    for ln in ics.split("\r\n"):
        if len(ln.encode("utf-8")) > 75:
            problems.append("line longer than 75 octets: %r" % ln[:40])
    unfolded = ics.replace("\r\n ", "")
    if unfolded.count("BEGIN:VEVENT") != unfolded.count("END:VEVENT"):
        problems.append("unbalanced VEVENT")
    for ln in unfolded.split("\r\n"):
        if ln.startswith("DTSTART") or ln.startswith("DTEND"):
            if not re.match(r"^DT(START|END);VALUE=DATE:\d{8}$", ln):
                problems.append("bad date line %r" % ln)
    return problems


EXAMPLES = [
    ("run", {"items": [{"symbol": "TSLA", "sale_date": "2026-12-31", "do_not_buy_until": "2027-01-31",
                        "first_trading_day_after": "2027-02-01"}],
             "dtstamp": "2026-11-17T01:05:00Z"},
     {"ok": True, "events": 2, "event_list": [
         {"kind": "do_not_buy", "symbol": "TSLA", "start": "2026-12-31", "end_exclusive": "2027-01-31"},
         {"kind": "ok_to_buy", "symbol": "TSLA", "date": "2027-01-31", "first_trading_day": "2027-02-01"}]}),
    ("run", {"items": [{"symbol": "AMD", "do_not_buy_until": "2026-12-04", "note": "rule check; commas, too"}],
             "dtstamp": "2026-11-16T20:05:00-05:00", "reminders": False},
     {"ok": True, "events": 1}),
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
        if out.get("ok"):
            problems += ics_problems(out["ics"])
            first_ok = inp["items"][0]["do_not_buy_until"].replace("-", "")
            if ("DTSTART;VALUE=DATE:" + first_ok) not in out["ics"]:
                problems.append("the OK-to-buy event does not start on do_not_buy_until")
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
    out_path, force, rest = None, False, []
    i = 0
    while i < len(argv):
        if argv[i] == "--out":
            if i + 1 >= len(argv):
                print(json.dumps(_err("MISSING_FIELD", "--out", "--out needs a file path")))
                return 0
            out_path = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--force":
            force = True
        else:
            rest.append(argv[i])
        i += 1
    if not rest:
        print(json.dumps(_err("MISSING_OP", "op", "usage: rebuy_calendar.py run [--out file.ics] < input.json")))
        return 0
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        print(json.dumps(_err("BAD_JSON", "", "stdin is not valid JSON: %s" % exc)))
        return 0
    result = run(rest[0], data)
    if result.get("ok") and out_path:
        try:
            result = write_out(result, out_path, force)
        except OSError as exc:
            result = _err("WRITE_FAILED", "--out", str(exc))
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
