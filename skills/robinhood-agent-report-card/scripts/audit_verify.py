#!/usr/bin/env python3
"""audit_verify.py - verify this kit's local audit log and summarize what it recorded.

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: the robinhood-trading plugin's hook appends one JSON line per Robinhood tool call
to $STATE/audit/audit-YYYY-MM.jsonl. Every line carries `prev`, the sha256 of the previous line's
exact bytes, so editing or removing a line in the middle breaks the chain from that point on. This
script recomputes the chain across every monthly file, then pulls out what the agent report card
needs: order simulations (with the quote seen at review time), the orders this kit sent (and whether
a same-session review with the same order fingerprint came first), guard blocks, permission prompts,
unknown tools and possible prompt injections.

Tamper-EVIDENT, not tamper-proof. A broken chain proves a line was changed or removed. An intact
chain proves only that the lines present are consistent: the newest lines can be dropped, and a
whole log can be rewritten by anyone who can write the files, without leaving a break.

Possible prompt injection = an attempt to place, exercise, cancel or confirm-delete (`delete_alert`
with `confirm: true`) within 3 tool calls after a tool that returns outside text (get_equity_news,
get_sec_filing, get_alert_log, get_scans, run_scan, get_watchlist_items, get_politician_trades) in
the same session, or a guard block whose `after_tool` is one of those tools. It is a flag for a human
to read, not a verdict.

Input  (op `run`): {"dir"?: "<audit directory>", "window"?: {"start","end"} | {"start_date","end_date"},
                    "ledger"?: "<path to ledger.jsonl>" | false}
  dir     default: ${ROBINHOOD_SKILLS_STATE:-${XDG_STATE_HOME:-~/.local/state}/robinhood-skills}/audit
  window  start inclusive, end exclusive, ISO 8601 with Z or an offset; or ET calendar dates
          (start_date..end_date, both inclusive). The chain is always checked in full; the window
          only filters the summaries.
  ledger  default: <dir>/../ledger.jsonl when it exists; false skips it.
Output: {"ok":true,"available","files","lines","chain_ok","breaks":[{"file","line","reason","detail"}],
         "truncated_start","first_ts","last_ts","covers_window","reviews","quotes_at_review":[...],
         "places":[...],"blocked":[...],"reason_counts",{...},"asks":[...],"possible_injection":[...],
         "unknown_tools":[...],"modes_seen":[...],"seq_anomalies":[...],"notes":[...]}

Usage:
    python3 audit_verify.py run < input.json > output.json
    python3 audit_verify.py --schema | --selftest
Contract: stdlib only, Python >= 3.9, no network, reads files but never writes them; one JSON object
out; exit 0 whenever JSON was printed, exit 1 only on a crash.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

VERSION = "2.0.0"
SCRIPT = "audit_verify"

FILE_RE = re.compile(r"^audit-(\d{4})-(\d{2})\.jsonl$")
BLOCKED_FILE = "blocked.jsonl"
REVIEW_TOOLS = ("review_equity_order", "review_advanced_order", "review_option_order", "preview_crypto_order")
UNTRUSTED_TOOLS = ("get_equity_news", "get_sec_filing", "get_alert_log", "get_scans", "run_scan",
                   "get_watchlist_items", "get_politician_trades")
MONEY_RE = re.compile(r"^(place|exercise|replace)_")
INJECTION_LOOKBACK = 3
FAMILY = {"place_equity_order": "equity", "place_option_order": "option", "place_crypto_order": "crypto",
          "place_advanced_order": "oco", "exercise_option": "option"}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
QUOTE_LOOKBACK = timedelta(days=1)


class InputError(Exception):
    def __init__(self, code, field, msg):
        Exception.__init__(self, msg)
        self.code, self.field, self.msg = code, field, msg


def _err(code, field, msg):
    return {"ok": False, "errors": [{"code": code, "field": field, "msg": msg}]}


# ---------------------------------------------------------------------------------------------
# Time helpers (US Eastern via the 2007+ DST rule, so no tzdata is needed)
# ---------------------------------------------------------------------------------------------
_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?\s*(Z|z|[+-]\d{2}:?\d{2})?$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_ts(value, naive_is_utc=True):
    """ISO 8601 -> aware UTC datetime, or None. A value without an offset is UTC (the connector's
    convention) unless naive_is_utc is False, in which case it is rejected (None)."""
    if not isinstance(value, str):
        return None
    m = _TS_RE.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, off = m.groups()
    try:
        micro = int((frac or "0")[:6].ljust(6, "0"))
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), micro)
    except ValueError:
        return None
    if off is None:
        if not naive_is_utc:
            return None
        return dt.replace(tzinfo=timezone.utc)
    if off in ("Z", "z"):
        return dt.replace(tzinfo=timezone.utc)
    sign = 1 if off[0] == "+" else -1
    digits = off[1:].replace(":", "")
    delta = timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return (dt - sign * delta).replace(tzinfo=timezone.utc)


def _nth_sunday(year, month, n):
    first = datetime(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def et_offset(dt_utc):
    start = _nth_sunday(dt_utc.year, 3, 2).replace(hour=7, tzinfo=timezone.utc)
    end = _nth_sunday(dt_utc.year, 11, 1).replace(hour=6, tzinfo=timezone.utc)
    return timedelta(hours=-4) if start <= dt_utc < end else timedelta(hours=-5)


def fmt_et(dt):
    if dt is None:
        return None
    return (dt + et_offset(dt)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M ET")


def fmt_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(date_text, field):
    m = _DATE_RE.match(str(date_text or "").strip())
    if not m:
        raise InputError("BAD_WINDOW", field, "expected an ET date YYYY-MM-DD, got %r" % (date_text,))
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise InputError("BAD_WINDOW", field, "not a calendar date: %r" % (date_text,))


def et_midnight_utc(local):
    """Midnight US Eastern on the naive date `local` -> aware UTC (midnight is never in a DST gap)."""
    guess = (local + timedelta(hours=5)).replace(tzinfo=timezone.utc)
    return (local - et_offset(guess)).replace(tzinfo=timezone.utc)


def parse_window(window):
    """Returns (start, end) aware UTC datetimes or (None, None). End is exclusive."""
    if window is None:
        return None, None
    if not isinstance(window, dict):
        raise InputError("BAD_WINDOW", "window", "window must be an object")
    if "start" in window or "end" in window:
        start = parse_ts(window.get("start"), naive_is_utc=False)
        end = parse_ts(window.get("end"), naive_is_utc=False)
        if start is None or end is None:
            raise InputError("BAD_WINDOW", "window", "window.start and window.end must be ISO 8601 times with Z "
                             "or an offset (convert ET dates with rh_time.py to_utc, or pass start_date/end_date)")
    elif "start_date" in window and "end_date" in window:
        start = et_midnight_utc(parse_date(window.get("start_date"), "window.start_date"))
        end = et_midnight_utc(parse_date(window.get("end_date"), "window.end_date") + timedelta(days=1))
    else:
        raise InputError("BAD_WINDOW", "window", "give window.start and window.end, or window.start_date and "
                         "window.end_date")
    if not start < end:
        raise InputError("BAD_WINDOW", "window", "window start must be before its end")
    return start, end


def in_window(dt, start, end):
    if start is None:
        return True
    return dt is not None and start <= dt < end


# ---------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------
def default_audit_dir():
    root = os.environ.get("ROBINHOOD_SKILLS_STATE")
    if not root:
        xdg = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
        root = os.path.join(xdg, "robinhood-skills")
    return os.path.join(root, "audit")


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def load_dir(directory, ledger_opt):
    """Returns (files [(name, bytes)], blocked bytes|None, ledger bytes|None, notes)."""
    notes = []
    files = []
    for name in sorted(os.listdir(directory)):
        m = FILE_RE.match(name)
        if m and os.path.isfile(os.path.join(directory, name)):
            files.append((name, _read_bytes(os.path.join(directory, name))))
    files.sort(key=lambda item: FILE_RE.match(item[0]).groups())
    blocked = b""
    for name in sorted(os.listdir(directory)):
        # blocked.jsonl, plus blocked.jsonl.folding-* batches the hook is part-way through folding
        if (name == BLOCKED_FILE or name.startswith(BLOCKED_FILE + ".folding-")) and os.path.isfile(
                os.path.join(directory, name)):
            data = _read_bytes(os.path.join(directory, name))
            blocked += data if data.endswith(b"\n") or not data else data + b"\n"
    blocked = blocked or None
    ledger = None
    if ledger_opt is not False:
        lpath = ledger_opt if isinstance(ledger_opt, str) and ledger_opt else os.path.join(
            os.path.dirname(os.path.abspath(directory)), "ledger.jsonl")
        lpath = os.path.expanduser(lpath)
        if os.path.isfile(lpath):
            ledger = _read_bytes(lpath)
        elif isinstance(ledger_opt, str) and ledger_opt:
            notes.append("ledger not found at the path given; review quotes come from the audit lines only")
    return files, blocked, ledger, notes


def split_lines(data):
    """Split file bytes into lines without the newline. Returns (lines, missing_final_newline)."""
    if not data:
        return [], False
    parts = data.split(b"\n")
    if data.endswith(b"\n"):
        return parts[:-1], False
    return parts, True


def _bare(tool):
    if not isinstance(tool, str) or not tool:
        return None
    return tool.rsplit("__", 1)[-1]


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _str_or_none(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    return str(value)


def normalize(obj, file_name, line_no, folded=True):
    return {
        "seq": obj.get("seq") if isinstance(obj.get("seq"), int) and not isinstance(obj.get("seq"), bool) else None,
        "ts": obj.get("ts") if isinstance(obj.get("ts"), str) else None,
        "dt": parse_ts(obj.get("ts")),
        "session": obj.get("session") if isinstance(obj.get("session"), str) else None,
        "event": obj.get("event") if isinstance(obj.get("event"), str) else None,
        "tool": _bare(obj.get("tool")),
        "class": obj.get("class") if isinstance(obj.get("class"), str) else None,
        "decision": obj.get("decision") if isinstance(obj.get("decision"), str) else None,
        "reason_code": obj.get("reason_code") if isinstance(obj.get("reason_code"), str) else None,
        "mode": obj.get("mode") if isinstance(obj.get("mode"), str) else None,
        "fingerprint": obj.get("fingerprint") if isinstance(obj.get("fingerprint"), str) else None,
        "ticket_id": obj.get("ticket_id") if isinstance(obj.get("ticket_id"), str) else None,
        "ref_id": obj.get("ref_id") if isinstance(obj.get("ref_id"), str) else None,
        "inputs": _as_dict(obj.get("inputs")),
        "result": _as_dict(obj.get("result")),
        "after_tool": _bare(obj.get("after_tool")),
        "folded": folded,
        "file": file_name,
        "line": line_no,
    }


# ---------------------------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------------------------
def verify_chain(files):
    """Recompute the hash chain across files in order. Returns (events, stats)."""
    events = []
    breaks = []
    seq_anomalies = []
    notes = []
    prev_hash = None
    prev_seq = None
    prev_where = None
    first_line_seen = False
    truncated_start = False
    total = 0
    for name, data in files:
        lines, missing_nl = split_lines(data)
        if missing_nl and lines:
            notes.append("%s: the last line has no newline (a write may have been interrupted)" % name)
        for idx, raw in enumerate(lines, start=1):
            total += 1
            line_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                obj = None
            if not isinstance(obj, dict):
                breaks.append({"file": name, "line": idx, "reason": "unreadable_line",
                               "detail": "not a JSON object: an interrupted write, damage, or a hand edit"})
                prev_hash, prev_seq, prev_where = line_hash, None, "%s line %d" % (name, idx)
                first_line_seen = True
                continue
            prev = obj.get("prev")
            prev = prev if isinstance(prev, str) else ""
            if not first_line_seen:
                if not prev.startswith("genesis:"):
                    truncated_start = True
            elif prev == prev_hash:
                pass
            elif prev.startswith("genesis:"):
                breaks.append({"file": name, "line": idx, "reason": "chain_restarted",
                               "detail": "a new genesis line in the middle of the log: the state directory was "
                                         "reset or a file was replaced"})
            else:
                breaks.append({"file": name, "line": idx, "reason": "prev_mismatch",
                               "detail": "does not chain to %s: that line was changed, or lines between them "
                                         "were removed" % prev_where})
            seq = obj.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                if prev_seq is not None and seq != prev_seq + 1:
                    seq_anomalies.append({"file": name, "line": idx, "expected": prev_seq + 1, "found": seq})
                prev_seq = seq
            else:
                prev_seq = None
            first_line_seen = True
            prev_hash = line_hash
            prev_where = "%s line %d" % (name, idx)
            events.append(normalize(obj, name, idx))
    return events, {"breaks": breaks, "truncated_start": truncated_start, "lines": total,
                    "seq_anomalies": seq_anomalies, "notes": notes}


def parse_jsonl(data):
    out = []
    if not data:
        return out
    lines, _ = split_lines(data)
    for idx, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(obj, dict):
            out.append((idx, obj))
    return out


def _quote(result):
    q = result.get("quote")
    if not isinstance(q, dict):
        return None
    bid, ask = _str_or_none(q.get("bid")), _str_or_none(q.get("ask"))
    if bid is None and ask is None:
        return None
    return {"bid": bid, "ask": ask, "ts": _str_or_none(q.get("ts"))}


def _result_ok(result):
    return result.get("ok") is not False


def summarize(events, blocked_extra, ledger_rows, start, end):
    reviews_count = 0
    quotes = []
    review_index = {}  # (session, fingerprint) -> [(dt, quote)]
    seen_quote_keys = set()
    for ev in events:
        if ev["event"] != "post" or ev["tool"] not in REVIEW_TOOLS:
            continue
        ok = _result_ok(ev["result"])
        if in_window(ev["dt"], start, end) and ok:
            reviews_count += 1
        if not ok or ev["fingerprint"] is None:
            continue
        q = _quote(ev["result"])
        review_index.setdefault((ev["session"], ev["fingerprint"]), []).append((ev["dt"], q))
        if q is not None and ev["dt"] is not None and (start is None or start - QUOTE_LOOKBACK <= ev["dt"] < end):
            key = (ev["session"], ev["fingerprint"], ev["ts"])
            seen_quote_keys.add(key)
            quotes.append({"fingerprint": ev["fingerprint"], "session": ev["session"], "ts": ev["ts"],
                           "tool": ev["tool"], "bid": q["bid"], "ask": q["ask"], "quote_ts": q["ts"],
                           "source": "audit"})
    for _idx, row in ledger_rows:
        if row.get("review_ok") is not True or not isinstance(row.get("fingerprint"), str):
            continue
        dt = parse_ts(row.get("ts"))
        session = row.get("session") if isinstance(row.get("session"), str) else None
        q = _quote(row)
        review_index.setdefault((session, row["fingerprint"]), []).append((dt, q))
        key = (session, row["fingerprint"], row.get("ts"))
        if q is not None and key not in seen_quote_keys and dt is not None and (
                start is None or start - QUOTE_LOOKBACK <= dt < end):
            seen_quote_keys.add(key)
            quotes.append({"fingerprint": row["fingerprint"], "session": session, "ts": row.get("ts"),
                           "tool": _bare(row.get("tool")), "bid": q["bid"], "ask": q["ask"], "quote_ts": q["ts"],
                           "source": "ledger"})
    quotes.sort(key=lambda r: (parse_ts(r["ts"]) or EPOCH, r["fingerprint"]))

    places = []
    for ev in events:
        if ev["event"] != "post" or not ev["tool"] or not MONEY_RE.match(ev["tool"]):
            continue
        if not in_window(ev["dt"], start, end):
            continue
        best = None
        if ev["fingerprint"] is not None and ev["dt"] is not None:
            for rdt, q in review_index.get((ev["session"], ev["fingerprint"]), []):
                if rdt is not None and rdt <= ev["dt"] and (best is None or rdt > best[0]):
                    best = (rdt, q)
        inputs, result = ev["inputs"], ev["result"]
        places.append({
            "ts": ev["ts"], "ts_et": fmt_et(ev["dt"]), "tool": ev["tool"], "asset": FAMILY.get(ev["tool"]),
            "session": ev["session"], "fingerprint": ev["fingerprint"], "ticket_id": ev["ticket_id"],
            "ref_id": ev["ref_id"], "order_id": _str_or_none(result.get("order_id")),
            "state": _str_or_none(result.get("state")), "ok": _result_ok(result),
            "symbol": _str_or_none(inputs.get("symbol")), "side": _str_or_none(inputs.get("side")),
            "quantity": _str_or_none(inputs.get("quantity")),
            "dollar_amount": _str_or_none(inputs.get("dollar_amount")),
            "matched_review": best is not None,
            "review_age_s": int(round((ev["dt"] - best[0]).total_seconds())) if best else None,
            "review_quote": best[1] if best else None,
            "mode": ev["mode"],
        })

    blocked = []
    for ev in events:
        if ev["event"] == "pre_block" and in_window(ev["dt"], start, end):
            blocked.append({"ts": ev["ts"], "ts_et": fmt_et(ev["dt"]), "tool": ev["tool"],
                            "reason_code": ev["reason_code"], "after_tool": ev["after_tool"],
                            "session": ev["session"], "folded": True})
    unfolded = []
    for idx, row in blocked_extra:
        if row.get("event") not in ("pre_block", "pre_ask"):
            continue
        dt = parse_ts(row.get("ts"))
        item = {"ts": row.get("ts") if isinstance(row.get("ts"), str) else None, "ts_et": fmt_et(dt),
                "tool": _bare(row.get("tool")),
                "reason_code": row.get("reason_code") if isinstance(row.get("reason_code"), str) else None,
                "after_tool": _bare(row.get("after_tool")), "session": None, "folded": False, "_dt": dt,
                "_event": row["event"], "_line": idx}
        unfolded.append(item)
        if in_window(dt, start, end) and row["event"] == "pre_block":
            blocked.append({k: v for k, v in item.items() if not k.startswith("_")})
    blocked.sort(key=lambda r: (parse_ts(r["ts"]) or EPOCH, r["tool"] or ""))
    reason_counts = {}
    for b in blocked:
        code = b["reason_code"] or "UNSPECIFIED"
        reason_counts[code] = reason_counts.get(code, 0) + 1

    asks = []
    unknown_tools = []
    for ev in events:
        if not in_window(ev["dt"], start, end):
            continue
        if ev["event"] == "pre_ask":
            asks.append({"ts": ev["ts"], "ts_et": fmt_et(ev["dt"]), "tool": ev["tool"], "session": ev["session"],
                         "reason_code": ev["reason_code"], "folded": True})
        if ev["class"] == "unknown":
            unknown_tools.append({"ts": ev["ts"], "tool": ev["tool"], "event": ev["event"],
                                  "decision": ev["decision"], "reason_code": ev["reason_code"]})

    injections = detect_injections(events, unfolded, start, end)
    for ask in asks:
        ask["sent"] = _ask_was_sent(ask, events)
    for item in unfolded:
        if item["_event"] == "pre_ask" and in_window(item["_dt"], start, end):
            # not folded yet, so whether the user approved it is not known from the log
            asks.append({"ts": item["ts"], "ts_et": item["ts_et"], "tool": item["tool"], "session": None,
                         "reason_code": item["reason_code"], "folded": False, "sent": None})
    asks.sort(key=lambda r: (parse_ts(r["ts"]) or EPOCH, r["tool"] or ""))
    modes = sorted({ev["mode"] for ev in events if ev["mode"] and in_window(ev["dt"], start, end)})
    return {"reviews": reviews_count, "quotes_at_review": quotes, "places": places, "blocked": blocked,
            "reason_counts": reason_counts, "asks": asks, "possible_injection": injections,
            "unknown_tools": unknown_tools, "modes_seen": modes}


def _ask_was_sent(ask, events):
    after = False
    for ev in sorted((e for e in events if e["session"] == ask["session"]),
                     key=lambda e: (e["dt"] or EPOCH, e["seq"] or 0)):
        if ev["event"] == "pre_ask" and ev["ts"] == ask["ts"] and ev["tool"] == ask["tool"]:
            after = True
            continue
        if after and ev["event"] in ("post", "pre_ask", "pre_block"):
            return ev["event"] == "post" and ev["tool"] == ask["tool"]
    return False


def _is_attempt(call):
    tool = call["tool"] or ""
    if MONEY_RE.match(tool) or tool.startswith("cancel_"):
        return True
    for ev in call["events"]:
        if ev["class"] == "money" or ev["reason_code"] == "UNKNOWN_MONEY_TOOL":
            return True
    if tool == "delete_alert":
        for ev in call["events"]:
            confirm = ev["inputs"].get("confirm")
            if ev["event"] == "pre_ask" or confirm is True or (isinstance(confirm, str) and confirm.lower() == "true"):
                return True
    return False


def _outcome(call):
    kinds = [ev["event"] for ev in call["events"]]
    if "pre_block" in kinds:
        return "blocked"
    if "pre_ask" in kinds:
        return "asked_then_sent" if "post" in kinds else "asked_not_sent"
    return "sent"


def detect_injections(events, unfolded, start, end):
    calls_by_session = {}
    ordered = sorted((e for e in events if e["event"] in ("post", "pre_block", "pre_ask") and e["tool"]),
                     key=lambda e: (e["session"] or "", e["dt"] or EPOCH, e["seq"] or 0))
    for ev in ordered:
        calls = calls_by_session.setdefault(ev["session"], [])
        if ev["event"] == "post" and calls and calls[-1]["pending_ask"] and calls[-1]["tool"] == ev["tool"]:
            calls[-1]["events"].append(ev)
            calls[-1]["pending_ask"] = False
            continue
        calls.append({"tool": ev["tool"], "events": [ev], "pending_ask": ev["event"] == "pre_ask"})
    found = []
    for session, calls in calls_by_session.items():
        for i, call in enumerate(calls):
            if not _is_attempt(call):
                continue
            first = call["events"][0]
            if not in_window(first["dt"], start, end):
                continue
            source, distance = None, None
            for j in range(i - 1, max(-1, i - 1 - INJECTION_LOOKBACK), -1):
                if calls[j]["tool"] in UNTRUSTED_TOOLS:
                    source, distance = calls[j]["tool"], i - j
                    break
            if source is None:
                for ev in call["events"]:
                    if ev["after_tool"] in UNTRUSTED_TOOLS:
                        source = ev["after_tool"]
                        break
            if source is None:
                continue
            found.append({"ts": first["ts"], "ts_et": fmt_et(first["dt"]), "tool": call["tool"],
                          "after_tool": source, "calls_after": distance, "session": session,
                          "outcome": _outcome(call), "folded": True})
    for item in unfolded:
        tool = item["tool"] or ""
        if not (MONEY_RE.match(tool) or tool.startswith("cancel_") or tool == "delete_alert"
                or item["reason_code"] == "UNKNOWN_MONEY_TOOL"):
            continue
        if item["after_tool"] in UNTRUSTED_TOOLS and in_window(item["_dt"], start, end):
            outcome = "blocked" if item["_event"] == "pre_block" else "asked_not_sent"
            found.append({"ts": item["ts"], "ts_et": item["ts_et"], "tool": tool, "after_tool": item["after_tool"],
                          "calls_after": None, "session": None, "outcome": outcome, "folded": False})
    found.sort(key=lambda r: (parse_ts(r["ts"]) or EPOCH, r["tool"]))
    return found


def verify(files, blocked_bytes=None, ledger_bytes=None, window=None, directory=None, extra_notes=None):
    start, end = parse_window(window)
    events, stats = verify_chain(files)
    notes = list(extra_notes or []) + stats["notes"]
    blocked_extra = parse_jsonl(blocked_bytes)
    ledger_rows = parse_jsonl(ledger_bytes)
    summary = summarize(events, blocked_extra, ledger_rows, start, end)
    stamps = [e["dt"] for e in events if e["dt"] is not None]
    first_dt = min(stamps) if stamps else None
    last_dt = max(stamps) if stamps else None
    covers = None
    if start is not None:
        covers = first_dt is not None and first_dt <= start
        if first_dt is not None and not covers:
            notes.append("the log starts at %s, after the window start: orders before then cannot be attributed"
                         % fmt_et(first_dt))
    if stats["truncated_start"]:
        notes.append("the oldest log lines are gone (the 50 MB retention deletes whole old files, or the log "
                     "was started elsewhere); verification starts at the first line present")
    if blocked_extra:
        notes.append("%d guard line(s) (blocks or prompts) are still in blocked.jsonl, not yet folded into the "
                     "chain; they are reported but not chain-verified" % len(blocked_extra))
    out = {
        "ok": True,
        "available": bool(files) or bool(blocked_extra),
        "dir": directory,
        "files": len(files),
        "lines": stats["lines"],
        "chain_ok": not stats["breaks"],
        "breaks": stats["breaks"],
        "truncated_start": stats["truncated_start"],
        "first_ts": fmt_utc(first_dt) if first_dt else None,
        "last_ts": fmt_utc(last_dt) if last_dt else None,
        "covers_window": covers,
        "window": {"start": fmt_utc(start), "end": fmt_utc(end)} if start is not None else None,
        "seq_anomalies": stats["seq_anomalies"],
        "notes": notes,
    }
    out.update(summary)
    return out


def op_run(data):
    directory = data.get("dir")
    if directory is None:
        directory = default_audit_dir()
    if not isinstance(directory, str) or not directory.strip():
        raise InputError("BAD_INPUT", "dir", "dir must be a path string")
    ledger_opt = data.get("ledger")
    if ledger_opt is not None and ledger_opt is not False and not isinstance(ledger_opt, str):
        raise InputError("BAD_INPUT", "ledger", "ledger must be a path string or false")
    path = os.path.expanduser(directory.strip())
    parse_window(data.get("window"))  # validate before touching the disk
    if not os.path.isdir(path):
        start, end = parse_window(data.get("window"))
        return {"ok": True, "available": False, "dir": directory, "files": 0, "lines": 0, "chain_ok": None,
                "breaks": [], "truncated_start": False, "first_ts": None, "last_ts": None, "covers_window": False
                if start is not None else None,
                "window": {"start": fmt_utc(start), "end": fmt_utc(end)} if start is not None else None,
                "seq_anomalies": [], "reviews": 0, "quotes_at_review": [], "places": [], "blocked": [],
                "reason_counts": {}, "asks": [], "possible_injection": [], "unknown_tools": [], "modes_seen": [],
                "notes": ["no audit directory at %s: the plugin's audit log is not on this machine (skills-only "
                          "install, another surface, or audit_log turned off)" % directory]}
    try:
        files, blocked, ledger, notes = load_dir(path, ledger_opt)
    except OSError as exc:
        raise InputError("READ_FAILED", "dir", "cannot read the audit directory: %s" % exc)
    out = verify(files, blocked, ledger, data.get("window"), directory, notes)
    if not out["available"]:
        out["chain_ok"] = None
        out["notes"].append("the audit directory exists but holds no audit-YYYY-MM.jsonl files yet")
    return out


OPS = {"run": op_run}


def run(op, data):
    if op not in OPS:
        return _err("UNKNOWN_OP", "op", "unknown op %r; expected: run" % op)
    if not isinstance(data, dict):
        return _err("BAD_INPUT", "", "input must be a JSON object")
    try:
        return OPS[op](data)
    except InputError as exc:
        return _err(exc.code, exc.field, exc.msg)


# ---------------------------------------------------------------------------------------------
# JSON Schemas (--schema) and the small validator used by --selftest
# ---------------------------------------------------------------------------------------------
_S = {"type": "string"}
_SN = {"type": ["string", "null"]}
_I = {"type": "integer"}
_IN = {"type": ["integer", "null"]}
_B = {"type": "boolean"}
_BN = {"type": ["boolean", "null"]}


def _obj(props, required=(), extra=False):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": extra}


_ERR = _obj({"ok": {"enum": [False]}, "errors": {"type": "array", "items": _obj(
    {"code": _S, "field": _S, "msg": _S}, ["code", "field", "msg"])}}, ["ok", "errors"])
_WINDOW_IN = {"anyOf": [_obj({"start": _S, "end": _S}, ["start", "end"]),
                        _obj({"start_date": _S, "end_date": _S}, ["start_date", "end_date"])]}
_QUOTE = {"anyOf": [{"type": "null"}, _obj({"bid": _SN, "ask": _SN, "ts": _SN}, ["bid", "ask", "ts"])]}
_BLOCKED = _obj({"ts": _SN, "ts_et": _SN, "tool": _SN, "reason_code": _SN, "after_tool": _SN, "session": _SN,
                 "folded": _B}, ["ts", "tool", "reason_code", "after_tool", "folded"])
SCHEMAS = {
    "run": {
        "input": _obj({"dir": _S, "window": _WINDOW_IN, "ledger": {"type": ["string", "boolean"]}}),
        "output": {"anyOf": [_obj({
            "ok": {"enum": [True]}, "available": _B, "dir": _SN, "files": _I, "lines": _I, "chain_ok": _BN,
            "breaks": {"type": "array", "items": _obj({"file": _S, "line": _I, "reason": _S, "detail": _S},
                                                       ["file", "line", "reason"])},
            "truncated_start": _B, "first_ts": _SN, "last_ts": _SN, "covers_window": _BN,
            "window": {"anyOf": [{"type": "null"}, _obj({"start": _S, "end": _S}, ["start", "end"])]},
            "seq_anomalies": {"type": "array"}, "reviews": _I,
            "quotes_at_review": {"type": "array", "items": _obj({
                "fingerprint": _S, "session": _SN, "ts": _SN, "tool": _SN, "bid": _SN, "ask": _SN, "quote_ts": _SN,
                "source": {"enum": ["audit", "ledger"]}}, ["fingerprint", "ts", "bid", "ask"])},
            "places": {"type": "array", "items": _obj({
                "ts": _SN, "ts_et": _SN, "tool": _S, "asset": _SN, "session": _SN, "fingerprint": _SN,
                "ticket_id": _SN, "ref_id": _SN, "order_id": _SN, "state": _SN, "ok": _B, "symbol": _SN,
                "side": _SN, "quantity": _SN, "dollar_amount": _SN, "matched_review": _B, "review_age_s": _IN,
                "review_quote": _QUOTE, "mode": _SN},
                ["ts", "tool", "fingerprint", "ref_id", "order_id", "matched_review", "review_age_s"])},
            "blocked": {"type": "array", "items": _BLOCKED},
            "reason_counts": {"type": "object", "additionalProperties": _I},
            "asks": {"type": "array", "items": _obj({"ts": _SN, "ts_et": _SN, "tool": _SN, "session": _SN,
                                                     "reason_code": _SN, "sent": _BN, "folded": _B},
                                                    ["ts", "tool", "sent", "folded"])},
            "possible_injection": {"type": "array", "items": _obj({
                "ts": _SN, "ts_et": _SN, "tool": _S, "after_tool": _S, "calls_after": _IN, "session": _SN,
                "outcome": {"enum": ["blocked", "asked_not_sent", "asked_then_sent", "sent"]}, "folded": _B},
                ["ts", "tool", "after_tool", "outcome"])},
            "unknown_tools": {"type": "array"}, "modes_seen": {"type": "array", "items": _S},
            "notes": {"type": "array", "items": _S},
        }, ["ok", "available", "files", "lines", "chain_ok", "breaks", "truncated_start", "reviews", "places",
            "blocked", "asks", "possible_injection", "unknown_tools"]), _ERR]},
    }
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
    """Minimal JSON Schema check (type, enum, properties, required, additionalProperties, items, anyOf)."""
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
# Self-test: small in-memory chains (tests/golden/audit_verify/ and the eval fixture cover more)
# ---------------------------------------------------------------------------------------------
def build_chain(records, genesis="genesis:0000000000000000", prev_hash=None):
    """Serialize records into chained JSONL bytes. Returns (bytes, last_line_hash)."""
    out = []
    prev = prev_hash or genesis
    for rec in records:
        line = dict(rec)
        line["prev"] = prev
        ordered = {"v": 1, "seq": line.pop("seq", None), "ts": line.pop("ts", None), "prev": line.pop("prev")}
        ordered.update(line)
        raw = json.dumps(ordered, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        out.append(raw)
        prev = "sha256:" + hashlib.sha256(raw).hexdigest()
    return b"".join(r + b"\n" for r in out), prev


def _ev(seq, ts, event, tool, cls, **kw):
    rec = {"seq": seq, "ts": ts, "session": kw.pop("session", "s1"), "client": "claude-code", "event": event,
           "tool": tool, "server": "srv-test", "class": cls, "decision": kw.pop("decision", "none"),
           "reason_code": kw.pop("reason_code", None), "mode": kw.pop("mode", "confirm"),
           "fingerprint": kw.pop("fingerprint", None), "ticket_id": None, "ref_id": kw.pop("ref_id", None),
           "inputs": kw.pop("inputs", {}), "result": kw.pop("result", {"ok": True}),
           "after_tool": kw.pop("after_tool", None), "kit_version": VERSION, "policy_sha": None}
    rec.update(kw)
    return rec


def _selftest_records():
    fp = "sha256:" + "ab" * 32
    return [
        _ev(1, "2026-11-12T15:00:00.000Z", "session_start", None, None, result={}),
        _ev(2, "2026-11-12T15:01:00.000Z", "post", "review_equity_order", "simulate", fingerprint=fp,
            result={"ok": True, "alerts_count": 0, "quote": {"bid": "29.46", "ask": "29.50", "ts": "x"}}),
        _ev(3, "2026-11-12T15:02:00.000Z", "post", "place_equity_order", "money", fingerprint=fp,
            ref_id="r-1", inputs={"symbol": "PLTR", "side": "buy", "quantity": "10"},
            result={"ok": True, "order_id": "o-1", "state": "queued"}),
        _ev(4, "2026-11-12T15:13:40.000Z", "post", "get_equity_news", "read", result={"ok": True, "count": 5}),
        _ev(5, "2026-11-12T15:14:05Z", "pre_block", "place_equity_order", "money", decision="deny",
            reason_code="NO_MATCHING_REVIEW", after_tool="get_equity_news", result=None),
    ]


def selftest():
    failures = []
    recs = _selftest_records()
    good, _ = build_chain(recs)
    window = {"start": "2026-11-12T05:00:00Z", "end": "2026-11-13T05:00:00Z"}
    cases = []
    out = verify([("audit-2026-11.jsonl", good)], window=window)
    cases.append(("intact chain", out, {"ok": True, "chain_ok": True, "files": 1, "lines": 5, "reviews": 1,
                                        "truncated_start": False, "reason_counts": {"NO_MATCHING_REVIEW": 1}}))
    cases.append(("place matched to its review", out["places"][0] if out["places"] else {},
                  {"order_id": "o-1", "matched_review": True, "review_age_s": 60,
                   "review_quote": {"bid": "29.46", "ask": "29.50", "ts": "x"}}))
    cases.append(("possible injection", out["possible_injection"][0] if out["possible_injection"] else {},
                  {"tool": "place_equity_order", "after_tool": "get_equity_news", "calls_after": 1,
                   "outcome": "blocked", "ts_et": "2026-11-12 10:14 ET"}))
    lines = good.split(b"\n")
    lines[2] = lines[2].replace(b'"quantity":"10"', b'"quantity":"1"')
    tampered = b"\n".join(lines)
    out2 = verify([("audit-2026-11.jsonl", tampered)], window=window)
    cases.append(("tampered line breaks the next line", out2,
                  {"chain_ok": False, "breaks": [{"file": "audit-2026-11.jsonl", "line": 4, "reason": "prev_mismatch"}]}))
    part_a, last = build_chain(recs[:2])
    part_b, _ = build_chain(recs[2:], prev_hash=last)
    out3 = verify([("audit-2026-10.jsonl", part_a), ("audit-2026-11.jsonl", part_b)], window=window)
    cases.append(("chain continues across monthly files", out3, {"chain_ok": True, "files": 2, "lines": 5}))
    out4 = verify([("audit-2026-11.jsonl", part_b)], window=window)
    cases.append(("missing older file -> truncated start, not a break", out4,
                  {"chain_ok": True, "truncated_start": True}))
    for name, got, expected in cases:
        problems = []
        if not _subset(expected, got):
            problems.append("output mismatch: %s" % json.dumps(got, sort_keys=True, default=str)[:600])
        if isinstance(got, dict) and "chain_ok" in got:
            problems += schema_errors(got, SCHEMAS["run"]["output"])
        if problems:
            failures.append({"case": name, "problems": problems})
    bad = run("run", {"dir": ".", "window": {"start": "2026-11-12", "end": "2026-11-13"}})
    if bad.get("ok") is not False or bad["errors"][0]["code"] != "BAD_WINDOW":
        failures.append({"case": "window without offsets is rejected", "problems": [json.dumps(bad)]})
    return {"ok": not failures, "script": SCRIPT, "selftest": {"cases": len(cases) + 1, "failed": failures}}


def _subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) <= len(actual) and all(
            _subset(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


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
        print(json.dumps(_err("MISSING_OP", "op", "usage: audit_verify.py run < input.json")))
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
