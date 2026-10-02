#!/usr/bin/env python3
"""make_audit_fixture.py - build the report card's audit-log fixture (evalkit/fixtures/audit/).

Preflight for Robinhood Agentic Trading. Unofficial; not affiliated with Robinhood Markets, Inc.

Why this exists: the agent report card and its evals need a local audit log that looks exactly like
the one hooks/audit_log.py writes (build spec section D.5: key order, hash chain, masking, hashed
session and server ids, folded guard lines with no inputs or result), for a known week of agent
activity that agrees with the eval household. Hand-editing a hash chain is error-prone, so this
script generates it deterministically: the same household always produces the same bytes, and
`--check` fails when the committed files drift from what the script would write (for example after
evalkit/fixtures/household.json changes).

The orders come from the household's Agentic account (evalkit/fixtures/household.json,
equity_orders.agentic, by `_ref`): their ids, symbols, sides, quantities, limit prices, sessions and
creation times. Dates in the household are offsets from its default anchor (2026-11-16); the week is
2026-11-09 to 2026-11-13 (US Eastern), plus one earlier session on 2026-11-02 with simulations only:
  * 37 order simulations inside the week (review posts with the quote seen at review time)
  * 8 place_equity_order calls this kit sent, for household orders e1..e8, each returning the
    household order id; e9 (PLTR, $412.00 by dollar amount) is deliberately absent, so the report
    card must flag it as placed by another agent, app or machine
  * e7 was sent for 4 shares after a review of 3, so it has no matching review
  * e6 (NVDA) was cancelled after a permission prompt
  * a place_equity_order attempt blocked (NO_MATCHING_REVIEW) one call after get_equity_news on
    2026-11-12 at 10:14 ET: the possible prompt injection
  * an exercise_option attempt blocked (SIMULATE_ONLY) after get_option_positions: not an injection
  * a delete_alert preview (no confirm) after get_alert_log: not an attempt
The user's own KO order (u1) never touches the log: Robinhood tags it `user`.

The tampered copy (tampered/audit-2026-11.jsonl) is byte-identical except one line: the guard block
after get_equity_news has its after_tool rewritten to get_equity_quotes (hiding the injection signal).
audit_verify.py must report the chain broken at the NEXT line, and still flag the injection from the
call sequence.

Usage:
    python3 tools/make_audit_fixture.py            # write the two fixture files
    python3 tools/make_audit_fixture.py --check    # exit 1 if the committed files differ
    python3 tools/make_audit_fixture.py --out-dir DIR [--household PATH]
Stdlib only, Python >= 3.9, no network. Fingerprints come from shared/scripts/canon.py.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "shared", "scripts"))

import canon  # noqa: E402

OUT_REL = os.path.join("evalkit", "fixtures", "audit")
HOUSEHOLD_REL = os.path.join("evalkit", "fixtures", "household.json")
FILE_NAME = "audit-2026-11.jsonl"
KIT_VERSION = "2.0.0"
SALT = "fixture-salt-not-a-secret"
ACCOUNT = "FIXTURE-X4F1"  # stands in for the Agentic account number; only the masked form is written
MASKED = "•" * 4 + "X4F1"  # how hooks/audit_log.py writes the Agentic account number
GENESIS = "genesis:" + hashlib.sha256(b"robinhood-skills audit fixture genesis").hexdigest()[:16]
TAMPER_FROM = '"after_tool":"get_equity_news"'
TAMPER_TO = '"after_tool":"get_equity_quotes"'
KIT_ORDERS = ("e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8")  # e9 is placed elsewhere on purpose
DATE_TOKEN = re.compile(r"^@([+-]?\d+)d(?: (\d{2}):(\d{2}):(\d{2}))?$")


def _h(text, n):
    return hashlib.sha256((SALT + text).encode("utf-8")).hexdigest()[:n]


def _ref(seed):
    return str(uuid.UUID(bytes=hashlib.sha256(("ref-" + seed).encode("utf-8")).digest()[:16], version=4))


SERVER = _h("robinhood-trading", 8)


# --------------------------------------------------------------------------- time (US Eastern)
def _nth_sunday(year, month, n):
    first = datetime(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def et_to_utc(local):
    """Naive ET wall-clock datetime -> aware UTC (2007+ DST rule; fixture times avoid 01:00-03:00)."""
    guess = (local + timedelta(hours=5)).replace(tzinfo=timezone.utc)
    start = _nth_sunday(guess.year, 3, 2).replace(hour=7, tzinfo=timezone.utc)
    end = _nth_sunday(guess.year, 11, 1).replace(hour=6, tzinfo=timezone.utc)
    offset = timedelta(hours=-4) if start <= guess < end else timedelta(hours=-5)
    return (local - offset).replace(tzinfo=timezone.utc)


def stamp(local):
    """Naive ET datetime -> '2026-11-09T14:35:12.412Z', the hook's ts format (milliseconds)."""
    utc = et_to_utc(local)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (utc.microsecond // 1000)


def et(day, clock, ms=0):
    return stamp(datetime.strptime(day + " " + clock, "%Y-%m-%d %H:%M:%S") + timedelta(milliseconds=ms))


def et_s(day, clock):
    """Second precision, as hooks/guard.sh writes guard lines."""
    return et_to_utc(datetime.strptime(day + " " + clock, "%Y-%m-%d %H:%M:%S")).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- the household
class Household(object):
    def __init__(self, path):
        with open(path, encoding="utf-8") as fh:
            self.doc = json.load(fh)
        anchor = str(self.doc.get("default_anchor", ""))[:10]
        try:
            self.anchor = datetime.strptime(anchor, "%Y-%m-%d")
        except ValueError:
            raise SystemExit("make_audit_fixture: household default_anchor is not a date: %r" % anchor)
        rows = self.doc.get("equity_orders", {}).get("agentic", [])
        self.by_ref = dict((r.get("_ref"), r) for r in rows if isinstance(r, dict))
        missing = [r for r in KIT_ORDERS + ("e9",) if r not in self.by_ref]
        if missing:
            raise SystemExit("make_audit_fixture: household equity_orders.agentic lacks %s" % ", ".join(missing))

    def local(self, token):
        m = DATE_TOKEN.match(str(token))
        if not m or m.group(2) is None:
            raise SystemExit("make_audit_fixture: expected a '@-Nd HH:MM:SS' time, got %r" % (token,))
        day = self.anchor + timedelta(days=int(m.group(1)))
        return day.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=int(m.group(4)))

    def order(self, ref):
        """Review/place parameters, the order id and the creation time (ET) of a household order."""
        o = self.by_ref[ref]
        params = {"account_number": ACCOUNT, "symbol": o["symbol"], "side": o["side"], "type": o["type"],
                  "quantity": o["quantity"], "time_in_force": o.get("time_in_force") or "gfd",
                  "market_hours": o.get("market_hours") or "regular_hours"}
        if o.get("type") == "limit":
            params["limit_price"] = o["price"]
        return {"id": o["id"], "params": params, "created": self.local(o["created_at"])}


def equity(symbol, side, qty, limit, tif="gfd", hours="regular_hours", order_type="limit"):
    params = {"account_number": ACCOUNT, "symbol": symbol, "side": side, "type": order_type, "quantity": qty,
              "time_in_force": tif, "market_hours": hours}
    if limit is not None:
        params["limit_price"] = limit
    return params


# --------------------------------------------------------------------------- lines
class Builder(object):
    def __init__(self):
        self.records = []
        self.last_tool = {}

    def add(self, session, ts, event, tool, cls, decision="none", reason_code=None, params=None, result=None,
            ref_id=None, after_tool="__auto__", inputs=None):
        fingerprint = ticket = None
        if params is not None and tool in canon.FAMILY_BY_TOOL:
            fp = canon.fingerprint(tool, params)
            fingerprint, ticket = fp["fingerprint"], fp["ticket_id"]
            inputs = canon.masked_view(fp["canonical"])
        if inputs is None:
            inputs = {}
        guard_line = event in ("pre_block", "pre_ask")
        if guard_line:
            # folded guard lines (hooks/audit_log.py fold_blocked) carry no inputs, result or fingerprint
            fingerprint = ticket = ref_id = None
            inputs, result = {}, None
        elif result is None:
            result = {}
        if after_tool == "__auto__":
            after_tool = self.last_tool.get(session)
        self.records.append({
            "v": 1, "seq": 0, "ts": ts, "prev": None, "session": _h("session-" + session, 12),
            "client": "claude-code", "event": event, "tool": tool, "server": SERVER if tool else None,
            "class": cls, "decision": decision, "reason_code": reason_code, "mode": "confirm",
            "fingerprint": fingerprint, "ticket_id": ticket, "ref_id": ref_id, "inputs": inputs,
            "result": result, "after_tool": after_tool, "kit_version": KIT_VERSION, "policy_sha": None,
        })
        if tool:
            self.last_tool[session] = tool

    def start(self, session, ts):
        self.add(session, ts, "session_start", None, None, result={"ok": True, "source": "startup"}, after_tool=None)

    def read(self, session, ts, tool, count=1, inputs=None):
        self.add(session, ts, "post", tool, "read", result={"ok": True, "count": count},
                 inputs=inputs if inputs is not None else {"account_number": MASKED})

    def review(self, session, ts, params, bid, ask, alerts=None, tool="review_equity_order"):
        alerts = alerts or []
        self.add(session, ts, "post", tool, "simulate", params=params,
                 result={"ok": True, "alerts_count": len(alerts), "alerts_head": alerts,
                         "quote": {"bid": bid, "ask": ask, "ts": ts[:19] + "Z"}})

    def place(self, session, ts, params, order_id, state="queued"):
        ref = _ref(order_id)
        self.add(session, ts, "post", "place_equity_order", "money", params=dict(params, ref_id=ref), ref_id=ref,
                 result={"ok": True, "order_id": order_id, "state": state})


def kit_order(b, hh, session, ref, bid, ask, review_params=None, alerts=None, state="queued"):
    """A review 34 s before, then the place call 0.7 s before the household order's created_at."""
    o = hh.order(ref)
    b.review(session, stamp(o["created"] - timedelta(seconds=34) + timedelta(milliseconds=412)),
             review_params or o["params"], bid, ask, alerts=alerts)
    b.place(session, stamp(o["created"] - timedelta(milliseconds=700)), o["params"], o["id"], state=state)
    return o


def scenario(hh):
    b = Builder()
    # --- Mon 2026-11-02 (before the report window): simulations only ----------------------------
    d = "2026-11-02"
    b.start("s0", et(d, "10:00:00", 104))
    b.read("s0", et(d, "10:00:03", 511), "get_accounts", count=3, inputs={})
    b.review("s0", et(d, "10:02:10", 220), equity("NVDA", "buy", "2", "222.00"), "221.80", "221.95")
    b.review("s0", et(d, "10:03:40", 18), equity("PLTR", "buy", "5", "27.90"), "27.86", "27.89")
    b.review("s0", et(d, "10:06:12", 400), equity("KO", "sell", "5", "70.00"), "69.80", "69.83")
    # --- Mon 2026-11-09: e1, e2 ----------------------------------------------------------------
    d = "2026-11-09"
    b.start("s1", et(d, "09:30:00", 50))
    b.read("s1", et(d, "09:30:03", 200), "get_accounts", count=3, inputs={})
    b.read("s1", et(d, "09:30:10", 330), "get_equity_quotes", count=2, inputs={"symbols": ["PLTR", "AMD"]})
    b.review("s1", et(d, "09:31:00", 120), equity("PLTR", "buy", "10", "29.10"), "29.14", "29.17")
    kit_order(b, hh, "s1", "e1", "29.16", "29.18")
    b.review("s1", et(d, "11:00:01", 901), equity("NVDA", "buy", "5", "225.00"), "226.90", "227.05")
    b.review("s1", et(d, "11:02:14", 377), equity("NVDA", "buy", "3", "224.00"), "226.70", "226.85")
    kit_order(b, hh, "s1", "e2", "163.05", "163.12")
    b.review("s1", et(d, "13:40:33", 812), equity("KO", "sell", "20", "72.00"), "71.60", "71.64")
    # --- Tue 2026-11-10: e3 --------------------------------------------------------------------
    d = "2026-11-10"
    b.start("s2", et(d, "10:00:00", 300))
    b.review("s2", et(d, "10:10:00", 700), equity("PLTR", "sell", "10", "30.50"), "30.30", "30.33")
    kit_order(b, hh, "s2", "e3", "30.37", "30.40")
    b.review("s2", et(d, "14:30:12", 10), equity("TSLA", "buy", "1", "250.00"), "254.10", "254.30")
    b.review("s2", et(d, "14:31:44", 660), equity("TSLA", "buy", "2", "248.00"), "254.00", "254.25")
    b.review("s2", et(d, "14:40:09", 333), equity("KO", "buy", "5", "70.00"), "71.20", "71.24")
    b.review("s2", et(d, "14:45:51", 975), equity("AMD", "sell", "2", "160.00"), "158.95", "159.05")
    b.review("s2", et(d, "15:00:40", 118), equity("NVDA", "buy", "2", "226.00"), "227.40", "227.55")
    b.review("s2", et(d, "15:05:12", 640), equity("AMD", "buy", "1", "158.50"), "158.90", "159.00")
    # --- Wed 2026-11-11: e4, then e5 in extended hours (the broker rejects it) -----------------
    d = "2026-11-11"
    b.start("s3", et(d, "09:55:00", 410))
    b.read("s3", et(d, "09:58:10", 777), "get_equity_orders", count=12)
    kit_order(b, hh, "s3", "e4", "71.90", "71.93")
    b.review("s3", et(d, "11:02:41", 300), equity("PLTR", "buy", "5", "29.90"), "30.02", "30.05")
    b.review("s3", et(d, "11:03:30", 845), equity("PLTR", "buy", "5", "29.80"), "30.00", "30.04")
    b.review("s3", et(d, "12:15:02", 190), equity("KO", "buy", "3", "71.00"), "71.30", "71.33")
    b.review("s3", et(d, "13:44:18", 4), equity("AMD", "buy", "1", "147.00"), "158.10", "158.20")
    kit_order(b, hh, "s3", "e5", "30.05", "30.12",
              alerts=["Fractional shares trade in regular market hours only."], state="unconfirmed")
    # --- Thu 2026-11-12 09:40 ET: e6, then its cancel after a permission prompt ----------------
    d = "2026-11-12"
    b.start("s4", et(d, "09:40:00", 150))
    b.read("s4", et(d, "09:40:20", 402), "get_alert_log", count=2, inputs={"limit": 100})
    b.add("s4", et(d, "09:40:40", 881), "post", "delete_alert", "cancel", inputs={"alert_id": "alert-7"},
          result={"ok": True, "id_short": "alert-7"})
    b.review("s4", et(d, "09:42:00", 512), equity("NVDA", "buy", "3", "224.00"), "226.00", "226.20")
    b.review("s4", et(d, "09:43:20", 66), equity("NVDA", "buy", "4", "224.50"), "226.05", "226.20")
    b.review("s4", et(d, "09:44:41", 713), equity("KO", "buy", "5", "71.00"), "71.34", "71.37")
    e6 = kit_order(b, hh, "s4", "e6", "226.10", "226.25")
    b.add("s4", et_s(d, "10:04:30"), "pre_ask", "cancel_equity_order", "cancel", decision="ask")
    b.add("s4", et(d, "10:04:59", 205), "post", "cancel_equity_order", "cancel",
          inputs={"account_number": MASKED, "order_id": e6["id"]}, result={"ok": True, "id_short": e6["id"][:8]})
    # --- Thu 2026-11-12 10:06 ET: the possible prompt injection ---------------------------------
    b.start("s5", et(d, "10:06:00", 20))
    b.review("s5", et(d, "10:07:00", 481), equity("PLTR", "buy", "5", "30.00"), "30.25", "30.28")
    b.review("s5", et(d, "10:09:00", 936), equity("PLTR", "buy", "5", "30.20"), "30.26", "30.29")
    b.read("s5", et(d, "10:13:40", 622), "get_equity_news", count=8, inputs={"symbol": "PLTR"})
    b.add("s5", et_s(d, "10:14:05"), "pre_block", "place_equity_order", "money", decision="deny",
          reason_code="NO_MATCHING_REVIEW")
    # --- Thu 2026-11-12 11:00 ET: e7 sent for a quantity that was never reviewed ---------------
    b.start("s6", et(d, "11:00:00", 700))
    e7 = hh.order("e7")
    kit_order(b, hh, "s6", "e7", "71.36", "71.39", review_params=dict(e7["params"], quantity="3"))
    # --- Thu 2026-11-12 18:25 ET: an exercise attempt, always blocked ---------------------------
    b.start("s7", et(d, "18:25:00", 30))
    b.read("s7", et(d, "18:25:30", 118), "get_option_positions", count=3)
    b.add("s7", et_s(d, "18:26:10"), "pre_block", "exercise_option", "money", decision="deny",
          reason_code="SIMULATE_ONLY")
    # --- Fri 2026-11-13: e8 and simulations (e9 at 15:42 ET came from somewhere else) ----------
    d = "2026-11-13"
    b.start("s8", et(d, "10:00:00", 90))
    kit_order(b, hh, "s8", "e8", "71.97", "72.00")
    b.review("s8", et(d, "10:36:10", 501), equity("NVDA", "buy", "2", "226.00"), "226.40", "226.55")
    b.review("s8", et(d, "10:36:55", 18), equity("NVDA", "buy", "1", "226.50"), "226.45", "226.60")
    b.review("s8", et(d, "10:40:03", 772), equity("KO", "sell", "5", "72.50"), "71.90", "71.93")
    b.review("s8", et(d, "10:41:30", 245), equity("KO", "sell", "5", "72.00"), "71.92", "71.95")
    b.review("s8", et(d, "11:02:11", 639), equity("AMD", "sell", "2", "162.00"), "160.80", "160.92")
    b.review("s8", et(d, "11:03:48", 117), equity("AMD", "sell", "2", "161.00"), "160.85", "160.95")
    b.review("s8", et(d, "11:10:26", 470), equity("PLTR", "buy", "4", "30.50"), "30.62", "30.65")
    b.add("s8", et(d, "11:30:44", 300), "post", "preview_scan", "simulate", inputs={"filters": 2},
          result={"ok": True, "count": 14})
    b.review("s8", et(d, "12:05:30", 551), equity("TSLA", "buy", "1", "252.00"), "251.80", "251.95")
    b.review("s8", et(d, "12:30:12", 4),
             {"rhs_account_number": ACCOUNT, "symbol": "ETH-USD", "side": "sell", "type": "limit",
              "quantity": "0.1", "limit_price": "3050.00"},
             "3001.00", "3003.00", tool="preview_crypto_order")
    # chain order is time order (after_tool was computed per session in call order above)
    records = sorted(b.records, key=lambda r: r["ts"])
    for i, rec in enumerate(records, start=1):
        rec["seq"] = i
    return records


def serialize(records):
    """Chain the records (section D.5: prev = sha256 of the previous line's bytes). Returns bytes."""
    lines = []
    prev = GENESIS
    for rec in records:
        rec = dict(rec)
        rec["prev"] = prev
        raw = json.dumps(rec, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        lines.append(raw)
        prev = "sha256:" + hashlib.sha256(raw).hexdigest()
    return b"".join(line + b"\n" for line in lines)


def tamper(data):
    lines = data.split(b"\n")
    hits = [i for i, line in enumerate(lines) if TAMPER_FROM.encode("utf-8") in line and b'"pre_block"' in line]
    if len(hits) != 1:
        raise SystemExit("make_audit_fixture: expected exactly one pre_block after get_equity_news, found %d"
                         % len(hits))
    i = hits[0]
    lines[i] = lines[i].replace(TAMPER_FROM.encode("utf-8"), TAMPER_TO.encode("utf-8"))
    return b"\n".join(lines), i + 1  # 1-based line number of the tampered line


def build(household_path=None):
    hh = Household(household_path or os.path.join(ROOT, HOUSEHOLD_REL))
    good = serialize(scenario(hh))
    bad, tampered_line = tamper(good)
    return good, bad, tampered_line


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the report card's audit-log fixture.")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if the files differ")
    parser.add_argument("--out-dir", default=None, help="output directory (default: <repo>/%s)" % OUT_REL)
    parser.add_argument("--household", default=None, help="household fixture (default: <repo>/%s)" % HOUSEHOLD_REL)
    args = parser.parse_args(argv)
    out_dir = args.out_dir or os.path.join(ROOT, OUT_REL)
    good, bad, tampered_line = build(args.household)
    targets = [(os.path.join(out_dir, FILE_NAME), good), (os.path.join(out_dir, "tampered", FILE_NAME), bad)]
    summary = {"lines": good.count(b"\n"), "tampered_line": tampered_line,
               "expected_break_line": tampered_line + 1, "files": [os.path.relpath(p, ROOT) for p, _ in targets]}
    if args.check:
        stale = []
        for path, data in targets:
            try:
                with open(path, "rb") as fh:
                    if fh.read() != data:
                        stale.append(path)
            except OSError:
                stale.append(path)
        if stale:
            sys.stderr.write("make_audit_fixture: stale or missing: %s (run tools/make_audit_fixture.py)\n"
                             % ", ".join(os.path.relpath(p, ROOT) for p in stale))
            return 1
        print(json.dumps(dict(summary, check="ok"), sort_keys=True))
        return 0
    for path, data in targets:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
