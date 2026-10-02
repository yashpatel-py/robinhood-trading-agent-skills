"""Tests for shared/scripts/exposure.py (household concentration)."""

import ast
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "shared", "scripts")
NAME = "exposure"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import exposure  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}


def strict(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and strict(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            strict(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def load(name):
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as fh:
        return json.load(fh)["cases"]


class GoldenTests(unittest.TestCase):
    def test_golden_cases(self):
        cases = load("run.json")
        self.assertGreaterEqual(len(cases), 10)
        for case in cases:
            with self.subTest(case=case["name"]):
                out = exposure.run(case["op"], case["input"])
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1, ensure_ascii=False))


class BehaviourTests(unittest.TestCase):
    def test_percentages_sum_to_about_100(self):
        out = exposure.run("run", load("run.json")[0]["input"])
        total = sum(row["pct"] for row in out["by_symbol"])
        self.assertAlmostEqual(100.0, total, delta=0.2)

    def test_sorted_by_value_descending(self):
        out = exposure.run("run", load("run.json")[0]["input"])
        values = [float(row["value_usd"]) for row in out["by_symbol"]]
        self.assertEqual(sorted(values, reverse=True), values)

    def test_money_is_cents_and_raw_pct_is_kept(self):
        out = exposure.run("run", load("run.json")[0]["input"])
        for row in out["by_symbol"]:
            self.assertRegex(row["value_usd"], r"^-?\d+\.\d{2}$")
            self.assertIsInstance(row["pct"], float)
            self.assertIsInstance(row["pct_raw"], str)

    def test_no_full_account_numbers(self):
        data = {"positions": [{"account_last4": "LONGACCOUNT-9999", "symbol": "KO", "quantity": "1", "price": "70"}]}
        text = json.dumps(exposure.run("run", data))
        self.assertNotIn("LONGACCOUNT", text)
        self.assertIn("9999", text)


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
        self.assertIn("$schema", doc["ops"]["run"]["output"])

    def test_cli_round_trip_and_errors(self):
        res = self.cli("run", stdin=json.dumps({"positions": [{"account_last4": "X4F1", "symbol": "KO", "quantity": "1",
                                                               "price": "70"}]}))
        self.assertEqual(0, res.returncode)
        self.assertEqual("70.00", json.loads(res.stdout)["household_total_usd"])
        self.assertEqual("BAD_JSON", json.loads(self.cli("run", stdin="x").stdout)["errors"][0]["code"])
        self.assertEqual("UNKNOWN_OP", json.loads(self.cli("sum", stdin="{}").stdout)["errors"][0]["code"])

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


if __name__ == "__main__":
    unittest.main()
