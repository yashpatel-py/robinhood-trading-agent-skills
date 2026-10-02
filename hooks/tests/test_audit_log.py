"""Tests for hooks/audit_log.py (spec E.5 H10, H11, H13 and the rest of D.5), plus structure
checks for hooks/hooks.json and the integrations/ snippets.

Unofficial; not affiliated with Robinhood Markets, Inc.

Run from the repository root:
    python3 -m unittest hooks/tests/test_audit_log.py -v

Every test runs audit_log.py as a subprocess (as the hook does) against a temporary state
directory. Account numbers are synthetic and assembled at run time, and the event files hold
__ACCT__ / __RHS__ / __RHC__ / __BEARER__ placeholders, so no account-like literal is committed.
"""

import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True  # loading audit_log.py and canon.py must not litter __pycache__
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
AUDIT = os.path.join(REPO, "hooks", "audit_log.py")
GUARD = os.path.join(REPO, "hooks", "guard.sh")
EVENTS = os.path.join(HERE, "events")
MASK = "\u2022\u2022\u2022\u2022"

# Synthetic identifiers, assembled so no committed line holds an account-like literal.
ACCT = "".join(["5QX", "81", "X4F1"])
RHS = "".join(["5123", "45678"])
RHC = "".join(["7C", "9Z", "Q2W8"])
BEARER = "".join(["Bear", "er ", "abc.def.ghi"])
JWT = "".join(["ey", "J", "abcdefghij", ".", "ey", "J", "klmnopqrst", ".sig"])
ALLOW_TEXT = '"' + "al" + "low" + '"'


def load_shared(name):
    for folder in (os.path.join(REPO, "hooks", "lib"), os.path.join(REPO, "shared", "scripts")):
        path = os.path.join(folder, name + ".py")
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("test_" + name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def event(name):
    with open(os.path.join(EVENTS, name), "r", encoding="utf-8") as handle:
        text = handle.read()
    for key, value in (("__ACCT__", ACCT), ("__RHS__", RHS), ("__RHC__", RHC), ("__BEARER__", BEARER)):
        text = text.replace(key, value)
    return json.loads(text)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class AuditCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rh-audit-test-")
        self.state = os.path.join(self.tmp, "state")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)

    def tearDown(self):
        for root, dirs, _files in os.walk(self.tmp):
            for name in dirs:
                try:
                    os.chmod(os.path.join(root, name), 0o700)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, **extra):
        env = dict((k, v) for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_", "ROBINHOOD_SKILLS_", "XDG_")))
        env.update({"ROBINHOOD_SKILLS_STATE": self.state, "HOME": self.home,
                    "XDG_CONFIG_HOME": os.path.join(self.home, ".config")})
        env.update(extra)
        return env

    def run_audit(self, payload, args=(), **env):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        started = time.monotonic()
        proc = subprocess.run([sys.executable, AUDIT] + list(args), input=data, env=self.env(**env),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.elapsed = time.monotonic() - started
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"", "audit_log.py must print nothing")
        self.assertEqual(proc.stderr, b"", "audit_log.py must never raise")
        return proc

    def audit_dir(self):
        return os.path.join(self.state, "audit")

    def audit_paths(self):
        folder = self.audit_dir()
        if not os.path.isdir(folder):
            return []
        return [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if re.match(r"^audit-\d{4}-\d{2}\.jsonl$", n)]

    def raw_lines(self):
        out = []
        for path in self.audit_paths():
            with open(path, "rb") as handle:
                out.extend(line for line in handle.read().split(b"\n") if line)
        return out

    def lines(self):
        return [json.loads(line.decode("utf-8")) for line in self.raw_lines()]

    def all_state_bytes(self):
        blob = b""
        for root, _dirs, files in os.walk(self.state):
            for name in files:
                with open(os.path.join(root, name), "rb") as handle:
                    blob += handle.read()
        return blob

    def assert_chain(self, raw):
        self.assertTrue(raw, "no audit lines")
        previous = None
        for index, line in enumerate(raw):
            rec = json.loads(line.decode("utf-8"))
            if previous is None:
                self.assertTrue(rec["prev"].startswith(("genesis:", "sha256:")), rec["prev"])
            else:
                self.assertEqual(rec["prev"], "sha256:" + sha(previous), "chain broken at line %d" % (index + 1))
                self.assertEqual(rec["seq"], json.loads(previous.decode("utf-8"))["seq"] + 1)
            previous = line

    def learn_accounts(self):
        self.run_audit(event("post_get_accounts.json"))


class LineSchemaTest(AuditCase):
    KEYS = ["v", "seq", "ts", "prev", "session", "client", "event", "tool", "server", "class", "decision",
            "reason_code", "mode", "fingerprint", "ticket_id", "ref_id", "inputs", "result", "after_tool",
            "kit_version", "policy_sha"]

    def test_h10_masked_line_and_chain(self):
        self.learn_accounts()
        self.run_audit(event("post_review_equity_order.json"))
        self.run_audit(event("post_update_watchlist_freetext.json"))
        self.run_audit(event("post_get_realized_pnl_error.json"))
        raw = self.raw_lines()
        self.assertEqual(len(raw), 4)
        self.assert_chain(raw)
        first = json.loads(raw[0].decode("utf-8"))
        self.assertRegex(first["prev"], r"^genesis:[0-9a-f]{16}$")
        self.assertEqual(first["seq"], 1)
        for rec in self.lines():
            self.assertEqual(list(rec.keys()), self.KEYS)
            self.assertEqual(rec["client"], "claude-code")
            self.assertRegex(rec["session"], r"^[0-9a-f]{12}$")
            self.assertRegex(rec["server"], r"^[0-9a-f]{8}$")
            self.assertRegex(rec["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
            self.assertEqual(rec["mode"], "simulate_only")
            self.assertEqual(rec["kit_version"], "2.0.0")
        blob = self.all_state_bytes()
        for secret in (ACCT, RHS, RHC, "test-session-1"):
            self.assertNotIn(secret.encode("utf-8"), blob, "%s leaked into state files" % secret)
        # servers.txt keeps raw server names by design (the guard reads it); audit lines hash them.
        self.assertNotIn(b"rh-uuid-server", b"".join(self.raw_lines()))
        review, watch, pnl = self.lines()[1:]
        self.assertEqual(review["inputs"]["account_number"], MASK + ACCT[-4:])
        self.assertEqual(watch["inputs"]["display_description"], "ideas for account %s this week" % (MASK + ACCT[-4:]))
        self.assertEqual(pnl["inputs"]["account_number"], MASK + RHS[-4:])
        self.assertEqual(pnl["result"]["ok"], False)
        self.assertIn(MASK + RHS[-4:], pnl["result"]["error_head"])

    def test_known_account_masked_in_free_text_after_get_accounts(self):
        self.learn_accounts()
        with open(os.path.join(self.state, "known_accounts.json")) as handle:
            known = json.load(handle)
        self.assertEqual(len(known["hashes"]), 3)
        self.run_audit(event("post_update_watchlist_freetext.json"))
        self.assertNotIn(ACCT.encode(), self.all_state_bytes())

    def test_read_result_is_a_count_only(self):
        self.run_audit(event("post_get_equity_quotes.json"))
        rec = self.lines()[0]
        self.assertEqual(rec["class"], "read")
        self.assertEqual(rec["result"], {"ok": True, "count": 2})
        self.assertNotIn(b"31.25", b"".join(self.raw_lines()), "quote values must not be logged for reads")
        self.assertEqual(rec["inputs"], {"symbols": ["PLTR", "AAPL"]})

    def test_session_start_line(self):
        self.run_audit(event("session_start.json"), args=["--session-start"])
        rec = self.lines()[0]
        self.assertEqual(rec["event"], "session_start")
        self.assertEqual(rec["decision"], "none")
        self.assertIsNone(rec["tool"])
        self.assertEqual(rec["result"], {"ok": True, "source": "startup"})

    def test_after_tool_and_last_tool(self):
        self.run_audit(event("post_get_equity_quotes.json"))
        self.run_audit(event("post_review_equity_order.json"))
        first, second = self.lines()
        self.assertIsNone(first["after_tool"])
        self.assertEqual(second["after_tool"], "get_equity_quotes")
        with open(os.path.join(self.state, "last_tool", "latest")) as handle:
            self.assertEqual(handle.read().strip(), "review_equity_order")
        with open(os.path.join(self.state, "last_tool", second["session"])) as handle:
            self.assertEqual(handle.read().strip(), "review_equity_order")

    def test_confirm_mode_is_recorded(self):
        self.run_audit(event("post_get_equity_quotes.json"), CLAUDE_PLUGIN_OPTION_ORDER_MODE="confirm")
        self.assertEqual(self.lines()[0]["mode"], "confirm")

    def test_policy_sha_tracks_the_config_file(self):
        config = os.path.join(self.home, ".config", "robinhood-skills", "config.toml")
        os.makedirs(os.path.dirname(config))
        with open(config, "wb") as handle:
            handle.write(b"[policy]\nmax_order_usd = \"UNSET\"\n")
        self.run_audit(event("post_get_equity_quotes.json"))
        with open(config, "rb") as handle:
            expected = "sha256:" + sha(handle.read())
        self.assertEqual(self.lines()[0]["policy_sha"], expected)


class MaskingTest(AuditCase):
    def test_bearer_dropped_and_long_text_hashed(self):
        self.learn_accounts()  # update_watchlist is a generic name: it is logged once the server is known
        self.run_audit(event("post_update_watchlist_secrets.json"))
        rec = self.lines()[-1]
        self.assertEqual(rec["tool"], "update_watchlist")
        self.assertEqual(rec["inputs"]["display_name"], "[dropped: credential]")
        self.assertEqual(rec["inputs"]["display_description"], {"len": 250, "sha256": sha(b"x" * 250)})
        self.assertNotIn(b"abc.def.ghi", self.all_state_bytes())

    def test_masker_rules(self):
        module = _load_audit_module()
        masker = module.Masker(b"0" * 16, set())
        masker.known = {masker.salted(ACCT), masker.salted(RHS)}
        out = masker.value({
            "account_number": ACCT, "rhs_account_number": int(RHS), "brokerage_account_type": "individual",
            "note": "acct %s, rhs %s" % (ACCT, RHS), "n": int(RHS), "flag": True, "items": [ACCT],
            "token": BEARER, "jwt": JWT,
        })
        self.assertEqual(out["account_number"], MASK + ACCT[-4:])
        self.assertEqual(out["rhs_account_number"], MASK + RHS[-4:])
        self.assertEqual(out["brokerage_account_type"], "individual")
        self.assertEqual(out["note"], "acct %s, rhs %s" % (MASK + ACCT[-4:], MASK + RHS[-4:]))
        self.assertEqual(out["n"], MASK + RHS[-4:])
        self.assertIs(out["flag"], True)
        self.assertEqual(out["items"], [MASK + ACCT[-4:]])
        self.assertEqual(out["token"], "[dropped: credential]")
        self.assertEqual(out["jwt"], "[dropped: credential]")
        self.assertEqual(list(out), sorted(out))

    def test_error_head_masks_unknown_account_like_tokens(self):
        # Before get_accounts has run, an error can still quote an account number.
        self.run_audit(event("post_get_realized_pnl_error.json"))
        rec = self.lines()[0]
        self.assertNotIn(RHS, json.dumps(rec, ensure_ascii=False))
        self.assertLessEqual(len(rec["result"]["error_head"]), 200)


class FilterTest(AuditCase):
    def test_unrelated_server_is_not_logged(self):
        self.run_audit(event("post_unrelated_search.json"))
        self.assertEqual(self.audit_paths(), [])

    def test_unknown_tool_on_an_unlearned_server_is_not_logged(self):
        self.run_audit(event("post_unknown_tool.json"))
        self.assertEqual(self.audit_paths(), [])

    def test_unknown_tool_on_a_learned_server_is_logged(self):
        os.makedirs(self.state)
        with open(os.path.join(self.state, "servers.txt"), "w") as handle:
            handle.write("rh-uuid-server\n")
        self.run_audit(event("post_unknown_tool.json"))
        rec = self.lines()[0]
        self.assertEqual((rec["tool"], rec["class"]), ("get_new_thing", "unknown"))
        self.assertEqual(rec["result"], {"ok": True})

    def test_generic_name_on_a_robinhood_named_server_is_logged(self):
        self.run_audit(event("post_generic_on_robinhood_named.json"))
        rec = self.lines()[0]
        self.assertEqual((rec["tool"], rec["class"], rec["result"]["count"]), ("get_alerts", "read", 2))

    def test_get_accounts_shape_proves_the_server(self):
        self.learn_accounts()
        self.assertEqual(self.lines()[0]["tool"], "get_accounts")
        with open(os.path.join(self.state, "servers.txt")) as handle:
            self.assertEqual(handle.read(), "rh-uuid-server\n")

    def test_disabled(self):
        self.run_audit(event("post_get_equity_quotes.json"), CLAUDE_PLUGIN_OPTION_AUDIT_LOG="false")
        self.assertFalse(os.path.exists(self.state))


class LedgerTest(AuditCase):
    def ledger(self):
        path = os.path.join(self.state, "ledger.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_review_writes_a_bindable_ledger_entry(self):
        payload = event("post_review_equity_order.json")
        self.run_audit(payload)
        entries = self.ledger()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        canon = load_shared("canon")
        self.assertIsNotNone(canon, "canon.py not found")
        fp = canon.fingerprint("review_equity_order", payload["tool_input"])
        self.assertEqual(entry["fingerprint"], fp["fingerprint"])
        self.assertEqual(entry["ticket_id"], fp["ticket_id"])
        # The place twin with a ref_id and the review's spelling hash the same (canon parity).
        place = dict(payload["tool_input"], ref_id="ref-test-ok-1")
        self.assertEqual(canon.fingerprint("place_equity_order", place)["fingerprint"], entry["fingerprint"])
        self.assertEqual(list(entry)[:6], ["fingerprint", "session", "ts", "tool", "review_ok", "alerts_count"])
        self.assertEqual(entry["session"], self.lines()[0]["session"])
        # The server that answered, as the chain line's salted hash (never the raw name), so the
        # confirm gate can bind a place call only to a review from the same server.
        self.assertRegex(entry["server"], r"^[0-9a-f]{8}$")
        self.assertEqual(entry["server"], self.lines()[0]["server"])
        server = payload["tool_name"].split("__")[1]
        self.assertNotIn(server.encode("utf-8"), self.all_state_bytes())
        self.assertIs(entry["review_ok"], True)
        self.assertEqual(entry["alerts_count"], 0)
        self.assertEqual(entry["quote"], {"bid": "31.20", "ask": "31.25", "ts": "2026-09-22T20:00:00Z"})
        self.assertEqual(entry["estimated_cost"], "312.40")
        self.assertIsNone(entry["consumed_by"])
        self.assertEqual(entry["canonical"]["account_number"], MASK + ACCT[-4:])
        rec = self.lines()[0]
        self.assertEqual(rec["fingerprint"], fp["fingerprint"])
        self.assertEqual(rec["result"]["estimated_cost"], "312.40")
        mode = stat.S_IMODE(os.stat(os.path.join(self.state, "ledger.jsonl")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_review_alerts_are_counted_and_masked(self):
        self.run_audit(event("post_review_equity_order_alert.json"))
        entry = self.ledger()[0]
        self.assertEqual(entry["alerts_count"], 1)
        self.assertIs(entry["review_ok"], True)
        head = self.lines()[0]["result"]["alerts_head"][0]
        self.assertTrue(head.startswith("EQUITY_NOT_ENOUGH_BP: Not enough buying power"), head)
        self.assertLessEqual(len(head), 120)
        self.assertNotIn(ACCT, head)

    def test_validation_errors_make_review_not_ok(self):
        self.run_audit(event("post_preview_crypto_order_invalid.json"))
        entry = self.ledger()[0]
        self.assertIs(entry["review_ok"], False)
        self.assertEqual(entry["alerts_count"], 1)
        self.assertEqual(self.lines()[0]["inputs"]["rhs_account_number"], MASK + RHS[-4:])

    def test_failed_review_writes_no_ledger_entry(self):
        self.run_audit(event("post_review_equity_order_error.json"))
        self.assertEqual(self.ledger(), [])
        self.assertEqual(self.lines()[0]["result"]["ok"], False)

    def test_place_updates_refids(self):
        self.run_audit(event("post_place_equity_order.json"))
        self.run_audit(event("post_place_equity_order_error.json"))
        with open(os.path.join(self.state, "refids.json")) as handle:
            refids = json.load(handle)
        ok = refids["ref-test-ok-1"]
        bad = refids["ref-test-bad-2"]
        self.assertEqual(ok["status"], "succeeded")
        self.assertEqual(bad["status"], "failed")
        self.assertTrue(ok["fingerprint"].startswith("sha256:"))
        placed, failed = self.lines()
        self.assertEqual(placed["class"], "money")
        self.assertEqual(placed["result"], {"ok": True, "order_id": "order-test-placed-1", "state": "queued"})
        self.assertEqual(placed["ref_id"], "ref-test-ok-1")
        self.assertEqual(failed["result"]["ok"], False)

    def test_ref_id_is_recorded_exactly_as_sent(self):
        payload = event("post_place_equity_order.json")
        for bad in ("ref-test-ok-1\n", " ref-test-ok-1", "ref-test-ok-1 "):
            payload["tool_input"]["ref_id"] = bad
            self.run_audit(payload)
        self.assertFalse(os.path.exists(os.path.join(self.state, "refids.json")))
        self.assertEqual([r["ref_id"] for r in self.lines()], [None, None, None])

    def test_place_keeps_the_gate_fingerprint(self):
        os.makedirs(self.state)
        ref = "ref-test-ok-1"
        with open(os.path.join(self.state, "refids.json"), "w") as handle:
            json.dump({ref: {"fingerprint": "sha256:gate", "status": "pending", "ts": "2026-09-22T20:00:00.000Z"}}, handle)
        self.run_audit(event("post_place_equity_order.json"))
        with open(os.path.join(self.state, "refids.json")) as handle:
            entry = json.load(handle)[ref]
        self.assertEqual((entry["fingerprint"], entry["status"]), ("sha256:gate", "succeeded"))


class FoldTest(AuditCase):
    def write_blocked(self, lines):
        os.makedirs(self.audit_dir(), exist_ok=True)
        with open(os.path.join(self.audit_dir(), "blocked.jsonl"), "w") as handle:
            for line in lines:
                handle.write(line + "\n")

    def test_h11_blocked_lines_folded_and_session_hashed(self):
        self.write_blocked([
            '{"v":1,"ts":"2026-09-22T20:01:02Z","event":"pre_block","tool":"place_equity_order","server":"robinhood-trading","reason_code":"SIMULATE_ONLY","session_id":"test-session-1","after_tool":"review_equity_order","mode":"simulate_only"}',
            "not json at all",
            '{"v":1,"ts":"2026-09-22T20:01:03Z","event":"pre_ask","tool":"cancel_advanced_order","server":"robinhood-trading","reason_code":null,"session_id":"test-session-1","after_tool":"","mode":"simulate_only"}',
            '{"v":1,"ts":"bad","event":"pre_block","tool":"x\\"y","server":"s","reason_code":"lower","session_id":"s"}',
        ])
        self.run_audit(event("post_get_equity_quotes.json"))
        recs = self.lines()
        self.assertEqual([r["event"] for r in recs], ["pre_block", "pre_ask", "pre_block", "post"])
        block, ask, odd, _post = recs
        self.assertEqual((block["tool"], block["decision"], block["reason_code"], block["class"]),
                         ("place_equity_order", "deny", "SIMULATE_ONLY", "money"))
        self.assertEqual(block["ts"], "2026-09-22T20:01:02Z")
        self.assertEqual(block["after_tool"], "review_equity_order")
        self.assertEqual((ask["decision"], ask["reason_code"], ask["class"]), ("ask", None, "cancel"))
        self.assertIsNone(odd["tool"])
        self.assertIsNone(odd["reason_code"])
        self.assertRegex(block["session"], r"^[0-9a-f]{12}$")
        self.assertEqual(block["session"], recs[3]["session"])
        self.assertNotIn(b"test-session-1", b"".join(self.raw_lines()))
        self.assertFalse(os.path.exists(os.path.join(self.audit_dir(), "blocked.jsonl")))
        self.assertEqual([n for n in os.listdir(self.audit_dir()) if n.startswith("blocked")], [])
        self.assert_chain(self.raw_lines())

    def test_fold_happens_even_when_the_call_is_not_logged(self):
        self.write_blocked(['{"v":1,"ts":"2026-09-22T20:01:02Z","event":"pre_block","tool":"exercise_option","server":"robinhood-trading","reason_code":"SIMULATE_ONLY","session_id":"s","after_tool":"","mode":"simulate_only"}'])
        self.run_audit(event("post_unrelated_search.json"))
        self.assertEqual([r["tool"] for r in self.lines()], ["exercise_option"])

    def test_guard_to_audit_pipeline(self):
        env = self.env(CLAUDE_PLUGIN_ROOT=REPO)
        with open(os.path.join(EVENTS, "pre_place_equity_order.json"), "rb") as handle:
            proc = subprocess.run(["sh", GUARD, "money"], input=handle.read(), env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertIn(b"Nothing was placed", proc.stderr)
        # The guard prints its verdict first and writes blocked.jsonl from a detached child.
        blocked = os.path.join(self.audit_dir(), "blocked.jsonl")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (os.path.isfile(blocked) and os.path.getsize(blocked)):
            time.sleep(0.05)
        self.run_audit(event("session_start.json"), args=["--session-start"])
        recs = self.lines()
        self.assertEqual([r["event"] for r in recs], ["pre_block", "session_start"])
        self.assertEqual(recs[0]["session"], recs[1]["session"])


class ChainFileTest(AuditCase):
    def current_name(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        return "audit-%04d-%02d.jsonl" % (now.year, now.month)

    def test_new_month_chains_to_the_previous_file(self):
        os.makedirs(self.audit_dir())
        old_line = ('{"v":1,"seq":41,"ts":"2000-01-31T23:59:59.000Z","prev":"genesis:%s","event":"post"}' % ("0" * 16)).encode("ascii")
        with open(os.path.join(self.audit_dir(), "audit-2000-01.jsonl"), "wb") as handle:
            handle.write(old_line + b"\n")
        self.run_audit(event("post_get_equity_quotes.json"))
        with open(os.path.join(self.audit_dir(), self.current_name()), "rb") as handle:
            first = json.loads(handle.readline().decode("utf-8"))
        self.assertEqual(first["prev"], "sha256:" + sha(old_line))
        self.assertEqual(first["seq"], 42)

    def test_partial_last_line_is_terminated(self):
        os.makedirs(self.audit_dir())
        path = os.path.join(self.audit_dir(), self.current_name())
        with open(path, "wb") as handle:
            handle.write(b'{"v":1,"seq":1,"partial')
        self.run_audit(event("post_get_equity_quotes.json"))
        with open(path, "rb") as handle:
            content = handle.read().split(b"\n")
        self.assertEqual(content[0], b'{"v":1,"seq":1,"partial')
        self.assertEqual(json.loads(content[1].decode("utf-8"))["prev"], "sha256:" + sha(b'{"v":1,"seq":1,"partial'))

    def test_retention_prunes_the_oldest_file(self):
        self.run_audit(event("post_get_equity_quotes.json"))
        old = os.path.join(self.audit_dir(), "audit-2000-01.jsonl")
        with open(old, "wb") as handle:
            handle.write(b"{}\n")
        os.truncate(old, 51 * 1024 * 1024)  # sparse on most filesystems
        self.run_audit(event("post_get_equity_quotes.json"))
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(os.path.join(self.audit_dir(), self.current_name())))

    def test_file_modes(self):
        self.run_audit(event("post_get_equity_quotes.json"))
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.audit_dir()).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.audit_paths()[0]).st_mode), 0o600)
        salt = os.path.join(self.state, "salt")
        self.assertEqual(stat.S_IMODE(os.stat(salt).st_mode), 0o600)
        self.assertEqual(os.path.getsize(salt), 16)


class LockTest(AuditCase):
    def test_busy_lock_skips_the_line(self):
        os.makedirs(self.state)
        lock = os.path.join(self.state, ".lock")
        with open(lock, "w") as handle:
            handle.write("12345")
        self.run_audit(event("post_get_equity_quotes.json"))
        self.assertEqual(self.audit_paths(), [])
        self.assertLess(self.elapsed, 10)
        self.assertTrue(os.path.exists(lock), "a live lock belongs to someone else")

    def test_stale_lock_is_recovered(self):
        os.makedirs(self.state)
        lock = os.path.join(self.state, ".lock")
        with open(lock, "w") as handle:
            handle.write("12345")
        old = time.time() - 600
        os.utime(lock, (old, old))
        self.run_audit(event("post_get_equity_quotes.json"))
        self.assertEqual(len(self.lines()), 1)
        self.assertFalse(os.path.exists(lock))


@unittest.skipUnless(hasattr(os, "mkfifo"), "needs mkfifo")
class TamperedStateTest(AuditCase):
    """A FIFO planted where a state file belongs blocks any open() of it, and a symlink could point
    anywhere. audit_log.py must finish promptly (a hook killed at its timeout loses its line, and the
    SessionStart hook its order-mode line) and never write through a symlink."""

    def plant(self, name):
        path = os.path.join(self.state, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.mkfifo(path)

    def test_fifos_in_the_state_dir_never_hang_the_hook(self):
        month = datetime.datetime.now(datetime.timezone.utc).strftime("audit-%Y-%m.jsonl")
        for name in ("servers.txt", "salt", "known_accounts.json", "ledger.jsonl", "refids.json",
                     "audit/blocked.jsonl", "last_tool/latest", os.path.join("audit", month)):
            with self.subTest(name=name):
                shutil.rmtree(self.state, ignore_errors=True)
                self.plant(name)
                for payload in (event("post_get_accounts.json"), event("post_review_equity_order.json"),
                                event("post_place_equity_order.json")):
                    self.run_audit(payload)
                    self.assertLess(self.elapsed, 5.0, name)
                self.run_audit(event("session_start.json"), args=["--session-start"])
                self.assertLess(self.elapsed, 5.0, name)

    def test_a_symlinked_state_file_is_never_written_through(self):
        os.makedirs(self.state)
        victim = os.path.join(self.tmp, "victim")
        with open(victim, "wb"):
            pass
        for name in ("ledger.jsonl", "servers.txt"):
            os.symlink(victim, os.path.join(self.state, name))
        self.run_audit(event("post_review_equity_order.json"))
        payload = event("post_get_accounts.json")
        payload["tool_name"] = "mcp__uuid-like-server__get_accounts"
        self.run_audit(payload)
        self.assertEqual(os.path.getsize(victim), 0)


class NeverRaisesTest(AuditCase):
    def test_garbage_inputs(self):
        for payload in (b"", b"not json", b"[]", b"42", b'{"tool_name": 7}', b"\xff\xfe\x00"):
            self.run_audit(payload)
        odd = event("post_get_equity_quotes.json")
        for response in (None, 12, [1, 2], {"content": "x"}, {"structuredContent": []}, "plain words"):
            odd["tool_response"] = response
            self.run_audit(odd)
        odd["tool_input"] = "not a dict"
        self.run_audit(odd)

    def test_h13_state_dir_missing_or_unwritable(self):
        self.run_audit(event("post_get_equity_quotes.json"))  # missing: created
        self.assertTrue(self.audit_paths())
        if os.geteuid() == 0:
            self.skipTest("root can write anywhere")
        readonly = os.path.join(self.tmp, "readonly")
        os.makedirs(readonly)
        os.chmod(readonly, 0o500)
        self.state = os.path.join(readonly, "state")
        self.run_audit(event("post_get_equity_quotes.json"))
        blocker = os.path.join(self.tmp, "a-file")
        with open(blocker, "w") as handle:
            handle.write("x")
        self.state = os.path.join(blocker, "state")
        self.run_audit(event("post_get_equity_quotes.json"))


class HookConfigTest(unittest.TestCase):
    """hooks/hooks.json is spec D.4.1 plus the confirm-mode config matcher (overrides O3)."""

    def load(self, *parts):
        with open(os.path.join(REPO, *parts), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def money_tools(self):
        classes = self.load("connector", "tool-classes.json")
        return sorted(t["name"] for t in classes["tools"] if t["class"] == "money")

    def test_hooks_json_exact(self):
        cmd = 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh" '
        hook = lambda mode, timeout: [{"type": "command", "command": cmd + mode, "timeout": timeout}]
        expected = {
            "SessionStart": [{"hooks": hook("session", 5)}],
            "PreToolUse": [
                {"matcher": "^mcp__.+__(place|exercise|replace)[_-][A-Za-z0-9_-]+$", "hooks": hook("money", 15)},
                {"matcher": "^mcp__.*[Rr]obinhood.*__(submit|execute|transfer|withdraw|deposit|stake|unstake|convert|send|buy|sell|trade|liquidate|lend|borrow)[_-][A-Za-z0-9_-]+$",
                 "hooks": hook("money", 15)},
                {"matcher": "^mcp__.+__(cancel[_-][A-Za-z0-9_-]+|delete_alert)$", "hooks": hook("cancel", 10)},
                {"matcher": "^mcp__.+__[A-Za-z0-9_-]+$", "hooks": hook("classify", 10)},
                {"matcher": "^(Write|Edit|MultiEdit|Bash)$", "hooks": hook("config", 5)},
            ],
            "PostToolUse": [{"matcher": "^mcp__.+__[A-Za-z0-9_-]+$", "hooks": hook("audit", 10)}],
        }
        data = self.load("hooks", "hooks.json")
        self.assertEqual(data["hooks"], expected)
        self.assertIn("No hook in this kit ever returns permissionDecision 'allow'", data["description"])

    def test_hyphenated_tool_names_are_covered(self):
        # Claude Code keeps "-" in MCP tool names (it replaces other characters with "_"), so every
        # MCP matcher must accept it, or a hyphenated tool gets no prompt, no block and no audit line.
        data = self.load("hooks", "hooks.json")["hooks"]
        pre = [re.compile(e["matcher"]) for e in data["PreToolUse"]]
        post = re.compile(data["PostToolUse"][0]["matcher"])
        money1, money2, cancel, classify = pre[0], pre[1], pre[2], pre[3]
        self.assertTrue(money1.search("mcp__x__place-equity-order"))
        self.assertTrue(money2.search("mcp__robinhood-trading__transfer-funds"))
        self.assertTrue(cancel.search("mcp__x__cancel-order"))
        for name in ("mcp__x__some-tool", "mcp__00000000-0000-4000-8000-000000000000__transfer-funds"):
            self.assertTrue(classify.search(name), name)
            self.assertTrue(post.search(name), name)
        self.assertFalse(money1.search("mcp__x__placeholder_text"))

    def test_every_money_tool_is_covered(self):
        tools = self.money_tools()
        self.assertEqual(tools, ["exercise_option", "place_advanced_order", "place_crypto_order", "place_equity_order", "place_option_order"])
        layer1 = re.compile(self.load("hooks", "hooks.json")["hooks"]["PreToolUse"][0]["matcher"])
        codex = re.compile(self.load("integrations", "codex", "hooks.json")["hooks"]["PreToolUse"][0]["matcher"])
        for tool in tools:
            for server in ("robinhood-trading", "plugin_unofficial-rh-connector_robinhood", "rh-sandbox"):
                self.assertTrue(layer1.search("mcp__%s__%s" % (server, tool)))
            self.assertTrue(codex.search("mcp__robinhood__%s" % tool))

    def test_settings_deny_covers_both_server_names(self):
        deny = self.load("integrations", "claude-code", "settings.deny.json")["permissions"]["deny"]
        expected = ["mcp__%s__%s" % (server, tool)
                    for server in ("robinhood-trading", "plugin_unofficial-rh-connector_robinhood")
                    for tool in ("place_equity_order", "place_option_order", "place_crypto_order", "place_advanced_order", "exercise_option")]
        self.assertEqual(deny, expected)
        self.assertEqual(sorted(set(t.rsplit("__", 1)[1] for t in deny)), self.money_tools())

    def test_codex_and_cursor_snippets(self):
        codex = self.load("integrations", "codex", "hooks.json")
        self.assertEqual(codex["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "sh <REPO>/hooks/guard.sh money --format codex")
        cursor = self.load("integrations", "cursor", "hooks.json")
        self.assertEqual(cursor, {"version": 1, "hooks": {"beforeMCPExecution": [{"command": "sh <REPO>/hooks/guard.sh money --format cursor"}]}})

    def test_no_allow_decision_anywhere(self):
        pattern = re.compile(r'"(permissionDecision|permission)"\s*:\s*' + re.escape(ALLOW_TEXT))
        for folder in ("hooks", "integrations"):
            for root, _dirs, files in os.walk(os.path.join(REPO, folder)):
                for name in files:
                    if name.endswith((".pyc",)):
                        continue
                    with open(os.path.join(root, name), "r", encoding="utf-8", errors="replace") as handle:
                        self.assertIsNone(pattern.search(handle.read()), os.path.join(root, name))

    def test_generic_names_match_guard_sh(self):
        with open(GUARD, "r", encoding="utf-8") as handle:
            text = handle.read()
        body = text.split("is_generic_name() {", 1)[1].split("return 0 ;;", 1)[0]
        pattern = body.split("case $1 in", 1)[1].replace("\\\n", "").rsplit(")", 1)[0]
        names = set(n.strip() for n in pattern.split("|") if n.strip())
        module = _load_audit_module()
        self.assertEqual(names, set(module.GENERIC_NAMES))
        classes = self.load("connector", "tool-classes.json")
        self.assertTrue(names <= set(t["name"] for t in classes["tools"]))


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_log_under_test", AUDIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
