"""Tests for skills/robinhood-trading/scripts/doctor.py (guard self-test, connector inventory, capability routing).

Unofficial; not affiliated with Robinhood Markets, Inc. Stdlib only. The self-test cases run small stub
guard scripts written to a temporary directory; nothing here calls an MCP tool or the network.
"""

import ast
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "robinhood-trading", "scripts")
SCRIPT = os.path.join(SCRIPTS, "doctor.py")
CLASSES = os.path.join(ROOT, "connector", "tool-classes.json")
REAL_GUARD = os.path.join(ROOT, "hooks", "guard.sh")
sys.path.insert(0, SCRIPTS)

import doctor  # noqa: E402

HAVE_SH = shutil.which("sh") is not None
FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "ftplib", "smtplib", "ssl", "asyncio"}


def run_cli(args, payload=None):
    proc = subprocess.run([sys.executable, SCRIPT] + args, input=(json.dumps(payload) if payload is not None else ""),
                          capture_output=True, text=True, timeout=60, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def classes_tool_names():
    with open(CLASSES, encoding="utf-8") as fh:
        return [t["name"] for t in json.load(fh)["tools"]]


class StubGuards(unittest.TestCase):
    """Writes throwaway guard scripts; each test gets a fresh directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-doctor-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stub(self, body, name="guard.sh"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n" + textwrap.dedent(body))
        os.chmod(path, stat.S_IRWXU)
        return path


@unittest.skipUnless(HAVE_SH, "needs a POSIX sh")
class SelftestOpTests(StubGuards):
    def test_guard_that_exits_2_is_active(self):
        guard = self.stub("""\
            cat >/dev/null
            echo "robinhood-trading guard: live order placement is disabled (simulate-only). Nothing was placed." >&2
            exit 2
            """)
        out = doctor.run("selftest", {"guard_path": guard, "order_mode_line_seen": True})
        self.assertTrue(out["ok"])
        self.assertEqual("active", out["guard"])
        self.assertEqual(2, out["exit_code"])
        self.assertIn("Nothing was placed", out["stderr_head"])
        self.assertEqual("Guard self-test: blocked a synthetic place_equity_order (exit 2).", out["summary"])
        self.assertTrue(out["never_calls_mcp"])

    def test_guard_that_exits_0_failed(self):
        guard = self.stub("cat >/dev/null\nexit 0\n")
        out = doctor.run("selftest", {"guard_path": guard})
        self.assertEqual("FAILED", out["guard"])
        self.assertEqual(0, out["exit_code"])
        self.assertIn("expected exit 2", out["results"][0]["problem"])
        self.assertIn("FAILED", out["summary"])

    def test_guard_that_exits_1_failed(self):
        # exit 1 is a non-blocking error in Claude Code: the call would go through.
        guard = self.stub("cat >/dev/null\necho oops >&2\nexit 1\n")
        out = doctor.run("selftest", {"guard_path": guard})
        self.assertEqual("FAILED", out["guard"])
        self.assertEqual(1, out["exit_code"])

    def test_guard_that_prints_allow_failed(self):
        guard = self.stub("""\
            cat >/dev/null
            printf '%s\\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}'
            exit 0
            """)
        out = doctor.run("selftest", {"guard_path": guard})
        self.assertEqual("FAILED", out["guard"])
        self.assertIn("allow", out["results"][0]["problem"])

    def test_missing_guard_is_not_found(self):
        out = doctor.run("selftest", {"guard_path": os.path.join(self.tmp, "nope", "guard.sh")})
        self.assertEqual("not_found", out["guard"])
        self.assertIsNone(out["exit_code"])
        self.assertIn("advised only", out["summary"])

    def test_payload_is_the_synthetic_place_event_in_money_mode(self):
        capture = os.path.join(self.tmp, "capture.json")
        guard = self.stub("""\
            cat >"%s"
            printf '%%s' "$1" >>"%s.mode"
            exit 2
            """ % (capture, capture))
        out = doctor.run("selftest", {"guard_path": guard, "order_mode_line_seen": True})
        self.assertEqual("active", out["guard"])
        with open(capture, encoding="utf-8") as fh:
            event = json.load(fh)
        self.assertEqual({"hook_event_name": "PreToolUse", "tool_name": "mcp__selftest__place_equity_order",
                          "tool_input": {}, "session_id": "selftest", "permission_mode": "default"}, event)
        with open(capture + ".mode", encoding="utf-8") as fh:
            self.assertEqual("money", fh.read())

    def test_environment_is_scrubbed_of_plugin_options(self):
        # A guard that would pass an order when confirm mode is on must still block during the self-test.
        guard = self.stub("""\
            cat >/dev/null
            if [ -n "${CLAUDE_PLUGIN_OPTION_ORDER_MODE:-}" ] || [ -n "${CLAUDE_PLUGIN_OPTION_GUARD_EXEMPT_SERVERS:-}" ]; then
              exit 0
            fi
            exit 2
            """)
        env = {"CLAUDE_PLUGIN_OPTION_ORDER_MODE": "confirm", "CLAUDE_PLUGIN_OPTION_GUARD_EXEMPT_SERVERS": "selftest"}
        with mock.patch.dict(os.environ, env):
            out = doctor.run("selftest", {"guard_path": guard, "order_mode_line_seen": True})
        self.assertEqual("active", out["guard"])

    def test_state_dir_is_temporary_and_deleted(self):
        record = os.path.join(self.tmp, "state-path.txt")
        guard = self.stub("""\
            cat >/dev/null
            mkdir -p "$ROBINHOOD_SKILLS_STATE/audit"
            echo x >"$ROBINHOOD_SKILLS_STATE/audit/blocked.jsonl"
            printf '%%s' "$ROBINHOOD_SKILLS_STATE" >"%s"
            exit 2
            """ % record)
        real_state = os.path.join(self.tmp, "real-state")
        with mock.patch.dict(os.environ, {"ROBINHOOD_SKILLS_STATE": real_state}):
            out = doctor.run("selftest", {"guard_path": guard, "order_mode_line_seen": True})
        self.assertEqual("active", out["guard"])
        with open(record, encoding="utf-8") as fh:
            used = fh.read()
        self.assertNotEqual(real_state, used)
        self.assertFalse(os.path.exists(used), "the self-test state directory must be deleted")
        self.assertFalse(os.path.exists(real_state), "the user's real state directory must not be touched")

    def test_timeout_is_failed(self):
        guard = self.stub("cat >/dev/null\nsleep 5\nexit 2\n")
        out = doctor.run("selftest", {"guard_path": guard, "timeout_s": 1})
        self.assertEqual("FAILED", out["guard"])
        self.assertIsNone(out["exit_code"])
        self.assertIn("did not finish", out["results"][0]["problem"])

    def test_several_tools_all_must_block(self):
        guard = self.stub("""\
            input=$(cat)
            case "$input" in
              *exercise_option*) exit 0 ;;
            esac
            exit 2
            """)
        tools = ["place_equity_order", "place_advanced_order", "exercise_option"]
        out = doctor.run("selftest", {"guard_path": guard, "tools": tools})
        self.assertEqual("FAILED", out["guard"])
        self.assertEqual(tools, [r["tool"] for r in out["results"]])
        self.assertEqual([True, True, False], [r["blocked"] for r in out["results"]])
        self.assertIn("exercise_option", out["summary"])
        ok = doctor.run("selftest", {"guard_path": self.stub("cat >/dev/null\nexit 2\n", "g2.sh"),
                                     "tools": tools, "order_mode_line_seen": True})
        self.assertEqual("active", ok["guard"])
        self.assertIn("exit 2 each", ok["summary"])

    def test_unset_variable_in_path_falls_back(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RH_DOCTOR_TEST_UNSET", None)
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            out = doctor.run("selftest", {"guard_path": "${RH_DOCTOR_TEST_UNSET}/hooks/guard.sh"})
        self.assertTrue(out["ok"])
        self.assertIn("unset variable", out["guard_path_source"])
        self.assertTrue(out["guard_path"].endswith(os.path.join("hooks", "guard.sh")))

    def test_plugin_root_variable_is_used(self):
        os.makedirs(os.path.join(self.tmp, "hooks"))
        path = os.path.join(self.tmp, "hooks", "guard.sh")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\ncat >/dev/null\nexit 2\n")
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": self.tmp}):
            out = doctor.run("selftest", {"order_mode_line_seen": True})
            bare = doctor.run("selftest", {})
        self.assertEqual("active", out["guard"])
        self.assertEqual(path, out["guard_path"])
        # CLAUDE_PLUGIN_ROOT alone is not evidence that the plugin's hooks are registered.
        self.assertEqual("script_only", bare["guard"])
        self.assertEqual(path, bare["guard_path"])

    def test_no_sh_is_failed_not_active(self):
        guard = self.stub("cat >/dev/null\nexit 2\n")
        with mock.patch.object(doctor.shutil, "which", return_value=None):
            out = doctor.run("selftest", {"guard_path": guard})
        self.assertEqual("FAILED", out["guard"])
        self.assertIn("fails open", out["note"])

    def test_blocking_script_without_session_line_is_script_only(self):
        guard = self.stub("cat >/dev/null\nexit 2\n")
        for payload in ({"guard_path": guard}, {"guard_path": guard, "order_mode_line_seen": False},
                        {"guard_path": guard, "order_mode_line_seen": None}):
            with self.subTest(payload=payload):
                out = doctor.run("selftest", payload)
                self.assertTrue(out["ok"])
                self.assertEqual("script_only", out["guard"])
                self.assertFalse(out["order_mode_line_seen"])
                self.assertEqual(2, out["exit_code"])
                self.assertIn("no order-guard hook is confirmed", out["summary"])
                self.assertIn("advised only", out["summary"])
                self.assertIn("first-run test", out["note"])

    def test_failing_script_is_failed_even_with_session_line(self):
        guard = self.stub("cat >/dev/null\nexit 0\n")
        out = doctor.run("selftest", {"guard_path": guard, "order_mode_line_seen": True})
        self.assertEqual("FAILED", out["guard"])

    def test_session_line_flag_must_be_boolean(self):
        out = doctor.run("selftest", {"guard_path": "/x/guard.sh", "order_mode_line_seen": "yes"})
        self.assertFalse(out["ok"])
        self.assertEqual("order_mode_line_seen", out["errors"][0]["field"])

    def test_missing_guard_note_does_not_equate_a_script_with_the_plugin(self):
        out = doctor.run("selftest", {"guard_path": os.path.join(self.tmp, "nope", "guard.sh")})
        self.assertIn("proves nothing about hook registration", out["note"])
        seen = doctor.run("selftest", {"guard_path": os.path.join(self.tmp, "nope", "guard.sh"),
                                       "order_mode_line_seen": True})
        self.assertEqual("not_found", seen["guard"])
        self.assertIn("could not run", seen["summary"])

    def test_rejects_non_money_tools(self):
        out = doctor.run("selftest", {"guard_path": "/x/guard.sh", "tools": ["get_accounts"]})
        self.assertFalse(out["ok"])
        self.assertEqual("BAD_VALUE", out["errors"][0]["code"])


@unittest.skipUnless(HAVE_SH and os.path.isfile(REAL_GUARD), "hooks/guard.sh is not present in this checkout")
class RealGuardTests(unittest.TestCase):
    def test_real_guard_blocks_every_money_tool(self):
        tools = ["place_equity_order", "place_option_order", "place_crypto_order", "place_advanced_order",
                 "exercise_option", "replace_option_order"]
        out = doctor.run("selftest", {"guard_path": REAL_GUARD, "tools": tools, "order_mode_line_seen": True})
        self.assertEqual("active", out["guard"], json.dumps(out, indent=1))

    def test_repo_tree_fallback_without_session_line_is_not_active(self):
        # Codex and Gemini installs carry hooks/guard.sh in the tree, but no hook calls it there.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            out = doctor.run("selftest", {})
            literal = doctor.run("selftest", {"guard_path": "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh"})
        for result in (out, literal):
            self.assertEqual(os.path.normpath(REAL_GUARD), result["guard_path"])
            self.assertIn("plugin tree next to this skill", result["guard_path_source"])
            self.assertEqual("script_only", result["guard"], json.dumps(result, indent=1))
            self.assertIn("advised only", result["summary"])
            self.assertNotIn("Guard self-test: blocked", result["summary"])


class InventoryOpTests(unittest.TestCase):
    def test_full_snapshot_matches(self):
        names = classes_tool_names()
        out = doctor.run("inventory", {"seen_tools": names, "inventory_path": CLASSES})
        self.assertTrue(out["ok"], out)
        self.assertEqual("match", out["status"])
        self.assertEqual(81, out["known"])
        self.assertEqual([], out["new"])
        self.assertEqual([], out["missing"])
        self.assertEqual("2026-09-21", out["snapshot_date"])
        self.assertIn("81 known tools visible · 0 new · 0 missing", out["summary"])

    def test_prefixed_names_are_normalized(self):
        names = ["mcp__00000000-0000-4000-8000-000000000000__" + n for n in classes_tool_names()]
        names += ["mcp__plugin_unofficial-rh-connector_robinhood__get_accounts"]
        out = doctor.run("inventory", {"seen_tools": names, "inventory_path": CLASSES})
        self.assertEqual("match", out["status"])
        self.assertEqual(81, out["seen_count"])

    def test_new_money_like_tools_are_classified(self):
        names = classes_tool_names()
        names.remove("get_alert_log")
        names += ["transfer_cash", "replace_option_order", "place_future_order", "get_futures_quotes"]
        out = doctor.run("inventory", {"seen_tools": names, "inventory_path": CLASSES})
        self.assertEqual("drift", out["status"])
        self.assertEqual(["get_futures_quotes", "place_future_order", "replace_option_order", "transfer_cash"],
                         out["new"])
        self.assertEqual(["get_alert_log"], out["missing"])
        self.assertEqual(["place_future_order", "replace_option_order", "transfer_cash"], out["new_money_like"])
        self.assertEqual(["place_future_order", "replace_option_order"], out["new_money_like_guarded"])
        self.assertEqual(["transfer_cash"], out["new_money_like_unguarded"])
        self.assertEqual(["replace_option_order"], out["previously_absent_now_visible"])
        self.assertTrue(any("classify layer" in a for a in out["advice"]))

    def test_every_money_verb_is_money_like(self):
        verbs = ["place", "exercise", "replace", "submit", "execute", "transfer", "withdraw", "deposit", "stake",
                 "unstake", "convert", "send", "buy", "sell", "trade", "liquidate", "lend", "borrow"]
        seen = ["get_accounts"] + ["%s_thing" % v for v in verbs]
        out = doctor.run("inventory", {"seen_tools": seen, "inventory_path": CLASSES})
        self.assertEqual(sorted("%s_thing" % v for v in verbs), out["new_money_like"])
        self.assertEqual(["exercise_thing", "place_thing", "replace_thing"], out["new_money_like_guarded"])

    def test_generated_inventory_format_is_read(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        try:
            import sync_shared  # WP-B's generator for scripts/tool_inventory.json
        except ImportError:  # pragma: no cover - the generator ships with the repo
            self.skipTest("tools/sync_shared.py not importable")
        with open(CLASSES, "rb") as fh:
            generated = sync_shared.render_generated("names_classes", fh.read(), "connector/tool-classes.json")
        inventory = json.loads(generated.decode("utf-8"))
        out = doctor.run("inventory", {"seen_tools": classes_tool_names(), "inventory": inventory})
        self.assertEqual("match", out["status"])
        self.assertEqual(81, out["known"])
        self.assertEqual("2026-09-21", out["snapshot_date"])
        out = doctor.run("inventory", {"seen_tools": ["get_accounts", "replace_option_order"], "inventory": inventory})
        self.assertEqual(["replace_option_order"], out["previously_absent_now_visible"])

    def test_empty_and_junk_input(self):
        out = doctor.run("inventory", {"seen_tools": [], "inventory_path": CLASSES})
        self.assertFalse(out["ok"])
        self.assertEqual("NO_TOOLS_SEEN", out["errors"][0]["code"])
        out = doctor.run("inventory", {"seen_tools": ["get_accounts", 7, "", "Not A Tool!"], "inventory_path": CLASSES})
        self.assertTrue(out["ok"])
        self.assertEqual(3, len(out["ignored"]))
        out = doctor.run("inventory", {"inventory_path": CLASSES})
        self.assertEqual("MISSING_FIELD", out["errors"][0]["code"])

    def test_missing_inventory_file(self):
        out = doctor.run("inventory", {"seen_tools": ["get_accounts"], "inventory_path": "/nonexistent/inv.json"})
        self.assertFalse(out["ok"])
        self.assertEqual("INVENTORY_MISSING", out["errors"][0]["code"])


class CapabilityOpTests(unittest.TestCase):
    def cap(self, strategy, **account):
        return doctor.run("capability", {"account": account, "strategy": strategy})

    def test_cash_l3_routes_through_limited_margin_first(self):
        out = self.cap("vertical_spread", type="cash", option_level="option_level_2", retirement=False)
        self.assertFalse(out["allowed"])
        self.assertEqual(["get_limited_margin_upgrade_info", "user completes the limited-margin upgrade",
                          "re-fetch get_accounts", "get_option_level_upgrade_info"], out["route"])
        self.assertEqual(["get_option_level_upgrade_info", "review_option_order"], out["do_not_call"])
        self.assertEqual({"option_level": "option_level_3", "account_types": ["margin", "limited_margin"],
                          "retirement_allowed": False}, out["required"])

    def test_cash_account_already_l3_needs_only_limited_margin(self):
        out = self.cap("iron_condor", type="cash", option_level="option_level_3", retirement=False)
        self.assertEqual(["get_limited_margin_upgrade_info", "user completes the limited-margin upgrade",
                          "re-fetch get_accounts"], out["route"])

    def test_margin_l2_needs_only_the_options_upgrade(self):
        out = self.cap("debit_put_spread", type="margin", option_level="option_level_2", retirement=False)
        self.assertEqual("vertical_spread", out["strategy"])
        self.assertFalse(out["allowed"])
        self.assertEqual(["get_option_level_upgrade_info", "user completes the options application",
                          "re-fetch get_accounts"], out["route"])
        self.assertIn("get_limited_margin_upgrade_info", out["do_not_call"])

    def test_sufficient_level_never_routes_to_an_upgrade(self):
        for strategy, acct in (("long_call", {"type": "cash", "option_level": "option_level_2"}),
                               ("covered_call", {"type": "margin", "option_level": "option_level_3"}),
                               ("calendar", {"type": "limited_margin", "option_level": "option_level_3",
                                             "retirement": False})):
            with self.subTest(strategy=strategy):
                out = self.cap(strategy, **acct)
                self.assertTrue(out["allowed"], out)
                self.assertEqual([], out["route"])
                self.assertIn("get_option_level_upgrade_info", out["do_not_call"])

    def test_no_options_access_variants(self):
        for level in (None, "", "option_level_0", "option_level_1"):
            with self.subTest(level=level):
                out = self.cap("long_put", type="cash", option_level=level)
                self.assertFalse(out["allowed"])
                self.assertEqual("get_option_level_upgrade_info", out["route"][0])
                self.assertIn("review_option_order", out["do_not_call"])

    def test_retirement_blocks_multi_leg_and_offers_two_single_legs_for_a_roll(self):
        out = self.cap("roll_single_order", type="cash", option_level="option_level_2", retirement=True)
        self.assertFalse(out["allowed"])
        self.assertEqual([], out["route"])
        self.assertIn("review_option_order", out["do_not_call"])
        self.assertTrue(out["alternative"]["allowed"])
        self.assertIn("legging risk", out["alternative"]["risk"])
        out = self.cap("iron_condor", type="margin", option_level="option_level_3", retirement=True)
        self.assertFalse(out["allowed"])
        self.assertNotIn("alternative", out)

    def test_roll_on_l2_cash_offers_alternative(self):
        out = self.cap("roll", type="cash", option_level="option_level_2", retirement=False)
        self.assertEqual("roll_single_order", out["strategy"])
        self.assertTrue(out["alternative"]["allowed"])

    def test_retirement_inferred_from_brokerage_account_type(self):
        out = self.cap("vertical_spread", type="margin", option_level="option_level_3", brokerage_account_type="roth_ira")
        self.assertFalse(out["allowed"])
        self.assertIn("inferred", out["note"])
        out = self.cap("vertical_spread", type="margin", option_level="option_level_3",
                       brokerage_account_type="individual")
        self.assertTrue(out["allowed"])

    def test_unknown_facts_are_never_clear(self):
        out = self.cap("vertical_spread", type="margin", option_level="option_level_3")
        self.assertFalse(out["allowed"])
        self.assertEqual(["account.retirement"], out["unknowns"])
        out = self.cap("vertical_spread", option_level="option_level_3", retirement=False)
        self.assertEqual(["account.type"], out["unknowns"])
        self.assertIn("get_option_level_upgrade_info", out["do_not_call"])

    def test_crypto(self):
        self.assertTrue(self.cap("crypto", has_crypto_account=True)["allowed"])
        absent = self.cap("crypto", has_crypto_account=False)
        self.assertEqual("get_crypto_account_onboarding_info", absent["route"][0])
        unknown = self.cap("crypto")
        self.assertFalse(unknown["allowed"])
        self.assertEqual(["account.has_crypto_account"], unknown["unknowns"])
        self.assertIn("never infer", unknown["note"])
        self.assertIn("New York", unknown["note"])

    def test_non_agentic_account_is_not_reviewable(self):
        out = self.cap("long_call", type="margin", option_level="option_level_3", agentic_allowed=False)
        self.assertFalse(out["reviewable"])
        self.assertIn("review_option_order", out["do_not_call"])
        self.assertIn("read-only to agents", out["note"])

    def test_bad_inputs(self):
        self.assertEqual("BAD_VALUE", self.cap("vertical_spread", type="ira")["errors"][0]["code"])
        self.assertEqual("BAD_VALUE", self.cap("long_call", type="cash", option_level="level2")["errors"][0]["code"])
        self.assertEqual("BAD_VALUE", self.cap("naked_call", type="margin")["errors"][0]["code"])
        out = doctor.run("capability", {"strategy": "long_call"})
        self.assertEqual("MISSING_FIELD", out["errors"][0]["code"])


class ContractTests(unittest.TestCase):
    def test_cli_selftest_and_schema(self):
        code, out, _ = run_cli(["--selftest"])
        self.assertEqual(0, code, out)
        self.assertTrue(json.loads(out)["ok"])
        code, out, _ = run_cli(["--schema"])
        self.assertEqual(0, code)
        schema = json.loads(out)
        self.assertEqual({"selftest", "inventory", "capability"}, set(schema["ops"]))
        for op in schema["ops"].values():
            self.assertIn("input", op)
            self.assertIn("output", op)

    def test_cli_emits_json_on_errors(self):
        code, out, _ = run_cli(["nope"], {})
        self.assertEqual(0, code)
        self.assertEqual("UNKNOWN_OP", json.loads(out)["errors"][0]["code"])
        proc = subprocess.run([sys.executable, SCRIPT, "capability"], input="{not json", capture_output=True, text=True,
                              check=False)
        self.assertEqual(0, proc.returncode)
        self.assertEqual("BAD_JSON", json.loads(proc.stdout)["errors"][0]["code"])
        code, out, _ = run_cli([])
        self.assertEqual("MISSING_OP", json.loads(out)["errors"][0]["code"])

    def test_cli_capability_round_trip(self):
        code, out, _ = run_cli(["capability"], {"account": {"type": "cash", "option_level": "option_level_2",
                                                            "retirement": False}, "strategy": "iron_condor"})
        self.assertEqual(0, code)
        self.assertEqual("get_limited_margin_upgrade_info", json.loads(out)["route"][0])

    def test_no_network_imports_and_py39_syntax(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, feature_version=(3, 9))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported & FORBIDDEN_IMPORTS, imported & FORBIDDEN_IMPORTS)
        for word in ("curl", "wget"):
            self.assertNotIn(word, source)

    def test_outputs_never_contain_allow(self):
        for op, payload in (("inventory", {"seen_tools": classes_tool_names(), "inventory_path": CLASSES}),
                            ("capability", {"account": {"type": "cash"}, "strategy": "crypto"}),
                            ("selftest", {"guard_path": "/nonexistent/guard.sh"})):
            text = json.dumps(doctor.run(op, payload))
            self.assertNotRegex(text, r'"permissionDecision"\s*:\s*"allow"')


if __name__ == "__main__":
    unittest.main()
