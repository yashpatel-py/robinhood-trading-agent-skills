"""Tests for skills/robinhood-agent-report-card/scripts/audit_verify.py (local audit-log verification)."""

import ast
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "robinhood-agent-report-card", "scripts")
NAME = "audit_verify"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
FIXTURE = os.path.join(ROOT, "evalkit", "fixtures", "audit")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import audit_verify  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}


def strict(expected, actual):
    """Listed dict keys must match; lists must match in length and order."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and strict(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            strict(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def load(name):
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as fh:
        return json.load(fh)


def materialize(case, root):
    """Write a synthetic case (tests/golden/audit_verify/synthetic.json) into root; return the audit dir."""
    directory = os.path.join(root, "audit")
    os.makedirs(directory)
    prev = None
    seq = 0
    for f in case.get("files", []):
        records = []
        for rec in f["records"]:
            rec = copy.deepcopy(rec)
            seq = rec.pop("seq", seq + 1)
            ts, event = rec.pop("ts"), rec.pop("event")
            records.append(audit_verify._ev(seq, ts, event, rec.pop("tool", None), rec.pop("class", None), **rec))
        if f["link"] == "genesis":
            data, prev = audit_verify.build_chain(records)
        elif f["link"] == "continue":
            data, prev = audit_verify.build_chain(records, prev_hash=prev)
        else:
            data, prev = audit_verify.build_chain(records, prev_hash="sha256:" + "0" * 64)
        mutate = case.get("mutate")
        if mutate and mutate["file"] == f["name"]:
            lines = data.split(b"\n")
            if "delete_line" in mutate:
                del lines[mutate["delete_line"] - 1]
            else:
                lines[mutate["line"] - 1] = mutate["raw"].encode("utf-8")
            data = b"\n".join(lines)
        with open(os.path.join(directory, f["name"]), "wb") as fh:
            fh.write(data)
    if case.get("blocked"):
        with open(os.path.join(directory, "blocked.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(b) + "\n" for b in case["blocked"]))
    if case.get("ledger"):
        with open(os.path.join(root, "ledger.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(b) + "\n" for b in case["ledger"]))
    if case.get("special") == "missing_dir":
        return os.path.join(root, "nope")
    return directory


class FixtureTests(unittest.TestCase):
    def test_eval_fixture_golden(self):
        for case in load("fixture.json")["cases"]:
            with self.subTest(case=case["name"]):
                inp = {"dir": os.path.join(ROOT, case["dir"])}
                if "window" in case:
                    inp["window"] = case["window"]
                out = audit_verify.run("run", inp)
                self.assertEqual(audit_verify.schema_errors(out, audit_verify.SCHEMAS["run"]["output"]), [])
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1, ensure_ascii=False)[:3000])

    def test_h12_tampered_line_breaks_the_next_line(self):
        with open(os.path.join(FIXTURE, "audit-2026-11.jsonl"), "rb") as fh:
            good = fh.read().split(b"\n")
        with open(os.path.join(FIXTURE, "tampered", "audit-2026-11.jsonl"), "rb") as fh:
            bad = fh.read().split(b"\n")
        diff = [i for i, (a, b) in enumerate(zip(good, bad)) if a != b]
        self.assertEqual(len(good), len(bad))
        self.assertEqual(len(diff), 1, "the tampered copy must differ in exactly one line")
        out = audit_verify.run("run", {"dir": os.path.join(FIXTURE, "tampered")})
        self.assertFalse(out["chain_ok"])
        self.assertEqual([b["line"] for b in out["breaks"]], [diff[0] + 2])
        self.assertIn("line %d" % (diff[0] + 1), out["breaks"][0]["detail"])
        intact = audit_verify.run("run", {"dir": FIXTURE})
        self.assertTrue(intact["chain_ok"])

    def test_committed_fixture_matches_the_generator(self):
        import make_audit_fixture

        good, bad, line = make_audit_fixture.build()
        with open(os.path.join(FIXTURE, "audit-2026-11.jsonl"), "rb") as fh:
            self.assertEqual(fh.read(), good, "run: python3 tools/make_audit_fixture.py")
        with open(os.path.join(FIXTURE, "tampered", "audit-2026-11.jsonl"), "rb") as fh:
            self.assertEqual(fh.read(), bad, "run: python3 tools/make_audit_fixture.py")
        self.assertEqual(line, 51)

    def test_fixture_lines_follow_the_d5_schema(self):
        keys = ["v", "seq", "ts", "prev", "session", "client", "event", "tool", "server", "class", "decision",
                "reason_code", "mode", "fingerprint", "ticket_id", "ref_id", "inputs", "result", "after_tool",
                "kit_version", "policy_sha"]
        with open(os.path.join(FIXTURE, "audit-2026-11.jsonl"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertTrue(lines[0].startswith('{"v":1,"seq":1,') and '"prev":"genesis:' in lines[0])
        for i, line in enumerate(lines, start=1):
            obj = json.loads(line)
            self.assertEqual(list(obj), keys, "line %d key order" % i)
            self.assertIn(obj["event"], ("post", "pre_block", "pre_ask", "session_start"))
            self.assertIn(obj["decision"], ("none", "ask", "deny"))
            self.assertRegex(obj["session"], r"^[0-9a-f]{12}$")
            account_values = [v for k, v in obj["inputs"].items() if "account" in k.lower()]
            for v in account_values:
                self.assertRegex(v, r"^•{4}[A-Z0-9]{4}$", "line %d: account numbers are masked" % i)


class SyntheticTests(unittest.TestCase):
    def test_synthetic_golden(self):
        doc = load("synthetic.json")
        self.assertGreaterEqual(len(doc["cases"]), 15)
        for case in doc["cases"]:
            with self.subTest(case=case["name"]):
                with tempfile.TemporaryDirectory() as tmp:
                    directory = materialize(case, tmp)
                    inp = {"dir": directory}
                    if case.get("window") is not None:
                        inp["window"] = case["window"]
                    out = audit_verify.run("run", inp)
                if isinstance(out.get("notes"), list):
                    out["notes"] = [n.replace(directory, "<dir>") for n in out["notes"]]
                self.assertEqual(audit_verify.schema_errors(out, audit_verify.SCHEMAS["run"]["output"]), [])
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1)[:3000])


class BehaviourTests(unittest.TestCase):
    def chain(self, records):
        data, _ = audit_verify.build_chain(records)
        return [("audit-2026-11.jsonl", data)]

    def test_injection_window_is_exactly_three_calls(self):
        base = [audit_verify._ev(1, "2026-11-10T15:00:00Z", "post", "get_sec_filing", "read")]
        fillers = ["get_equity_quotes", "get_portfolio", "get_accounts"]
        for gap in range(0, 4):
            recs = list(base)
            for j in range(gap):
                recs.append(audit_verify._ev(2 + j, "2026-11-10T15:00:%02dZ" % (j + 1), "post", fillers[j], "read"))
            recs.append(audit_verify._ev(10, "2026-11-10T15:01:00Z", "pre_block", "place_crypto_order", "money",
                                         decision="deny", reason_code="SIMULATE_ONLY", result={}))
            out = audit_verify.verify(self.chain(recs))
            with self.subTest(calls_between=gap):
                if gap < 3:
                    self.assertEqual([x["calls_after"] for x in out["possible_injection"]], [gap + 1])
                else:
                    self.assertEqual(out["possible_injection"], [])

    def test_each_untrusted_tool_counts(self):
        for tool in audit_verify.UNTRUSTED_TOOLS:
            recs = [audit_verify._ev(1, "2026-11-10T15:00:00Z", "post", tool, "read"),
                    audit_verify._ev(2, "2026-11-10T15:00:05Z", "pre_block", "exercise_option", "money",
                                     decision="deny", reason_code="SIMULATE_ONLY", result={})]
            with self.subTest(tool=tool):
                out = audit_verify.verify(self.chain(recs))
                self.assertEqual(out["possible_injection"][0]["after_tool"], tool)

    def test_reads_are_not_attempts(self):
        recs = [audit_verify._ev(1, "2026-11-10T15:00:00Z", "post", "get_equity_news", "read"),
                audit_verify._ev(2, "2026-11-10T15:00:05Z", "post", "review_equity_order", "simulate",
                                 fingerprint="sha256:x", result={"ok": True}),
                audit_verify._ev(3, "2026-11-10T15:00:06Z", "post", "create_alert", "write_confirm")]
        self.assertEqual(audit_verify.verify(self.chain(recs))["possible_injection"], [])

    def test_sessions_do_not_mix(self):
        recs = [audit_verify._ev(1, "2026-11-10T15:00:00Z", "post", "get_equity_news", "read", session="a"),
                audit_verify._ev(2, "2026-11-10T15:00:05Z", "pre_block", "place_equity_order", "money",
                                 session="b", decision="deny", reason_code="SIMULATE_ONLY", result={})]
        self.assertEqual(audit_verify.verify(self.chain(recs))["possible_injection"], [])

    def test_failed_review_is_not_counted_or_matched(self):
        recs = [audit_verify._ev(1, "2026-11-10T15:00:00Z", "post", "review_equity_order", "simulate",
                                 fingerprint="sha256:f", result={"ok": False, "error_head": "rejected"}),
                audit_verify._ev(2, "2026-11-10T15:00:30Z", "post", "place_equity_order", "money",
                                 fingerprint="sha256:f", result={"ok": True, "order_id": "o1"})]
        out = audit_verify.verify(self.chain(recs))
        self.assertEqual(out["reviews"], 0)
        self.assertFalse(out["places"][0]["matched_review"])

    def test_missing_final_newline_is_noted(self):
        data, _ = audit_verify.build_chain([audit_verify._ev(1, "2026-11-10T15:00:00Z", "session_start", None, None)])
        out = audit_verify.verify([("audit-2026-11.jsonl", data.rstrip(b"\n"))])
        self.assertTrue(out["chain_ok"])
        self.assertTrue(any("no newline" in n for n in out["notes"]))

    def test_prefixed_tool_names_are_reduced_to_bare_names(self):
        recs = [audit_verify._ev(1, "2026-11-10T15:00:00Z", "post", "mcp__robinhood-trading__get_equity_news", "read"),
                audit_verify._ev(2, "2026-11-10T15:00:05Z", "pre_block", "mcp__rh-sandbox__place_equity_order", "money",
                                 decision="deny", reason_code="SIMULATE_ONLY", result={})]
        out = audit_verify.verify(self.chain(recs))
        self.assertEqual(out["blocked"][0]["tool"], "place_equity_order")
        self.assertEqual(out["possible_injection"][0]["after_tool"], "get_equity_news")

    def test_et_display_across_dst(self):
        self.assertEqual(audit_verify.fmt_et(audit_verify.parse_ts("2026-11-01T05:30:00Z")), "2026-11-01 01:30 ET")
        self.assertEqual(audit_verify.fmt_et(audit_verify.parse_ts("2026-11-01T06:30:00Z")), "2026-11-01 01:30 ET")
        self.assertEqual(audit_verify.fmt_et(audit_verify.parse_ts("2027-03-14T07:30:00Z")), "2027-03-14 03:30 ET")
        start, end = audit_verify.parse_window({"start_date": "2026-10-31", "end_date": "2026-11-01"})
        self.assertEqual((audit_verify.fmt_utc(start), audit_verify.fmt_utc(end)),
                         ("2026-10-31T04:00:00Z", "2026-11-02T05:00:00Z"))

    def test_output_never_carries_performance_wording(self):
        out = audit_verify.run("run", {"dir": FIXTURE})
        self.assertIsNone(re.search(r"\b(beat|beats|outperform\w*|underperform\w*|alpha)\b", json.dumps(out), re.I))


AUDIT_LOG = os.path.join(ROOT, "hooks", "audit_log.py")


@unittest.skipUnless(os.path.isfile(AUDIT_LOG), "hooks/audit_log.py (WP-J) not present")
class WriterInteropTests(unittest.TestCase):
    """Drive the real writer (hooks/audit_log.py) and check that audit_verify reads what it wrote."""

    def post(self, state, session, tool, tool_input, data, extra_env=None):
        event = {"session_id": session, "hook_event_name": "PostToolUse", "cwd": state,
                 "tool_name": "mcp__robinhood-trading__" + tool, "tool_input": tool_input,
                 "tool_response": [{"type": "text", "text": json.dumps({"data": data, "guide": "g"})}]}
        env = dict(os.environ, ROBINHOOD_SKILLS_STATE=state, CLAUDE_PLUGIN_OPTION_ORDER_MODE="confirm")
        env.update(extra_env or {})
        subprocess.run([sys.executable, AUDIT_LOG], input=json.dumps(event), text=True, env=env, timeout=60,
                       check=True, capture_output=True)

    def test_chain_reviews_places_and_folded_blocks(self):
        with tempfile.TemporaryDirectory() as state:
            order = {"account_number": "ACCT-9-X4F1", "symbol": "PLTR", "side": "buy", "type": "limit",
                     "quantity": "10", "limit_price": "29.50", "time_in_force": "gfd", "market_hours": "regular_hours"}
            quote = {"bid_price": "29.45", "ask_price": "29.47", "updated_at": "2026-11-10T15:00:00Z"}
            self.post(state, "sess-a", "get_equity_news", {"symbol": "PLTR"}, {"results": [{"title": "t"}]})
            self.post(state, "sess-a", "review_equity_order", order,
                      {"order_checks": {}, "quote_data": quote, "market_data_disclosure": "d"})
            self.post(state, "sess-a", "place_equity_order", dict(order, ref_id=str(uuid.uuid4())),
                      {"id": "ord-1", "state": "queued"})
            # a guard block written the way guard.sh writes it, folded on the next hook run
            blocked = {"v": 1, "ts": "2026-11-10T15:00:05Z", "event": "pre_block", "tool": "place_crypto_order",
                       "server": "robinhood-trading", "reason_code": "SIMULATE_ONLY", "session_id": "sess-a",
                       "after_tool": "get_equity_news", "mode": "confirm"}
            with open(os.path.join(state, "audit", "blocked.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps(blocked) + "\n")
            self.post(state, "sess-a", "get_portfolio", {"account_number": "ACCT-9-X4F1"}, {"total_value": "1"})
            out = audit_verify.run("run", {"dir": os.path.join(state, "audit")})
        self.assertTrue(out["available"])
        self.assertTrue(out["chain_ok"], out["breaks"])
        self.assertEqual(out["lines"], 5)
        self.assertEqual(out["reviews"], 1)
        self.assertEqual(len(out["places"]), 1)
        place = out["places"][0]
        self.assertEqual((place["order_id"], place["matched_review"]), ("ord-1", True))
        self.assertEqual(place["review_quote"]["ask"], "29.47")
        self.assertEqual([b["tool"] for b in out["blocked"]], ["place_crypto_order"])
        self.assertTrue(out["blocked"][0]["folded"])
        # the place sent two calls after the news read is flagged too: the rule is positional, a prompt for a human
        flagged = sorted((p["tool"], p["outcome"], p["after_tool"]) for p in out["possible_injection"])
        self.assertEqual(flagged, [("place_crypto_order", "blocked", "get_equity_news"),
                                   ("place_equity_order", "sent", "get_equity_news")])
        self.assertNotIn("ACCT-9", json.dumps(out))


class ContractTests(unittest.TestCase):
    def cli(self, args, stdin=""):
        return subprocess.run([sys.executable, SCRIPT] + args, input=stdin, capture_output=True, text=True,
                              timeout=60)

    def test_selftest_and_schema(self):
        proc = self.cli(["--selftest"])
        self.assertEqual(proc.returncode, 0, proc.stdout)
        schema = json.loads(self.cli(["--schema"]).stdout)
        self.assertEqual(schema["script"], NAME)
        self.assertIn("input", schema["ops"]["run"])
        self.assertIn("output", schema["ops"]["run"])

    def test_cli_errors_are_json_with_exit_0(self):
        for args, stdin, code in (([], "", "MISSING_OP"), (["run"], "{bad", "BAD_JSON"),
                                  (["nope"], "{}", "UNKNOWN_OP"), (["run"], "[]", "BAD_INPUT")):
            proc = self.cli(args, stdin)
            self.assertEqual(proc.returncode, 0)
            out = json.loads(proc.stdout)
            self.assertFalse(out["ok"])
            self.assertEqual(out["errors"][0]["code"], code)

    def test_cli_run_on_fixture(self):
        proc = self.cli(["run"], json.dumps({"dir": FIXTURE}))
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(json.loads(proc.stdout)["chain_ok"])

    def test_no_network_imports_and_no_file_writes(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            self.assertFalse(set(names) & FORBIDDEN_IMPORTS, names)
        self.assertIsNone(re.search(r"open\([^)]*['\"][wax]b?['\"]", source))
        self.assertNotIn("os.remove", source)


if __name__ == "__main__":
    unittest.main()
