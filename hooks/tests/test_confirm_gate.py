"""Tests for hooks/confirm_gate.py (spec E.5 K1-K14) and its wiring through hooks/guard.sh.

Unofficial; not affiliated with Robinhood Markets, Inc.

Run from the repository root:
    python3 -m unittest hooks/tests/test_confirm_gate.py -v

Reviews reach the ledger the way they do in a real session: the review event is piped through the
real hooks/audit_log.py, which writes $STATE/ledger.jsonl. The place event then goes to the gate
directly (exit 0 + ask, or exit 3 + "CODE: reason") and, in the end-to-end tests, through
hooks/guard.sh money (exit 0 + the relayed ask, or exit 2). Every run uses a temporary state
directory, HOME and config directory.

Account numbers and ref_ids are synthetic and assembled at run time; the event files hold
__ACCT__ / __RHS__ / __REF__ / __CWD__ placeholders, so no account-like literal is committed.

The build ships with confirm mode wired but OFF. OffByDefault checks the promise that matters
most: with order_mode unset or simulate_only, every place_* call is denied even when a matching
review exists and the gate would have asked.
"""

import datetime
import hashlib
import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import redirect_stdout
from decimal import Decimal

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
GATE = os.path.join(REPO, "hooks", "confirm_gate.py")
AUDIT = os.path.join(REPO, "hooks", "audit_log.py")
GUARD = os.path.join(REPO, "hooks", "guard.sh")
EVENTS = os.path.join(HERE, "events")

ACCT = "".join(["5QX", "81", "X4F1"])
RHS = "".join(["5123", "45678"])
# Joined at run time: a "+" of string literals is folded into one constant in the .pyc, and the
# repo's static grep (test_guard.sh H9) would then find an allow decision in hooks/tests/__pycache__.
ALLOW_DECISION = "".join(['"permissionDecision":"', "al", "low", '"'])
ASK_PREFIX = '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"'
PLACE_TOOLS = ("place_equity_order", "place_option_order", "place_crypto_order", "place_advanced_order")
UUID_SERVER = "-".join(["00000000", "0000", "4000", "8000", "000000000000"])  # a connector named by UUID
STRIP_ENV = ("CLAUDE_PLUGIN_", "ROBINHOOD_SKILLS_", "XDG_", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ATTENDED")


def new_ref():
    return str(uuid.uuid4())


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_shared(name):
    for folder in (os.path.join(REPO, "hooks", "lib"), os.path.join(REPO, "shared", "scripts")):
        path = os.path.join(folder, name + ".py")
        if os.path.isfile(path):
            return load_module("test_" + name, path)
    raise unittest.SkipTest("%s.py not found" % name)


gate_module = load_module("confirm_gate_under_test", GATE)


def wait_for_text(path, text, seconds=5.0):
    """guard.sh prints its verdict first and writes blocked.jsonl from a detached child, so a check
    on that file waits for the child. Returns the file's text (or "" when it never appeared)."""
    deadline = time.monotonic() + seconds
    body = ""
    while time.monotonic() < deadline:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                body = handle.read()
        except OSError:
            body = ""
        if text in body:
            return body
        time.sleep(0.05)
    return body


def expected_et(ts):
    when = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local = when.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        local = gate_module.to_eastern(when)
    return local.strftime("%H:%M:%S ET")


class GateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rh-confirm-test-")
        self.state = os.path.join(self.tmp, "state")
        self.home = os.path.join(self.tmp, "home")
        self.project = os.path.join(self.tmp, "project")
        for folder in (self.home, self.project):
            os.makedirs(folder)
        self.outputs = []

    def tearDown(self):
        for root, dirs, _files in os.walk(self.tmp):
            for name in dirs:
                try:
                    os.chmod(os.path.join(root, name), 0o700)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- plumbing

    def env(self, **extra):
        env = dict((k, v) for k, v in os.environ.items() if not k.startswith(STRIP_ENV) and k != "CI")
        env.update({"ROBINHOOD_SKILLS_STATE": self.state, "HOME": self.home,
                    "XDG_CONFIG_HOME": os.path.join(self.home, ".config"),
                    "CLAUDE_PLUGIN_ROOT": REPO,
                    "CLAUDE_PLUGIN_OPTION_ORDER_MODE": "confirm",
                    "CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD": "500.00"})
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def event(self, name, ref=None, session=None, tool=None, **changes):
        with open(os.path.join(EVENTS, name), "r", encoding="utf-8") as handle:
            text = handle.read()
        for key, value in (("__ACCT__", ACCT), ("__RHS__", RHS), ("__CWD__", self.project),
                           ("__REF__", ref or "unused-ref")):
            text = text.replace(key, value)
        data = json.loads(text)
        if session is not None:
            data["session_id"] = session
        if tool is not None:
            data["tool_name"] = tool
        for key, value in changes.items():
            if value is None:
                data["tool_input"].pop(key, None)
            else:
                data["tool_input"][key] = value
        return data

    def audit(self, payload, **env):
        proc = subprocess.run([sys.executable, AUDIT], input=json.dumps(payload).encode("utf-8"),
                              env=self.env(**env), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def review(self, name="confirm_review_equity_order.json", **kw):
        """A review as the user saw it: through audit_log.py, into the ledger."""
        self.audit(self.event(name, **kw))
        return self.ledger()[-1]

    def ledger(self):
        path = os.path.join(self.state, "ledger.jsonl")
        with open(path, "rb") as handle:
            return [json.loads(line) for line in handle.read().split(b"\n") if line.strip()]

    def ledger_bytes(self):
        path = os.path.join(self.state, "ledger.jsonl")
        with open(path, "rb") as handle:
            return handle.read()

    def refids(self):
        path = os.path.join(self.state, "refids.json")
        with open(path, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))

    def age_ledger(self, seconds, index=-1):
        """Move one ledger entry's timestamp into the past (or the future with a negative age)."""
        rows = self.ledger()
        when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
        rows[index]["ts"] = when.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (when.microsecond // 1000)
        with open(os.path.join(self.state, "ledger.jsonl"), "wb") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")

    def check_hygiene(self, out):
        self.assertNotIn(ALLOW_DECISION, out)
        self.assertNotIn(ACCT, out)
        self.assertNotIn(RHS, out)
        self.assertEqual(out.count("\n"), 1, "exactly one line: %r" % out)

    def gate(self, payload, **env):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        started = time.monotonic()
        proc = subprocess.run([sys.executable, GATE], input=data, env=self.env(**env),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.elapsed = time.monotonic() - started
        out = proc.stdout.decode("utf-8")
        self.outputs.append(out)
        self.check_hygiene(out)
        self.assertEqual(proc.stderr, b"", "the gate writes nothing to stderr")
        return proc.returncode, out.rstrip("\n")

    def assert_ask(self, payload, contains=(), **env):
        rc, out = self.gate(payload, **env)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith(ASK_PREFIX), out)
        self.assertRegex(out, gate_module.ASK_RE)
        reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertLessEqual(len(reason), 600)
        self.assertNotIn('"', reason)
        self.assertNotIn("\\", reason)
        self.assertTrue(all(32 <= ord(ch) <= 126 for ch in reason), reason)
        self.assertIn("Approve only if this is exactly what you intend.", reason)
        for text in contains:
            self.assertIn(text, reason)
        return reason

    def assert_deny(self, payload, code, contains=(), **env):
        rc, out = self.gate(payload, **env)
        self.assertEqual(rc, 3, out)
        self.assertTrue(out.startswith(code + ": "), "expected %s, got %s" % (code, out))
        self.assertNotIn('"', out)
        for text in contains:
            self.assertIn(text, out)
        return out

    def guard(self, payload, mode="money", **env):
        data = json.dumps(payload).encode("utf-8")
        proc = subprocess.run(["sh", GUARD, mode], input=data, env=self.env(**env),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def place(self, ref=None, **changes):
        return self.event("confirm_place_equity_order.json", ref=ref or new_ref(), **changes)


# ==================================================================== the default: confirm mode OFF

class OffByDefault(GateCase):
    """With order_mode unset or simulate_only, every place_* is denied, even with a matching review."""

    def test_every_place_tool_is_denied_through_the_guard_when_mode_is_not_confirm(self):
        self.review()
        events = [self.place()]
        for tool in PLACE_TOOLS + ("exercise_option",):
            events.append(self.place(tool="mcp__robinhood-trading__" + tool))
        for mode in (None, "", "simulate_only", "Confirm", "CONFIRM", "confirm "):
            for payload in events:
                rc, out, err = self.guard(payload, CLAUDE_PLUGIN_OPTION_ORDER_MODE=mode)
                self.assertEqual(rc, 2, (mode, payload["tool_name"], out, err))
                self.assertEqual(out, "")
                self.assertIn("simulate-only", err)
                self.assertIn("Nothing was placed", err)
        self.assertIsNone(self.ledger()[-1]["consumed_by"], "the gate must not have been consulted")
        self.assertFalse(os.path.exists(os.path.join(self.state, "refids.json")))

    def test_the_gate_itself_refuses_when_mode_is_not_confirm(self):
        self.review()
        for mode in (None, "", "simulate_only", "CONFIRM"):
            self.assert_deny(self.place(), "CONFIRM_MODE_OFF", CLAUDE_PLUGIN_OPTION_ORDER_MODE=mode)

    def test_session_line_says_installed_off(self):
        rc, out, _ = self.guard({"session_id": "s", "hook_event_name": "SessionStart"}, mode="session",
                                CLAUDE_PLUGIN_OPTION_ORDER_MODE=None)
        self.assertEqual(rc, 0)
        self.assertIn("SIMULATE-ONLY", out)
        self.assertIn("CONFIRM MODE: INSTALLED, OFF.", out)

    def test_session_line_says_on_only_when_switched_on_with_a_cap(self):
        rc, out, _ = self.guard({"session_id": "s", "hook_event_name": "SessionStart"}, mode="session")
        self.assertEqual(rc, 0)
        self.assertIn("CONFIRM MODE: ON", out)
        rc, out, _ = self.guard({"session_id": "s", "hook_event_name": "SessionStart"}, mode="session",
                                CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD="")
        self.assertIn("SELECTED BUT UNAVAILABLE", out)


# ==================================================================== K1-K14

class BindingToTheReview(GateCase):
    def test_k1_no_review_in_the_ledger(self):
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW")
        os.makedirs(self.state)
        with open(os.path.join(self.state, "salt"), "wb") as handle:
            handle.write(os.urandom(16))
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW")
        self.review(name="confirm_review_option_order.json")  # a ledger, but no equity review
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW", contains=("no review_equity_order in this session",))

    def test_k1_through_the_guard(self):
        rc, out, err = self.guard(self.place())
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("confirm gate refused: NO_MATCHING_REVIEW", err)
        self.assertIn("Nothing was placed", err)
        self.assertIn('"reason_code":"NO_MATCHING_REVIEW"',
                      wait_for_text(os.path.join(self.state, "audit", "blocked.jsonl"), "NO_MATCHING_REVIEW"))

    def test_k2_changed_quantity_names_the_field(self):
        self.review()
        out = self.assert_deny(self.place(quantity="12"), "NO_MATCHING_REVIEW")
        self.assertIn("fields that differ from your latest review_equity_order", out)
        self.assertIn(": quantity", out)
        self.assertNotIn("limit_price", out)

    def test_k2_changed_price_and_session_are_both_named(self):
        self.review()
        out = self.assert_deny(self.place(limit_price="31.30", market_hours="extended_hours"), "NO_MATCHING_REVIEW")
        self.assertIn("limit_price", out)
        self.assertIn("market_hours", out)

    def test_review_from_another_session_does_not_count(self):
        self.review(session="another-session")
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW")

    def test_review_with_validation_errors_does_not_count(self):
        payload = self.event("confirm_review_equity_order.json")
        payload["tool_response"] = [{"type": "text", "text": json.dumps(
            {"data": {"symbol": "PLTR", "order_checks": {}, "validation_errors": ["limit_price is too far"]},
             "guide": "g"})}]
        self.audit(payload)
        self.assertIs(self.ledger()[-1]["review_ok"], False)
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW", contains=("validation errors",))

    def test_a_review_of_another_tool_does_not_count(self):
        self.review()
        place = self.event("confirm_place_crypto_order.json", ref=new_ref())
        self.assert_deny(place, "NO_MATCHING_REVIEW")

    def test_k3_same_ref_after_success_is_a_duplicate(self):
        self.review()
        ref = new_ref()
        self.assert_ask(self.place(ref))
        self.audit(self.event("confirm_post_place_equity_order.json", ref=ref))
        self.assertEqual(self.refids()[ref]["status"], "succeeded")
        self.assert_deny(self.place(ref), "DUPLICATE_ORDER", contains=("get_equity_orders",))

    def test_k4_same_ref_with_a_different_order(self):
        self.review()
        self.review(quantity="12")
        ref = new_ref()
        self.assert_ask(self.place(ref))
        self.assert_deny(self.place(ref, quantity="12"), "REF_ID_REUSED")

    def test_k5_new_ref_for_a_spent_review(self):
        self.review()
        ref = new_ref()
        self.assert_ask(self.place(ref))
        self.audit(self.event("confirm_post_place_equity_order.json", ref=ref))
        self.assert_deny(self.place(new_ref()), "REVIEW_ALREADY_CONSUMED", contains=("get_equity_orders",))

    def test_k5_a_second_review_of_the_same_order_can_be_placed_again(self):
        self.review()
        first = new_ref()
        self.assert_ask(self.place(first))
        self.audit(self.event("confirm_post_place_equity_order.json", ref=first))
        self.review()  # the user reviewed the same order again and approves a second, new order
        self.assert_ask(self.place(new_ref()))

    def test_k6_pending_ref_retried(self):
        self.review()
        ref = new_ref()
        self.assert_ask(self.place(ref))
        reason = self.assert_ask(self.place(ref), contains=("retry of ref %s" % ref[:8], "last status pending",
                                                            "get_equity_orders"))
        self.assertIn("confirm with get_equity_orders that no order exists", reason)

    def test_k6_failed_ref_retried(self):
        self.review()
        ref = new_ref()
        self.assert_ask(self.place(ref))
        failed = self.event("confirm_post_place_equity_order.json", ref=ref)
        failed["tool_response"] = [{"type": "text", "text": "Error: upstream timeout"}]
        self.audit(failed)
        self.assertEqual(self.refids()[ref]["status"], "failed")
        self.assert_ask(self.place(ref), contains=("retry of ref", "last status failed"))

    def test_consumed_review_with_no_ref_record_is_refused(self):
        self.review()
        ref = new_ref()
        self.assert_ask(self.place(ref))
        os.remove(os.path.join(self.state, "refids.json"))
        self.assert_deny(self.place(ref), "DUPLICATE_ORDER", contains=("not on record",))

    def test_retry_must_resend_the_ref_id_exactly(self):
        self.review()
        ref = new_ref()
        self.assert_ask(self.place(ref))
        self.assert_deny(self.place(ref.upper()), "REF_ID_REUSED", contains=("letter case",))

    def test_non_positive_size_is_refused(self):
        self.review(quantity="-10")
        self.assert_deny(self.place(quantity="-10"), "NOTIONAL_UNKNOWN", contains=("not above zero",))

    def test_corrupt_refids_file_is_refused(self):
        self.review()
        with open(os.path.join(self.state, "refids.json"), "w") as handle:
            handle.write("{not json")
        self.assert_deny(self.place(), "GATE_FAILED", contains=("refids.json",))

    def test_ref_id_must_be_a_uuid(self):
        self.review()
        for ref in (None, "", "ref-1", "not-a-uuid-at-all", 12345):
            payload = self.place()
            if ref is None:
                payload["tool_input"].pop("ref_id")
            else:
                payload["tool_input"]["ref_id"] = ref
            self.assert_deny(payload, "REF_ID_INVALID")
        self.assert_ask(self.place(new_ref().upper()))

    def test_k12_review_older_than_the_ttl(self):
        self.review()
        self.age_ledger(301)
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW", contains=("older than 300 s",))
        self.age_ledger(290)
        self.assert_ask(self.place())

    def test_k12_ttl_is_clamped_to_60_900(self):
        self.review()
        self.age_ledger(90)
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW", contains=("older than 60 s",),
                         CLAUDE_PLUGIN_OPTION_REVIEW_TTL_SECONDS="10")
        self.age_ledger(1000)
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW", contains=("older than 900 s",),
                         CLAUDE_PLUGIN_OPTION_REVIEW_TTL_SECONDS="5000")
        self.age_ledger(800)
        self.assert_ask(self.place(), CLAUDE_PLUGIN_OPTION_REVIEW_TTL_SECONDS="5000")

    def test_k12_unreadable_ttl_means_the_default(self):
        self.review()
        self.age_ledger(301)
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW", contains=("older than 300 s",),
                         CLAUDE_PLUGIN_OPTION_REVIEW_TTL_SECONDS="five minutes")

    def test_a_review_stamped_in_the_future_does_not_count(self):
        self.review()
        self.age_ledger(-3600)
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW")

    def test_k14_canon_parity(self):
        canon = load_shared("canon")
        review = self.event("confirm_review_option_order.json")["tool_input"]
        place = self.event("confirm_place_option_order.json", ref=new_ref())["tool_input"]
        self.assertEqual(canon.fingerprint("review_option_order", review)["fingerprint"],
                         canon.fingerprint("place_option_order", place)["fingerprint"])
        equity_review = self.event("confirm_review_equity_order.json")["tool_input"]
        equity_place = dict(self.place()["tool_input"], limit_price="31.240", quantity="10.0", symbol="pltr",
                            time_in_force="GFD")
        self.assertEqual(canon.fingerprint("review_equity_order", equity_review)["fingerprint"],
                         canon.fingerprint("place_equity_order", equity_place)["fingerprint"])

    def test_k14_option_review_binds_the_place_call_end_to_end(self):
        self.review(name="confirm_review_option_order.json")
        reason = self.assert_ask(self.event("confirm_place_option_order.json", ref=new_ref()),
                                 contains=("LIVE ORDER - place_option_order: OPTION x2 - LIMIT 1.5 debit",
                                           "2 legs: BUY OPEN opt-leg-a, SELL OPEN opt-leg-b",
                                           "est 300.00 USD net premium paid (cap 500.00)"))
        self.assertIn("account ****X4F1", reason)

    def test_state_is_written_on_ask_only(self):
        self.review()
        before = self.ledger_bytes()
        self.assert_deny(self.place(quantity="12"), "NO_MATCHING_REVIEW")
        self.assertEqual(self.ledger_bytes(), before)
        self.assertFalse(os.path.exists(os.path.join(self.state, "refids.json")))
        ref = new_ref()
        self.assert_ask(self.place(ref))
        record = self.refids()[ref]
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["fingerprint"], self.ledger()[-1]["fingerprint"])
        self.assertEqual(self.ledger()[-1]["consumed_by"], ref)
        mode = os.stat(os.path.join(self.state, "refids.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual(os.stat(os.path.join(self.state, "ledger.jsonl")).st_mode & 0o777, 0o600)


class HumanAtThePrompt(GateCase):
    def test_k7_permission_modes_that_can_skip_the_prompt(self):
        self.review()
        for mode in ("bypassPermissions", "dontAsk", "plan", "auto", "Default"):
            payload = self.place()
            payload["permission_mode"] = mode
            self.assert_deny(payload, "NOT_INTERACTIVE", contains=("human at a permission prompt",))
        payload = self.place()
        payload.pop("permission_mode")
        self.assert_deny(payload, "CANNOT_VERIFY_SESSION", contains=("permission_mode",))
        payload = self.place()
        payload["permission_mode"] = "acceptEdits"
        self.assert_ask(payload)

    def test_headless_and_unattended_runs(self):
        self.review()
        for env in ({"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}, {"CLAUDE_CODE_ENTRYPOINT": "sdk-py"},
                    {"CLAUDE_CODE_SESSION_ATTENDED": "0"}, {"CI": "true"}, {"CI": "1"}):
            self.assert_deny(self.place(), "NOT_INTERACTIVE", **env)
        self.assert_ask(self.place(), CLAUDE_CODE_ENTRYPOINT="cli", CI="false", CLAUDE_CODE_SESSION_ATTENDED="1")

    def test_missing_keys_and_unreadable_input(self):
        for payload in (b"", b"garbage", b"{}", b"[]", b'"text"'):
            self.assert_deny(payload, "CANNOT_VERIFY_SESSION")
        for key in ("tool_name", "tool_input", "session_id"):
            payload = self.place()
            payload.pop(key)
            self.assert_deny(payload, "CANNOT_VERIFY_SESSION", contains=(key,))
        payload = self.place()
        payload["hook_event_name"] = "PostToolUse"
        self.assert_deny(payload, "CANNOT_VERIFY_SESSION")

    def test_k13_audit_log_off(self):
        self.review()
        for value in ("false", "0", "no", "off", "False"):
            self.assert_deny(self.place(), "AUDIT_LOG_OFF", CLAUDE_PLUGIN_OPTION_AUDIT_LOG=value)
        self.assert_ask(self.place(), CLAUDE_PLUGIN_OPTION_AUDIT_LOG="true")

    def test_k10_exercise_and_other_tools_are_never_confirmable(self):
        self.review()
        for tool in ("exercise_option", "replace_option_order", "submit_order", "place_order"):
            payload = self.event("confirm_exercise_option.json", ref=new_ref(),
                                 tool="mcp__robinhood-trading__" + tool)
            self.assert_deny(payload, "NOT_CONFIRMABLE")

    def test_k10_exercise_never_reaches_the_gate_through_the_guard(self):
        rc, out, err = self.guard(self.event("confirm_exercise_option.json", ref=new_ref()))
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("blocked in every order mode", err)
        self.assertNotIn("confirm gate refused", err)

    def test_server_must_be_robinhood(self):
        self.review(tool="mcp__otherbroker__review_equity_order")
        payload = self.place(tool="mcp__otherbroker__place_equity_order")
        self.assert_deny(payload, "NOT_ROBINHOOD_SERVER")
        with open(os.path.join(self.state, "servers.txt"), "a") as handle:
            handle.write("otherbroker\n")
        self.assert_ask(payload)


class NotionalAndPolicy(GateCase):
    def test_k8_over_the_cap(self):
        self.review()
        self.assert_deny(self.place(), "POLICY_CAP", contains=("312.40", "300.00"),
                         CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD="300.00")
        self.assert_deny(self.place(), "POLICY_CAP", CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD="312.39")
        self.assert_ask(self.place(), contains=("(cap 312.40)",), CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD="312.4")

    def test_k8b_notional_not_computable(self):
        payload = self.event("confirm_review_equity_order.json", type="market", limit_price=None,
                             market_hours="regular_hours")
        payload["tool_response"] = [{"type": "text", "text": json.dumps(
            {"data": {"symbol": "PLTR", "order_checks": {}}, "guide": "g"})}]  # no quote recorded
        self.audit(payload)
        self.assert_deny(self.place(type="market", limit_price=None, market_hours="regular_hours"),
                         "NOTIONAL_UNKNOWN", contains=("ask price",))

    def test_k8b_option_market_order_is_not_computable(self):
        self.review(name="confirm_review_option_order.json", price=None, type="market", direction=None,
                    legs=[{"option_id": "opt-leg-a", "side": "buy", "position_effect": "open"}])
        place = self.event("confirm_place_option_order.json", ref=new_ref(), price=None, type="market",
                           direction=None,
                           legs=[{"option_id": "opt-leg-a", "side": "buy", "position_effect": "open",
                                  "ratio_quantity": 1}])
        self.assert_deny(place, "NOTIONAL_UNKNOWN")

    def test_k8c_cap_unset_or_malformed(self):
        self.review()
        for cap in (None, "", "0", "0.00", "abc", "-5", "1,000", "5e2"):
            self.assert_deny(self.place(), "CAP_NOT_SET", CLAUDE_PLUGIN_OPTION_MAX_ORDER_NOTIONAL_USD=cap)

    def test_market_order_uses_the_review_quote(self):
        self.review(type="market", limit_price=None, market_hours="regular_hours")
        self.assert_ask(self.place(type="market", limit_price=None, market_hours="regular_hours"),
                        contains=("BUY 10 PLTR - MARKET - gfd - regular_hours",
                                  "est 312.40 USD at the review ask 31.24 (cap 500.00)"))

    def test_stop_order_uses_the_higher_of_stop_and_quote(self):
        changes = {"side": "sell", "type": "stop_market", "limit_price": None, "stop_price": "30",
                   "quantity": "5", "market_hours": "regular_hours", "time_in_force": "gtc"}
        self.review(**changes)
        self.assert_ask(self.place(**changes), contains=("SELL 5 PLTR - STOP 30 (becomes a market order) - gtc",
                                                         "est 155.90 USD at the stop or review bid"))

    def test_oco_uses_the_larger_leg(self):
        self.review(name="confirm_review_advanced_order.json")
        self.assert_ask(self.event("confirm_place_advanced_order.json", ref=new_ref()),
                        contains=("LIVE ORDER - place_advanced_order: OCO SELL 2 AMD - take-profit 180 / stop 142 - "
                                  "gtc - regular_hours - account ****X4F1",
                                  "est 360.00 USD if the larger leg fills (cap 500.00)"))

    def test_crypto_dollar_amount(self):
        self.review(name="confirm_preview_crypto_order.json")
        reason = self.assert_ask(self.event("confirm_place_crypto_order.json", ref=new_ref()),
                                 contains=("SELL $250.00 of ETH-USD - MARKET - gtc - crypto account ****5678",
                                           "est 250.00 USD (cap 500.00)"))
        self.assertNotIn(RHS, reason)

    def write_config(self, body):
        folder = os.path.join(self.project, ".robinhood")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "config.toml"), "w", encoding="utf-8") as handle:
            handle.write(body)

    def test_policy_denylist(self):
        self.review()
        self.write_config('[policy]\nsymbol_denylist = ["PLTR"]\n')
        self.assert_deny(self.place(), "POLICY_DENY", contains=("symbol_denylist",))

    def test_policy_max_order_usd_is_the_lower_cap(self):
        self.review()
        self.write_config('[policy]\nmax_order_usd = "100.00"\n')
        self.assert_deny(self.place(), "POLICY_CAP", contains=("max_order_usd",))

    def test_policy_allowed_sessions(self):
        self.review()
        self.write_config('[policy]\nallowed_sessions = ["regular_hours"]\n')
        self.assert_deny(self.place(), "POLICY_DENY", contains=("allowed_sessions",))

    def test_policy_allow_options_false(self):
        self.review(name="confirm_review_option_order.json")
        self.write_config("[policy]\nallow_options = false\n")
        self.assert_deny(self.event("confirm_place_option_order.json", ref=new_ref()), "POLICY_DENY",
                         contains=("allow_options",))

    def test_symbol_lists_cannot_be_checked_for_options(self):
        # place_option_order carries contract ids only, so the underlying is unknown: unknown is not a pass.
        self.review(name="confirm_review_option_order.json")
        self.write_config('[policy]\nsymbol_allowlist = ["AMD"]\n')
        self.assert_deny(self.event("confirm_place_option_order.json", ref=new_ref()), "POLICY_DENY",
                         contains=("symbol_allowlist", "symbol unknown"))

    def test_unreadable_or_invalid_config_is_refused(self):
        self.review()
        self.write_config("[policy]\nmax_order_usd = 5.5\n")  # floats are outside the kit's grammar
        self.assert_deny(self.place(), "POLICY_DENY", contains=("could not be read",))
        self.write_config('[policy]\nmax_order_usd = "-3"\n')
        self.assert_deny(self.place(), "POLICY_DENY", contains=("invalid value",))

    def test_rules_that_need_live_data_are_left_to_the_skill(self):
        self.review()
        self.write_config('[policy]\nmax_symbol_pct_household = "5"\nearnings_blackout_days = 3\n'
                          'max_orders_per_day = 1\nsymbol_allowlist = ["PLTR", "AMD"]\n')
        self.assert_ask(self.place())

    def test_config_from_the_environment_path(self):
        self.review()
        path = os.path.join(self.tmp, "elsewhere.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('[policy]\nsymbol_denylist = ["PLTR"]\n')
        self.assert_deny(self.place(), "POLICY_DENY", ROBINHOOD_SKILLS_CONFIG=path)

    def test_lint_errors_are_refused(self):
        self.review(name="confirm_review_option_order.json")
        place = self.event("confirm_place_option_order.json", ref=new_ref(), chain_symbol="AMD")
        self.assert_deny(place, "LINT_ERROR", contains=("UNKNOWN_PARAM", "chain_symbol"))


class TheAsk(GateCase):
    def test_k9_exact_restatement(self):
        entry = self.review()
        reason = self.assert_ask(self.place())
        expected = ("LIVE ORDER - place_equity_order: BUY 10 PLTR - LIMIT 31.24 - gfd - all_day_hours - "
                    "account ****X4F1 - est 312.40 USD (cap 500.00). Matches your review at %s (ticket %s). "
                    "Approve only if this is exactly what you intend." % (expected_et(entry["ts"]), entry["ticket_id"]))
        self.assertEqual(reason, expected)

    def test_k9_review_alerts_are_mentioned(self):
        payload = self.event("confirm_review_equity_order.json")
        data = json.loads(payload["tool_response"][0]["text"])
        data["data"]["order_checks"] = {"alertType": "EQUITY_NOT_ENOUGH_BP", "equityNotEnoughBpAlertDetails": {}}
        payload["tool_response"][0]["text"] = json.dumps(data)
        self.audit(payload)
        self.assertEqual(self.ledger()[-1]["alerts_count"], 1)
        self.assert_ask(self.place(), contains=("That review returned 1 alert; read it on the ticket.",))

    def test_k9_ask_is_relayed_byte_for_byte_by_the_guard(self):
        self.review()
        ref = new_ref()
        rc, out, err = self.guard(self.place(ref))
        self.assertEqual(rc, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(out.count("\n"), 1)
        self.assertRegex(out.rstrip("\n"), gate_module.ASK_RE)
        self.assertNotIn(ALLOW_DECISION, out)
        self.assertEqual(self.refids()[ref]["status"], "pending")
        self.assertIn('"event":"pre_ask"', wait_for_text(os.path.join(self.state, "audit", "blocked.jsonl"),
                                                         '"event":"pre_ask"'))

    def test_k9_guard_refusal_for_a_changed_order(self):
        self.review()
        rc, out, err = self.guard(self.place(quantity="11"))
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("confirm gate refused: NO_MATCHING_REVIEW", err)
        self.assertIn("quantity", err)

    def test_dollar_amount_and_tax_lots_restatement(self):
        changes = {"type": "market", "limit_price": None, "quantity": None, "dollar_amount": "200",
                   "market_hours": "regular_hours"}
        self.review(**changes)
        self.assert_ask(self.place(**changes), contains=("BUY $200.00 of PLTR - MARKET", "est 200.00 USD (cap"))

    def test_eastern_time_fallback(self):
        cases = [("2026-11-17T01:05:11", -5), ("2026-07-01T16:00:00", -4), ("2026-03-08T06:59:59", -5),
                 ("2026-03-08T07:00:00", -4), ("2026-11-01T05:59:59", -4), ("2026-11-01T06:00:00", -5),
                 ("2027-03-14T07:00:00", -4), ("2027-11-07T06:00:00", -5)]
        for text, hours in cases:
            when = datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
            self.assertEqual(gate_module._us_eastern_offset_hours(when), hours, text)

    def test_lock_busy_then_stale(self):
        self.review()
        lock = os.path.join(self.state, ".lock")
        with open(lock, "w") as handle:
            handle.write("1")
        self.assert_deny(self.place(), "GATE_BUSY")
        old = datetime.datetime.now().timestamp() - 120
        os.utime(lock, (old, old))
        self.assert_ask(self.place())
        self.assertFalse(os.path.exists(lock), "the gate releases the lock")

    def test_crash_exits_4_with_gate_failed(self):
        original = gate_module.run
        buffer = io.StringIO()

        def boom(env=None):
            raise RuntimeError("synthetic failure")

        gate_module.run = boom
        try:
            with redirect_stdout(buffer):
                code = gate_module.main([])
        finally:
            gate_module.run = original
        self.assertEqual(code, 4)
        self.assertTrue(buffer.getvalue().startswith("GATE_FAILED: internal error (RuntimeError)"))
        self.assertNotIn("ask", buffer.getvalue())

    def test_missing_canon_is_refused(self):
        root = os.path.join(self.tmp, "root")
        os.makedirs(os.path.join(root, "hooks", "lib"))
        shutil.copy(GATE, os.path.join(root, "hooks", "confirm_gate.py"))
        with open(os.path.join(root, "hooks", "lib", "canon.py"), "w") as handle:
            handle.write("this is not python\n")
        self.review()
        proc = subprocess.run([sys.executable, os.path.join(root, "hooks", "confirm_gate.py")],
                              input=json.dumps(self.place()).encode("utf-8"), env=self.env(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(proc.returncode, 3)
        self.assertTrue(proc.stdout.decode().startswith("GATE_FAILED: canon.py could not be loaded"))

    def test_selftest(self):
        proc = subprocess.run([sys.executable, GATE, "--selftest"], env=self.env(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(json.loads(proc.stdout.decode())["ok"])
        self.assertFalse(os.path.exists(self.state), "the self-test never touches the real state directory")

    def test_no_file_in_the_package_prints_allow(self):
        for path in (GATE, __file__):
            with open(path, "r", encoding="utf-8") as handle:
                self.assertNotRegex(handle.read(), r'"(permissionDecision|permission)"\s*:\s*"allow"')


# ==================================================================== the review's server

class SameServerBinding(GateCase):
    """A review binds only a place call on the MCP server that answered it. hooks/audit_log.py
    records a review from any server that serves a known review tool name (the kit's sandbox, an
    eval mock, a look-alike), so without this a stale or synthetic quote could size a live order."""

    def learn(self, *servers):
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "servers.txt"), "a", encoding="utf-8") as handle:
            for server in servers:
                handle.write(server + "\n")

    def test_a_review_answered_by_another_server_never_sizes_a_live_order(self):
        # The finding's scenario: a look-alike server quotes AAPL at 1.00; the real order is live.
        self.learn(UUID_SERVER)
        changes = {"symbol": "AAPL", "type": "market", "limit_price": None, "quantity": "400",
                   "market_hours": "regular_hours"}
        payload = self.event("confirm_review_equity_order.json", tool="mcp__some-other-server__review_equity_order",
                             **changes)
        payload["tool_response"] = [{"type": "text", "text": json.dumps(
            {"data": {"order_checks": {}, "quote_data": {"bid_price": "0.99", "ask_price": "1.00"}}, "guide": "g"})}]
        self.audit(payload)
        entry = self.ledger()[-1]
        self.assertRegex(entry["server"], r"^[0-9a-f]{8}$")
        self.assertNotIn("some-other-server", json.dumps(entry))
        out = self.assert_deny(self.place(tool="mcp__%s__place_equity_order" % UUID_SERVER, **changes),
                               "NO_MATCHING_REVIEW", contains=("different MCP server",))
        self.assertNotIn("Matches your review", out)
        self.assertIsNone(self.ledger()[-1]["consumed_by"])

    def test_the_sandbox_review_does_not_bind_the_connector(self):
        self.learn("rh-sandbox", UUID_SERVER)
        self.review(tool="mcp__rh-sandbox__review_equity_order")
        self.assert_deny(self.place(tool="mcp__%s__place_equity_order" % UUID_SERVER), "NO_MATCHING_REVIEW",
                         contains=("different MCP server",))
        self.assert_ask(self.place(tool="mcp__rh-sandbox__place_equity_order"))

    def test_robinhood_named_servers_do_not_vouch_for_each_other(self):
        self.review()  # answered by mcp__robinhood-trading__
        self.assert_deny(self.place(tool="mcp__plugin_unofficial-rh-connector_robinhood__place_equity_order"),
                         "NO_MATCHING_REVIEW", contains=("different MCP server",))

    def test_review_and_place_on_the_same_uuid_server_asks(self):
        self.learn(UUID_SERVER)
        self.review(tool="mcp__%s__review_equity_order" % UUID_SERVER)
        self.assert_ask(self.place(tool="mcp__%s__place_equity_order" % UUID_SERVER))

    def test_a_ledger_entry_without_a_server_never_binds(self):
        self.review()
        rows = self.ledger()
        rows[-1].pop("server")
        with open(os.path.join(self.state, "ledger.jsonl"), "wb") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":")).encode("utf-8") + b"\n")
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW")

    def test_same_server_field_diff_is_still_named(self):
        self.review(tool="mcp__rh-sandbox__review_equity_order")
        self.review()
        out = self.assert_deny(self.place(quantity="12"), "NO_MATCHING_REVIEW")
        self.assertIn(": quantity", out)


# ==================================================================== short options

LEG_A = "".join(["0f1e2d3c", "-aaaa-4bbb-8ccc-", "000000000001"])
LEG_B = "".join(["0f1e2d3c", "-aaaa-4bbb-8ccc-", "000000000002"])
LEG_C = "".join(["0f1e2d3c", "-aaaa-4bbb-8ccc-", "000000000003"])


class ShortOptionExposure(GateCase):
    """The option notional is its premium. That bounds a long option or a debit spread, not an order
    that opens a short leg: a cash-secured put for 0.05 commits its strike, not 5 dollars."""

    def option(self, legs, price, quantity="10", direction=None, otype="limit"):
        changes = {"legs": legs, "price": price, "quantity": quantity, "direction": direction, "type": otype}
        self.review(name="confirm_review_option_order.json", **changes)
        return self.event("confirm_place_option_order.json", ref=new_ref(), **changes)

    @staticmethod
    def leg(option_id, side, effect, ratio=1):
        return {"option_id": option_id, "side": side, "position_effect": effect, "ratio_quantity": ratio}

    def test_single_leg_sell_to_open_is_refused(self):
        # The finding's case: 10 short puts at 0.05 read "est 50.00 USD" against a 500 cap.
        place = self.option([self.leg(LEG_A, "sell", "open")], "0.05")
        out = self.assert_deny(place, "NOTIONAL_UNKNOWN", contains=("sells an option to open",))
        self.assertNotIn("50.00", out)

    def test_credit_vertical_is_refused(self):
        place = self.option([self.leg(LEG_A, "sell", "open"), self.leg(LEG_B, "buy", "open")], "0.40",
                            direction="credit")
        self.assert_deny(place, "NOTIONAL_UNKNOWN", contains=("not a net debit",))

    def test_multi_leg_short_without_a_direction_is_refused(self):
        place = self.option([self.leg(LEG_A, "sell", "open"), self.leg(LEG_B, "buy", "open")], "0.40")
        self.assert_deny(place, "NOTIONAL_UNKNOWN", contains=("not a net debit",))

    def test_debit_ratio_spread_with_an_uncovered_short_is_refused(self):
        place = self.option([self.leg(LEG_A, "buy", "open"), self.leg(LEG_B, "sell", "open", 2)], "0.20",
                            direction="debit")
        self.assert_deny(place, "NOTIONAL_UNKNOWN", contains=("more short option contracts",))

    def test_debit_roll_into_a_short_is_refused(self):
        place = self.option([self.leg(LEG_A, "buy", "close"), self.leg(LEG_B, "sell", "open")], "0.10",
                            direction="debit")
        self.assert_deny(place, "NOTIONAL_UNKNOWN")
        place = self.option([self.leg(LEG_A, "sell", "close"), self.leg(LEG_B, "sell", "open"),
                             self.leg(LEG_C, "buy", "open")], "0.10", direction="debit")
        self.assert_deny(place, "NOTIONAL_UNKNOWN", contains=("a roll",))

    def test_long_option_and_closing_orders_are_sized_at_their_premium(self):
        place = self.option([self.leg(LEG_A, "buy", "open")], "1.20", quantity="2")
        self.assert_ask(place, contains=("est 240.00 USD premium paid (cap 500.00)",))
        place = self.option([self.leg(LEG_A, "sell", "close")], "1.20", quantity="2")
        self.assert_ask(place, contains=("est 240.00 USD premium received (cap 500.00)",))
        place = self.option([self.leg(LEG_A, "sell", "close"), self.leg(LEG_B, "buy", "open")], "0.50",
                            quantity="2", direction="debit")
        self.assert_ask(place, contains=("est 100.00 USD net premium paid (cap 500.00)",))

    def test_notional_rule_directly(self):
        n = gate_module.notional
        short = {"quantity": "10", "price": "0.05", "type": "limit",
                 "legs": [{"option_id": "a", "side": "sell", "position_effect": "open", "ratio_quantity": 1}]}
        self.assertIsNone(n("option", short, {})[0])
        weird = dict(short, legs=[{"option_id": "a", "side": "sell", "position_effect": "open", "ratio_quantity": "x"}])
        self.assertIsNone(n("option", weird, {})[0])
        self.assertIsNone(n("option", dict(short, legs=[]), {})[0])
        vertical = {"quantity": "2", "price": "1.5", "type": "limit", "direction": "debit",
                    "legs": [{"option_id": "a", "side": "buy", "position_effect": "open", "ratio_quantity": 1},
                             {"option_id": "b", "side": "sell", "position_effect": "open", "ratio_quantity": 1}]}
        self.assertEqual(n("option", vertical, {}), (Decimal("300.0"), "net premium paid"))


# ==================================================================== ref_id exactly as sent

class ExactRefId(GateCase):
    def test_padded_or_newline_terminated_ref_ids_are_refused(self):
        self.review()
        ref = new_ref()
        for bad in (" " + ref, ref + " ", ref + "\n", "\t" + ref, ref + "\r\n"):
            self.assert_deny(self.place(ref_id=bad), "REF_ID_INVALID", contains=("no spaces",))
        self.assertFalse(os.path.exists(os.path.join(self.state, "refids.json")))

    def test_a_padded_resend_after_a_success_is_never_a_retry(self):
        self.review()
        ref = new_ref()
        self.assert_ask(self.place(ref))
        self.audit(self.event("confirm_post_place_equity_order.json", ref=ref))
        self.assertEqual(self.refids()[ref]["status"], "succeeded")
        self.assert_deny(self.place(ref), "DUPLICATE_ORDER")
        for bad in (" " + ref, ref + " ", ref + "\n"):
            self.assert_deny(self.place(ref_id=bad), "REF_ID_INVALID")

    def test_audit_log_never_records_a_padded_ref_under_the_trimmed_key(self):
        self.review()
        ref = new_ref()
        self.assert_ask(self.place(ref))
        post = self.event("confirm_post_place_equity_order.json", ref=ref)
        post["tool_input"]["ref_id"] = ref + "\n"
        self.audit(post)
        self.assertEqual(self.refids()[ref]["status"], "pending")
        self.assertEqual(sorted(self.refids()), [ref])


# ==================================================================== the deadline

class Deadline(GateCase):
    """A hook still running at its timeout is killed, and Claude Code then treats the call as if the
    hook had no opinion. The gate must therefore always answer in time, and a refusal is the answer."""

    def stuck_root(self, body):
        root = os.path.join(self.tmp, "root")
        os.makedirs(os.path.join(root, "hooks", "lib"))
        shutil.copy(GATE, os.path.join(root, "hooks", "confirm_gate.py"))
        shutil.copy(GUARD, os.path.join(root, "hooks", "guard.sh"))
        with open(os.path.join(root, "hooks", "lib", "canon.py"), "w", encoding="utf-8") as handle:
            handle.write(body)
        return root

    # The checks hang (canon.py never returns), and the stuck work leaves a process behind that
    # still holds everything it inherited: it must not be able to hold the caller's pipes.
    HANG = "import os, time\nif os.fork() == 0:\n    time.sleep(6)\n    os._exit(0)\ntime.sleep(60)\n"

    def test_a_stuck_gate_answers_gate_failed_in_time(self):
        root = self.stuck_root(self.HANG)
        self.review()
        started = time.monotonic()
        proc = subprocess.run([sys.executable, os.path.join(root, "hooks", "confirm_gate.py")],
                              input=json.dumps(self.place()).encode("utf-8"),
                              env=self.env(ROBINHOOD_SKILLS_GATE_DEADLINE_SECONDS="1"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        elapsed = time.monotonic() - started
        self.assertEqual(proc.returncode, 4, proc.stdout)
        self.assertTrue(proc.stdout.decode().startswith("GATE_FAILED: the gate did not finish within 1 s"),
                        proc.stdout)
        self.assertEqual(proc.stderr, b"")
        self.assertLess(elapsed, 4.0)

    def test_a_stuck_gate_is_a_block_through_the_guard(self):
        root = self.stuck_root(self.HANG)
        self.review()
        started = time.monotonic()
        proc = subprocess.run(["sh", os.path.join(root, "hooks", "guard.sh"), "money"],
                              input=json.dumps(self.place()).encode("utf-8"),
                              env=self.env(CLAUDE_PLUGIN_ROOT=root, ROBINHOOD_SKILLS_GATE_DEADLINE_SECONDS="1"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        elapsed = time.monotonic() - started
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertIn(b"confirm gate refused: GATE_FAILED", proc.stderr)
        self.assertLess(elapsed, 4.0)

    def test_the_deadline_sits_well_inside_the_money_hook_timeout(self):
        with open(os.path.join(REPO, "hooks", "hooks.json"), "r", encoding="utf-8") as handle:
            hooks = json.load(handle)["hooks"]["PreToolUse"]
        timeouts = [h["timeout"] for entry in hooks for h in entry["hooks"] if h["command"].endswith(" money")]
        self.assertTrue(timeouts)
        self.assertLessEqual(gate_module.DEADLINE_SECONDS + 5, min(timeouts))

    def test_the_environment_can_only_shorten_the_deadline(self):
        d = gate_module.deadline_seconds
        key = gate_module.DEADLINE_ENV
        self.assertEqual(d({}), gate_module.DEADLINE_SECONDS)
        self.assertEqual(d({key: "100"}), gate_module.DEADLINE_SECONDS)
        self.assertEqual(d({key: "0.5"}), 0.5)
        self.assertEqual(d({key: "0"}), gate_module.DEADLINE_MIN)
        self.assertEqual(d({key: "-3"}), gate_module.DEADLINE_MIN)
        for junk in ("abc", "nan", "inf", ""):
            self.assertLessEqual(d({key: junk}), gate_module.DEADLINE_SECONDS)

    def test_an_abnormal_child_exit_is_gate_failed(self):
        code, line = gate_module.supervised(lambda: os._exit(7), 5)
        self.assertEqual(code, 4)
        self.assertTrue(line.startswith("GATE_FAILED:"), line)
        code, line = gate_module.supervised(lambda: (0, ""), 5)
        self.assertEqual(code, 4)
        code, line = gate_module.supervised(lambda: (3, "NO_MATCHING_REVIEW: x"), 5)
        self.assertEqual((code, line), (3, "NO_MATCHING_REVIEW: x"))


# ==================================================================== a tampered state dir

@unittest.skipUnless(hasattr(os, "mkfifo"), "needs mkfifo")
class TamperedState(GateCase):
    """A FIFO planted where the gate expects a state file blocks any open() of it. The gate opens
    state files non-blocking and refuses anything but a regular file, at once."""

    def fifo(self, name):
        path = os.path.join(self.state, name)
        if os.path.lexists(path):
            os.remove(path)
        os.mkfifo(path)

    def test_fifo_at_refids(self):
        self.review()
        self.fifo("refids.json")
        self.assert_deny(self.place(), "GATE_FAILED", contains=("refids.json",))
        self.assertLess(self.elapsed, 3.0)

    def test_fifo_at_ledger(self):
        self.review()
        self.fifo("ledger.jsonl")
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW")
        self.assertLess(self.elapsed, 3.0)

    def test_fifo_at_salt(self):
        self.review()
        self.fifo("salt")
        self.assert_deny(self.place(), "NO_MATCHING_REVIEW")
        self.assertLess(self.elapsed, 3.0)

    def test_fifo_at_servers_txt(self):
        self.review()
        self.fifo("servers.txt")
        self.assert_deny(self.place(tool="mcp__%s__place_equity_order" % UUID_SERVER), "NOT_ROBINHOOD_SERVER")
        self.assertLess(self.elapsed, 3.0)

    def test_read_regular_refuses_non_files(self):
        os.makedirs(self.state)
        self.fifo("pipe")

        def stuck(signum, frame):
            raise AssertionError("read_regular blocked on a FIFO")

        previous = signal.signal(signal.SIGALRM, stuck)  # a regression fails here instead of hanging
        signal.alarm(3)
        try:
            with self.assertRaises(OSError):
                gate_module.read_regular(os.path.join(self.state, "pipe"))
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        with self.assertRaises(OSError):
            gate_module.read_regular(self.state)
        with self.assertRaises(FileNotFoundError):
            gate_module.read_regular(os.path.join(self.state, "absent"))


if __name__ == "__main__":
    unittest.main()
