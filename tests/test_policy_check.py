"""Tests for shared/scripts/policy_check.py (soft [policy] limits)."""

import ast
import copy
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "shared", "scripts")
NAME = "policy_check"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import policy_check  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}


def strict(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and strict(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            strict(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


with open(os.path.join(GOLDEN, "run.json"), encoding="utf-8") as _fh:
    DOC = json.load(_fh)


def build(case):
    data = copy.deepcopy(case["input"])
    data["order"] = copy.deepcopy(DOC["orders"][case["order"]])
    return data


class GoldenTests(unittest.TestCase):
    def test_golden_cases(self):
        self.assertGreater(len(DOC["cases"]), 30)
        for case in DOC["cases"]:
            with self.subTest(case=case["name"]):
                out = policy_check.run("run", build(case))
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1))

    def test_every_rule_has_a_failing_and_a_passing_golden(self):
        failing, passing = set(), set()
        for case in DOC["cases"]:
            out = policy_check.run("run", build(case))
            for check in out["checks"]:
                (passing if check["pass"] else failing).add(check["rule"])
        self.assertEqual(set(policy_check.RULES), failing)
        self.assertEqual(set(policy_check.RULES), passing)


class BehaviourTests(unittest.TestCase):
    def test_policy_sha_is_order_independent_and_changes_with_values(self):
        a = policy_check.policy_sha({"max_order_usd": "500", "allow_crypto": False})
        b = policy_check.policy_sha({"allow_crypto": False, "max_order_usd": "500"})
        c = policy_check.policy_sha({"allow_crypto": False, "max_order_usd": "501"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertRegex(a, r"^sha256:[0-9a-f]{64}$")

    def test_violation_codes_match_the_confirm_gate_vocabulary(self):
        codes = set()
        for case in DOC["cases"]:
            out = policy_check.run("run", build(case))
            codes.update(v["code"] for v in out["violations"])
        self.assertEqual({"POLICY_CAP", "POLICY_DENY", "POLICY_UNKNOWN", "BAD_POLICY_VALUE"}, codes)

    def test_input_errors(self):
        self.assertEqual("MISSING_FIELD", policy_check.run("run", {})["errors"][0]["code"])
        out = policy_check.run("run", {"order": {"tool": "get_accounts", "params": {}}})
        self.assertEqual("UNKNOWN_TOOL", out["errors"][0]["code"])
        out = policy_check.run("run", {"policy": "strict", "order": DOC["orders"]["pltr_buy"]})
        self.assertEqual("BAD_INPUT", out["errors"][0]["code"])

    def test_library_entry_point_and_note(self):
        out = policy_check.check({"order": DOC["orders"]["pltr_buy"], "policy": {"max_order_usd": "1"},
                                  "estimate_usd": "312.40"})
        self.assertFalse(out["pass"])
        self.assertIn("Robinhood does not enforce them", out["note"])


class ContractTests(unittest.TestCase):
    def cli(self, *args, **kw):
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=kw.get("stdin", ""),
                              capture_output=True, text=True, timeout=120)

    def test_selftest_exits_zero(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)

    def test_schema_prints_json_schemas(self):
        doc = json.loads(self.cli("--schema").stdout)
        self.assertEqual(["run"], sorted(doc["ops"]))
        self.assertIn("$schema", doc["ops"]["run"]["input"])
        self.assertEqual(policy_check.RULES, doc["rules"])

    def test_cli_round_trip_and_errors(self):
        res = self.cli("run", stdin=json.dumps({"order": DOC["orders"]["pltr_buy"], "policy": {}}))
        self.assertEqual(0, res.returncode)
        self.assertTrue(json.loads(res.stdout)["pass"])
        self.assertEqual("BAD_JSON", json.loads(self.cli("run", stdin="{{").stdout)["errors"][0]["code"])
        self.assertEqual("UNKNOWN_OP", json.loads(self.cli("enforce", stdin="{}").stdout)["errors"][0]["code"])

    def test_stdlib_only_no_network_and_py39_syntax(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, feature_version=(3, 9))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported & FORBIDDEN_IMPORTS, imported)
        self.assertNotIn("canon", imported)  # synced alone into the report-card skill


if __name__ == "__main__":
    unittest.main()
