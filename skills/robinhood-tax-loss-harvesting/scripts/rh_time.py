#!/usr/bin/env python3
# synced from shared/scripts/rh_time.py; do not edit
"""rh_time.py - NYSE session clock, trading-day calendar and ET/UTC conversion.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: the Robinhood connector has no market-hours tool, and `get_index_quotes`
returned an empty `state` in the 2026-09-22 capture, so neither can tell an agent whether the
regular session is open. This script answers from the US Eastern wall clock plus an embedded
NYSE holiday table. It asserts ONLY the regular session (09:30-16:00 ET, 13:00 on early-close
days). Robinhood's extended-hours and 24 Hour Market windows are not verified yet, so outside
the regular session it tells the skill to ask the user which session they want rather than guess.

A time the user states beats a quote timestamp, which beats the system clock (connector rule R11).
Pass the time you trust as `now` and say where it came from in `now_source`.

Usage:
    python3 rh_time.py <op> < input.json > output.json
    python3 rh_time.py --schema      # JSON Schemas for every op
    python3 rh_time.py --selftest    # run the embedded examples; exit 0 or 1

Ops: session, to_utc, to_et, next_trading_day, add_days, settlement_date,
     trading_days_between, calendar_info

Contract: stdlib only, Python >= 3.9, no network, no file writes. Always prints one JSON object,
{"ok": true, ...} or {"ok": false, "errors": [{"code", "field", "msg"}]}; exit 0 whenever JSON was
printed, exit 1 only on a crash.
"""

import json
import re
import sys
from datetime import date, datetime, time, timedelta, timezone

VERSION = "2.0.0"
SCRIPT = "rh_time"
TZ_NAME = "America/New_York"

# Tests set this to True to exercise the embedded DST rules used when zoneinfo/tzdata is missing.
FORCE_EMBEDDED_TZ = False

try:  # zoneinfo is stdlib on 3.9+, but tzdata can be missing (for example on some Windows installs)
    from zoneinfo import ZoneInfo

    try:
        _ZONE = ZoneInfo(TZ_NAME)
    except Exception:  # ZoneInfoNotFoundError and friends
        _ZONE = None
except ImportError:  # pragma: no cover - only on stripped-down interpreters
    _ZONE = None

# ---------------------------------------------------------------------------------------------
# Embedded calendar. VERIFY-D1 against https://www.nyse.com/trade/hours-calendars before relying
# on it; golden tests pin the entries the skills depend on (2026-11-26, 2026-11-27, 2027-01-01).
# ---------------------------------------------------------------------------------------------
CALENDAR_VALID_FROM = date(2026, 1, 1)
CALENDAR_VALID_THROUGH = date(2027, 12, 31)
CALENDAR_SOURCE = (
    "NYSE holidays and early closures 2026-2027 (nyse.com hours and calendars), embedded "
    "2026-09-22; re-verify before 2027-11-01"
)
EXPIRY_WARNING_DAYS = 60

HOLIDAYS = {
    date(2026, 1, 1): "New Year's Day",
    date(2026, 1, 19): "Martin Luther King Jr. Day",
    date(2026, 2, 16): "Washington's Birthday",
    date(2026, 4, 3): "Good Friday",
    date(2026, 5, 25): "Memorial Day",
    date(2026, 6, 19): "Juneteenth National Independence Day",
    date(2026, 7, 3): "Independence Day (observed)",
    date(2026, 9, 7): "Labor Day",
    date(2026, 11, 26): "Thanksgiving Day",
    date(2026, 12, 25): "Christmas Day",
    date(2027, 1, 1): "New Year's Day",
    date(2027, 1, 18): "Martin Luther King Jr. Day",
    date(2027, 2, 15): "Washington's Birthday",
    date(2027, 3, 26): "Good Friday",
    date(2027, 5, 31): "Memorial Day",
    date(2027, 6, 18): "Juneteenth National Independence Day (observed)",
    date(2027, 7, 5): "Independence Day (observed)",
    date(2027, 9, 6): "Labor Day",
    date(2027, 11, 25): "Thanksgiving Day",
    date(2027, 12, 24): "Christmas Day (observed)",
    # 2028-01-01 falls on a Saturday and NYSE does not observe it on Friday 2027-12-31.
}

EARLY_CLOSES = {  # regular session closes at 13:00 ET
    date(2026, 11, 27): "Day after Thanksgiving",
    date(2026, 12, 24): "Christmas Eve",
    date(2027, 11, 26): "Day after Thanksgiving",
}

# Days NYSE trades but banks and the clearing system do not settle (Federal Reserve holidays).
# A trade the business day before settles one day later. VERIFY against your trade confirmation.
SETTLEMENT_ONLY_HOLIDAYS = {
    date(2026, 10, 12): "Columbus Day (banks closed; NYSE open)",
    date(2026, 11, 11): "Veterans Day (banks closed; NYSE open)",
    date(2027, 10, 11): "Columbus Day (banks closed; NYSE open)",
    date(2027, 11, 11): "Veterans Day (banks closed; NYSE open)",
}

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Robinhood's non-regular equity sessions. Candidate values only (Robinhood Help Center, not yet
# verified against the live connector). While "verified" is False the session op never names a
# candidate; it tells the skill to ask the user. Flip to True only after the Day-1 check (J.1 #2).
NON_REGULAR_WINDOWS = {
    "verified": False,
    "source": "Robinhood Help Center candidate values; VERIFY-D1 (build spec J.1 item 2)",
    "extended_hours": {"pre_market": ["07:00", "09:30"], "post_market": ["16:00", "20:00"]},
    "all_day_hours": {"start": "20:00", "end": "04:00", "nights": "Sunday night through Friday morning"},
}
ASK_SESSION = (
    "Which session: extended_hours (pre-/post-market) or all_day_hours "
    "(24 Hour Market, overnight)?"
)

NOW_SOURCES = ("user_stated", "quote_ts", "system")


class InputError(Exception):
    """A problem with the caller's input; becomes {"ok": false, "errors": [...]}."""

    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


# ---------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------
_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})"
    r"(?:[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?)?"
    r"\s*(Z|z|[+-]\d{2}(?::?\d{2})?)?$"
)
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_date(value, field):
    if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
        raise InputError("BAD_DATE", field, "expected a date string YYYY-MM-DD")
    y, m, d = (int(x) for x in value.strip().split("-"))
    try:
        return date(y, m, d)
    except ValueError as exc:
        raise InputError("BAD_DATE", field, "not a real calendar date: %s" % exc)


def parse_iso(value, field):
    """Parse ISO 8601 / RFC 3339 (Z suffix and up to 9 fractional digits, as the connector
    returns). Returns (datetime, has_time, has_offset); naive when there is no offset."""
    if not isinstance(value, str):
        raise InputError("BAD_TIME", field, "expected an ISO 8601 string")
    m = _ISO_RE.match(value.strip())
    if not m:
        raise InputError("BAD_TIME", field, "not ISO 8601 (e.g. 2026-11-16T20:05:00-05:00): %r" % value)
    y, mo, d, hh, mi, ss, frac, off = m.groups()
    try:
        micro = int((frac or "0")[:6].ljust(6, "0"))
        dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0), micro)
    except ValueError as exc:
        raise InputError("BAD_TIME", field, "invalid date or time: %s" % exc)
    if off is None:
        return dt, hh is not None, False
    if off in ("Z", "z"):
        tz = timezone.utc
    else:
        sign = -1 if off[0] == "-" else 1
        digits = off[1:].replace(":", "")
        hours, minutes = int(digits[:2]), int(digits[2:4] or 0)
        if hours > 23 or minutes > 59:
            raise InputError("BAD_TIME", field, "bad UTC offset %r" % off)
        tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
    return dt.replace(tzinfo=tz), hh is not None, True


def _as_int(value, field):
    if isinstance(value, bool):
        raise InputError("BAD_INT", field, "expected an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.match(r"^[+-]?\d+$", value.strip()):
        return int(value.strip())
    raise InputError("BAD_INT", field, "expected an integer or integer string")


def _as_bool(value, field, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise InputError("BAD_BOOL", field, "expected true or false")


# ---------------------------------------------------------------------------------------------
# US Eastern conversion (zoneinfo when present, embedded post-2007 US DST rules otherwise)
# ---------------------------------------------------------------------------------------------
def _second_sunday_march(year):
    first = date(year, 3, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7)


def _first_sunday_november(year):
    first = date(year, 11, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7)


def _use_zone():
    return _ZONE is not None and not FORCE_EMBEDDED_TZ


def _embedded_offset_for_utc(dt_utc):
    if dt_utc.year < 2007:
        raise InputError("UNSUPPORTED_YEAR", "", "embedded DST rules cover 2007 onward only")
    start = datetime.combine(_second_sunday_march(dt_utc.year), time(7, 0)).replace(tzinfo=timezone.utc)
    end = datetime.combine(_first_sunday_november(dt_utc.year), time(6, 0)).replace(tzinfo=timezone.utc)
    return timedelta(hours=-4) if start <= dt_utc < end else timedelta(hours=-5)


def utc_to_et(dt):
    """Aware datetime -> aware datetime on US Eastern wall time with a fixed offset."""
    dt_utc = dt.astimezone(timezone.utc)
    if _use_zone():
        offset = dt_utc.astimezone(_ZONE).utcoffset()
    else:
        offset = _embedded_offset_for_utc(dt_utc)
    return (dt_utc + offset).replace(tzinfo=timezone(offset))


def et_local_to_utc(naive, fold=0):
    """US Eastern wall time -> (aware UTC datetime, status); status is ok|ambiguous|nonexistent.
    Ambiguous times (the repeated hour in November) use the earlier instant unless fold=1."""
    if _use_zone():
        off0 = naive.replace(tzinfo=_ZONE, fold=0).utcoffset()
        off1 = naive.replace(tzinfo=_ZONE, fold=1).utcoffset()
        chosen = off1 if fold else off0
        utc = (naive - chosen).replace(tzinfo=timezone.utc)
        if utc_to_et(utc).replace(tzinfo=None) != naive:
            return utc, "nonexistent"
        return utc, ("ambiguous" if off0 != off1 else "ok")
    if naive.year < 2007:
        raise InputError("UNSUPPORTED_YEAR", "", "embedded DST rules cover 2007 onward only")
    start = datetime.combine(_second_sunday_march(naive.year), time(2, 0))
    end = datetime.combine(_first_sunday_november(naive.year), time(1, 0))
    hour = timedelta(hours=1)
    status = "ok"
    if naive < start:
        offset = -5
    elif naive < start + hour:
        offset, status = -5, "nonexistent"
    elif naive < end:
        offset = -4
    elif naive < end + hour:
        offset, status = (-5 if fold else -4), "ambiguous"
    else:
        offset = -5
    return (naive - timedelta(hours=offset)).replace(tzinfo=timezone.utc), status


def _fmt_local(dt):
    return dt.replace(microsecond=0).isoformat()


def _fmt_utc(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fmt_offset(td):
    total = int(td.total_seconds())
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return "%s%02d:%02d" % (sign, total // 3600, (total % 3600) // 60)


# ---------------------------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------------------------
def _check_covered(d, field):
    if d > CALENDAR_VALID_THROUGH:
        raise InputError(
            "CALENDAR_EXPIRED", field,
            "%s is after calendar_valid_through %s; update the NYSE table in rh_time.py"
            % (d.isoformat(), CALENDAR_VALID_THROUGH.isoformat()),
        )
    if d < CALENDAR_VALID_FROM:
        raise InputError(
            "CALENDAR_NOT_COVERED", field,
            "%s is before the embedded calendar (starts %s)" % (d.isoformat(), CALENDAR_VALID_FROM.isoformat()),
        )


def is_trading_day(d):
    _check_covered(d, "date")
    return d.weekday() < 5 and d not in HOLIDAYS


def close_time(d):
    return EARLY_CLOSE if d in EARLY_CLOSES else REGULAR_CLOSE


def next_trading_day(d, inclusive=False):
    cur = d if inclusive else d + timedelta(days=1)
    while True:
        _check_covered(cur, "date")
        if is_trading_day(cur):
            return cur
        cur += timedelta(days=1)


def _is_settlement_day(d):
    return is_trading_day(d) and d not in SETTLEMENT_ONLY_HOLIDAYS


def _open_dt(d):
    utc, _ = et_local_to_utc(datetime.combine(d, REGULAR_OPEN))
    return utc_to_et(utc)


# ---------------------------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------------------------
def _non_regular_candidate(now_et, trading_day):
    """Only used once NON_REGULAR_WINDOWS['verified'] is True."""
    t = now_et.time().replace(microsecond=0)
    hhmm = t.strftime("%H:%M")
    ext = NON_REGULAR_WINDOWS["extended_hours"]
    if trading_day:
        pre, post = ext["pre_market"], ext["post_market"]
        if pre[0] <= hhmm < pre[1] or post[0] <= hhmm < post[1]:
            return "extended_hours"
    night = NON_REGULAR_WINDOWS["all_day_hours"]
    wd = now_et.weekday()  # Mon=0 .. Sun=6
    evening = hhmm >= night["start"] and wd in (6, 0, 1, 2, 3)  # Sun..Thu evenings
    morning = hhmm < night["end"] and wd in (0, 1, 2, 3, 4)  # Mon..Fri mornings
    if evening or morning:
        return "all_day_hours"
    return None


def op_session(data):
    notes = []
    now_raw = data.get("now")
    source = data.get("now_source")
    if source is not None and source not in NOW_SOURCES:
        raise InputError("BAD_ENUM", "now_source", "now_source must be one of %s" % ", ".join(NOW_SOURCES))
    if now_raw in (None, ""):
        now_utc = datetime.now(timezone.utc)
        source = "system"
    else:
        dt, has_time, has_offset = parse_iso(now_raw, "now")
        if not has_time:
            raise InputError("BAD_TIME", "now", "now needs a time of day, not just a date")
        if has_offset:
            now_utc = dt.astimezone(timezone.utc)
        else:
            now_utc, status = et_local_to_utc(dt)
            notes.append("now had no UTC offset; read as US Eastern wall time")
            if status == "nonexistent":
                raise InputError("NONEXISTENT_LOCAL_TIME", "now", "that wall time is skipped by the DST change")
            if status == "ambiguous":
                notes.append("ambiguous wall time (DST change); the earlier instant was used")
        if source is None:
            source = "unspecified"
            notes.append("now_source not given; say where this time came from in the as-of line")
    now_et = utc_to_et(now_utc)
    today = now_et.date()
    _check_covered(today, "now")

    trading_day = is_trading_day(today)
    early = today in EARLY_CLOSES
    holiday = HOLIDAYS.get(today)
    t = now_et.time()
    in_regular = trading_day and REGULAR_OPEN <= t < close_time(today)
    if in_regular:
        session = "regular"
    elif trading_day:
        session = "outside_regular_trading_day"
    else:
        session = "closed_day"

    if trading_day and t < REGULAR_OPEN:
        next_open_day = today
    else:
        try:
            next_open_day = next_trading_day(today)
        except InputError:
            next_open_day = None
            notes.append("the next regular open is after calendar_valid_through")

    candidate = None
    if NON_REGULAR_WINDOWS["verified"] and not in_regular:
        candidate = _non_regular_candidate(now_et, trading_day)
    ask = None if in_regular or candidate else ASK_SESSION

    out = {
        "ok": True,
        "now_et": _fmt_local(now_et),
        "now_utc": _fmt_utc(now_utc),
        "trading_day": trading_day,
        "holiday": holiday,
        "early_close": early,
        "regular_open_et": "09:30" if trading_day else None,
        "regular_close_et": (close_time(today).strftime("%H:%M") if trading_day else None),
        "in_regular_session": in_regular,
        "session": session,
        "non_regular": {
            "windows_verified": bool(NON_REGULAR_WINDOWS["verified"]),
            "candidate": candidate,
            "ask_user": ask,
        },
        "next_regular_open_et": (_fmt_local(_open_dt(next_open_day)) if next_open_day else None),
        "calendar_valid_through": CALENDAR_VALID_THROUGH.isoformat(),
        "now_source": source,
    }
    if early:
        notes.append("early close: the regular session ends at 13:00 ET today (%s)" % EARLY_CLOSES[today])
    if notes:
        out["notes"] = notes
    return out


def op_to_utc(data):
    tz = data.get("tz", TZ_NAME)
    notes = []
    if "local" in data:
        dt, _, has_offset = parse_iso(data["local"], "local")
        if has_offset:
            raise InputError("HAS_OFFSET", "local", "local must be a wall time without an offset; it already has one")
        field = "local"
    elif "date" in data:
        dt = datetime.combine(parse_date(data["date"], "date"), time(0, 0))
        field = "date"
        if "tz" not in data:
            notes.append("date read as local midnight US Eastern")
    else:
        raise InputError("MISSING_FIELD", "local", "give local (with tz) or date")
    fold = 1 if data.get("fold") in (1, "1", True) else 0
    if tz == TZ_NAME:
        utc, status = et_local_to_utc(dt, fold)
    elif tz in ("UTC", "Etc/UTC", "Z"):
        utc, status = dt.replace(tzinfo=timezone.utc), "ok"
    else:
        if ZoneInfo is None:
            raise InputError("TZ_UNAVAILABLE", "tz", "zoneinfo is unavailable; only America/New_York and UTC work")
        try:
            zone = ZoneInfo(tz)
        except Exception:
            raise InputError("BAD_TZ", "tz", "unknown IANA time zone %r" % tz)
        off0 = dt.replace(tzinfo=zone, fold=0).utcoffset()
        off1 = dt.replace(tzinfo=zone, fold=1).utcoffset()
        utc = (dt - (off1 if fold else off0)).replace(tzinfo=timezone.utc)
        back = utc.astimezone(zone).replace(tzinfo=None)
        status = "nonexistent" if back != dt else ("ambiguous" if off0 != off1 else "ok")
    if status == "nonexistent":
        raise InputError("NONEXISTENT_LOCAL_TIME", field, "that wall time is skipped by a DST change in %s" % tz)
    if status == "ambiguous":
        notes.append("ambiguous wall time (DST change); %s instant used" % ("later" if fold else "earlier"))
    out = {"ok": True, "utc": _fmt_utc(utc), "local": dt.replace(microsecond=0).isoformat(), "tz": tz}
    if notes:
        out["notes"] = notes
    return out


def op_to_et(data):
    if "utc" not in data:
        raise InputError("MISSING_FIELD", "utc", "give utc (ISO 8601 with Z or an offset)")
    dt, has_time, has_offset = parse_iso(data["utc"], "utc")
    if not has_offset:
        dt = dt.replace(tzinfo=timezone.utc)  # the connector's naive values are UTC
    et = utc_to_et(dt)
    return {"ok": True, "et": _fmt_local(et), "date_et": et.date().isoformat(), "offset": _fmt_offset(et.utcoffset())}


def op_next_trading_day(data):
    d = parse_date(data.get("date"), "date")
    inclusive = _as_bool(data.get("inclusive"), "inclusive", False)
    _check_covered(d, "date")
    return {"ok": True, "date": next_trading_day(d, inclusive).isoformat(), "inclusive": inclusive}


def op_add_days(data):
    d = parse_date(data.get("date"), "date")
    days = _as_int(data.get("days"), "days")
    return {"ok": True, "date": (d + timedelta(days=days)).isoformat()}


def op_settlement_date(data):
    trade = parse_date(data.get("trade_date"), "trade_date")
    _check_covered(trade, "trade_date")
    notes = []
    effective = trade
    if not is_trading_day(trade):
        effective = next_trading_day(trade)
        notes.append(
            "%s is not an NYSE trading day; a fill then (for example in the overnight session) "
            "carries the next trading day's trade date" % trade.isoformat()
        )
    cur = effective + timedelta(days=1)
    while True:
        _check_covered(cur, "trade_date")
        if _is_settlement_day(cur):
            break
        if cur in SETTLEMENT_ONLY_HOLIDAYS:
            notes.append("skipped %s: %s" % (cur.isoformat(), SETTLEMENT_ONLY_HOLIDAYS[cur]))
        cur += timedelta(days=1)
    out = {
        "ok": True,
        "trade_date": trade.isoformat(),
        "trade_date_effective": effective.isoformat(),
        "settlement_date": cur.isoformat(),
        "basis": (
            "T+1: the next business day on which NYSE is open and banks settle (US equities since "
            "2024-05-28); embedded calendar, so confirm against your trade confirmation"
        ),
    }
    if notes:
        out["notes"] = notes
    return out


def op_trading_days_between(data):
    start = parse_date(data.get("start"), "start")
    end = parse_date(data.get("end"), "end")
    include_start = _as_bool(data.get("include_start"), "include_start", False)
    if end < start:
        raise InputError("BAD_RANGE", "end", "end is before start")
    _check_covered(start, "start")
    _check_covered(end, "end")
    days = []
    cur = start if include_start else start + timedelta(days=1)
    while cur <= end:
        if is_trading_day(cur):
            days.append(cur.isoformat())
        cur += timedelta(days=1)
    return {
        "ok": True,
        "trading_days": len(days),
        "first": days[0] if days else None,
        "last": days[-1] if days else None,
        "counting": "start %s, end included" % ("included" if include_start else "excluded"),
    }


def op_calendar_info(data):
    today_raw = data.get("today")
    if today_raw:
        today = parse_date(today_raw, "today")
    else:
        today = utc_to_et(datetime.now(timezone.utc)).date()
    left = (CALENDAR_VALID_THROUGH - today).days
    return {
        "ok": True,
        "valid_from": CALENDAR_VALID_FROM.isoformat(),
        "valid_through": CALENDAR_VALID_THROUGH.isoformat(),
        "days_until_expiry": left,
        "expiry_warning": left <= EXPIRY_WARNING_DAYS,
        "source": CALENDAR_SOURCE,
        "holidays": {d.isoformat(): n for d, n in sorted(HOLIDAYS.items())},
        "early_closes": {d.isoformat(): n for d, n in sorted(EARLY_CLOSES.items())},
        "settlement_only_holidays": {d.isoformat(): n for d, n in sorted(SETTLEMENT_ONLY_HOLIDAYS.items())},
        "non_regular_windows": NON_REGULAR_WINDOWS,
        "tz_rules": "zoneinfo" if _use_zone() else "embedded US DST rules (2007+)",
    }


OPS = {
    "session": op_session,
    "to_utc": op_to_utc,
    "to_et": op_to_et,
    "next_trading_day": op_next_trading_day,
    "add_days": op_add_days,
    "settlement_date": op_settlement_date,
    "trading_days_between": op_trading_days_between,
    "calendar_info": op_calendar_info,
}


def run(op, data):
    """Library entry point: returns the output dict for one op (never raises InputError)."""
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected one of %s" % (op, ", ".join(sorted(OPS))))
    if not isinstance(data, dict):
        return _err("BAD_INPUT", "", "input must be a JSON object")
    try:
        return OPS[op](data)
    except InputError as exc:
        return _err(exc.code, exc.field, exc.msg)


# ---------------------------------------------------------------------------------------------
# JSON Schemas (--schema) and a small validator used by --selftest
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_B = {"type": "boolean"}
_DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_NOTES = {"type": "array", "items": _S}
_ERR = {
    "type": "object",
    "required": ["ok", "errors"],
    "properties": {
        "ok": {"enum": [False]},
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["code", "field", "msg"],
                "properties": {"code": _S, "field": _S, "msg": _S},
            },
        },
    },
}


def _obj(props, required=(), extra=False):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": extra}


def _out(success):
    return {"anyOf": [success, _ERR]}


SCHEMAS = {
    "session": {
        "input": _obj({"now": _S, "now_source": {"enum": list(NOW_SOURCES)}}),
        "output": _out(_obj({
            "ok": {"enum": [True]}, "now_et": _S, "now_utc": _S, "trading_day": _B,
            "holiday": _SN, "early_close": _B, "regular_open_et": _SN, "regular_close_et": _SN,
            "in_regular_session": _B,
            "session": {"enum": ["regular", "outside_regular_trading_day", "closed_day"]},
            "non_regular": _obj({"windows_verified": _B, "candidate": _SN, "ask_user": _SN},
                                ["windows_verified", "candidate", "ask_user"]),
            "next_regular_open_et": _SN, "calendar_valid_through": _DATE,
            "now_source": {"enum": list(NOW_SOURCES) + ["unspecified"]}, "notes": _NOTES,
        }, ["ok", "now_et", "trading_day", "holiday", "early_close", "in_regular_session", "session",
            "non_regular", "next_regular_open_et", "calendar_valid_through", "now_source"])),
    },
    "to_utc": {
        "input": _obj({"local": _S, "tz": _S, "date": _DATE, "fold": {"type": ["integer", "boolean"]}}),
        "output": _out(_obj({"ok": {"enum": [True]}, "utc": _S, "local": _S, "tz": _S, "notes": _NOTES},
                            ["ok", "utc"])),
    },
    "to_et": {
        "input": _obj({"utc": _S}, ["utc"]),
        "output": _out(_obj({"ok": {"enum": [True]}, "et": _S, "date_et": _DATE, "offset": _S},
                            ["ok", "et", "date_et"])),
    },
    "next_trading_day": {
        "input": _obj({"date": _DATE, "inclusive": _B}, ["date"]),
        "output": _out(_obj({"ok": {"enum": [True]}, "date": _DATE, "inclusive": _B}, ["ok", "date"])),
    },
    "add_days": {
        "input": _obj({"date": _DATE, "days": {"type": ["integer", "string"]}}, ["date", "days"]),
        "output": _out(_obj({"ok": {"enum": [True]}, "date": _DATE}, ["ok", "date"])),
    },
    "settlement_date": {
        "input": _obj({"trade_date": _DATE}, ["trade_date"]),
        "output": _out(_obj({"ok": {"enum": [True]}, "trade_date": _DATE, "trade_date_effective": _DATE,
                             "settlement_date": _DATE, "basis": _S, "notes": _NOTES},
                            ["ok", "trade_date", "settlement_date", "basis"])),
    },
    "trading_days_between": {
        "input": _obj({"start": _DATE, "end": _DATE, "include_start": _B}, ["start", "end"]),
        "output": _out(_obj({"ok": {"enum": [True]}, "trading_days": {"type": "integer"}, "first": _SN,
                             "last": _SN, "counting": _S}, ["ok", "trading_days"])),
    },
    "calendar_info": {
        "input": _obj({"today": _DATE}),
        "output": _out(_obj({"ok": {"enum": [True]}, "valid_from": _DATE, "valid_through": _DATE,
                             "days_until_expiry": {"type": "integer"}, "expiry_warning": _B, "source": _S,
                             "holidays": {"type": "object"}, "early_closes": {"type": "object"},
                             "settlement_only_holidays": {"type": "object"},
                             "non_regular_windows": {"type": "object"}, "tz_rules": _S},
                            ["ok", "valid_through", "days_until_expiry", "expiry_warning"])),
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
    """Minimal JSON Schema check (type, enum, pattern, properties, required,
    additionalProperties, items, anyOf) - enough to keep --schema honest."""
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
            elif isinstance(extra, dict):
                errs.extend(schema_errors(sub, extra, "%s.%s" % (path, key)))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs.extend(schema_errors(item, schema["items"], "%s[%d]" % (path, i)))
    return errs


def full_schema():
    ops = {}
    for name, pair in SCHEMAS.items():
        ops[name] = {
            "input": dict(pair["input"], **{"$schema": "https://json-schema.org/draft/2020-12/schema"}),
            "output": dict(pair["output"], **{"$schema": "https://json-schema.org/draft/2020-12/schema"}),
        }
    return {"script": SCRIPT, "version": VERSION, "ops": ops}


# ---------------------------------------------------------------------------------------------
# Self-test examples (the golden tests in tests/golden/rh_time/ cover much more)
# ---------------------------------------------------------------------------------------------
EXAMPLES = [
    ("session", {"now": "2026-11-16T20:05:00-05:00", "now_source": "user_stated"},
     {"ok": True, "now_et": "2026-11-16T20:05:00-05:00", "trading_day": True, "holiday": None,
      "early_close": False, "regular_open_et": "09:30", "regular_close_et": "16:00",
      "in_regular_session": False, "session": "outside_regular_trading_day",
      "non_regular": {"windows_verified": False, "candidate": None, "ask_user": ASK_SESSION},
      "next_regular_open_et": "2026-11-17T09:30:00-05:00", "calendar_valid_through": "2027-12-31",
      "now_source": "user_stated"}),
    ("session", {"now": "2026-11-27T12:59:59-05:00", "now_source": "user_stated"},
     {"in_regular_session": True, "early_close": True, "regular_close_et": "13:00"}),
    ("session", {"now": "2026-11-26T11:00:00-05:00", "now_source": "user_stated"},
     {"trading_day": False, "session": "closed_day", "holiday": "Thanksgiving Day",
      "next_regular_open_et": "2026-11-27T09:30:00-05:00"}),
    ("session", {"now": "2028-01-03T10:00:00-05:00", "now_source": "user_stated"},
     {"ok": False, "errors": [{"code": "CALENDAR_EXPIRED", "field": "now"}]}),
    ("to_utc", {"date": "2026-10-17"}, {"ok": True, "utc": "2026-10-17T04:00:00Z"}),
    ("to_utc", {"local": "2026-10-17T00:00:00", "tz": "America/New_York"},
     {"ok": True, "utc": "2026-10-17T04:00:00Z"}),
    ("to_et", {"utc": "2026-11-17T01:05:00.5Z"},
     {"ok": True, "et": "2026-11-16T20:05:00-05:00", "date_et": "2026-11-16"}),
    ("next_trading_day", {"date": "2027-01-01", "inclusive": True}, {"ok": True, "date": "2027-01-04"}),
    ("add_days", {"date": "2026-11-16", "days": 31}, {"ok": True, "date": "2026-12-17"}),
    ("settlement_date", {"trade_date": "2026-10-09"}, {"ok": True, "settlement_date": "2026-10-13"}),
    ("trading_days_between", {"start": "2026-11-20", "end": "2026-11-30"}, {"ok": True, "trading_days": 5}),
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
        print(json.dumps(_err("MISSING_OP", "op", "usage: rh_time.py <op> < input.json; ops: %s"
                              % ", ".join(sorted(OPS)))))
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
