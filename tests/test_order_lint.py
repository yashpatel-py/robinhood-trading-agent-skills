"""Tests for shared/scripts/order_lint.py.

Every error and warning code has at least one triggering case and one passing case
(tests/golden/order_lint/codes.json), including the OCO boundaries: exactly 0.25% from the
market passes and 0.249% fails; legs $0.10 apart pass and $0.09 apart fail.
"""

import ast
import copy
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "shared", "scripts")
NAME = "order_lint"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import order_lint  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}


def load(name):
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as fh:
        return json.load(fh)


CODES = load("codes.json")
SCENARIOS = load("scenarios.json")["cases"]


def merge(target, patch):
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        else:
            target[key] = copy.deepcopy(value)


def build(case):
    """Apply a golden patch to its base order."""
    base = copy.deepcopy(CODES["bases"][case["base"]])
    order = {"tool": case.get("tool", base["tool"]), "params": base["params"], "provenance": base["provenance"],
             "context": base["context"]}
    merge(order["params"], case.get("params", {}))
    for key in case.get("remove", []):
        order["params"].pop(key, None)
    merge(order["context"], case.get("context", {}))
    if "provenance" in case:
        order["provenance"] = copy.deepcopy(case["provenance"])
    return order


def codes_of(out, kind):
    return [f["code"] for f in out[kind]]


def strict(expected, actual):
    """Subset for dicts; lists must match in length and element-wise."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and strict(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            strict(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


class CodeCoverageTests(unittest.TestCase):
    def test_every_error_code_has_a_trigger_and_a_pass(self):
        covered = {c["code"] for c in CODES["errors"]}
        self.assertEqual(set(order_lint.ERROR_CODES), covered)

    def test_every_warning_code_has_a_trigger_and_a_pass(self):
        covered = {c["code"] for c in CODES["warnings"]}
        self.assertEqual(set(order_lint.WARNING_CODES), covered)

    def test_bases_are_clean(self):
        for name, base in CODES["bases"].items():
            with self.subTest(base=name):
                out = order_lint.run("lint", base)
                self.assertTrue(out["ok"], out)
                self.assertEqual([], out["errors"], out["errors"])
                self.assertTrue(out["valid"])

    def run_pairs(self, entries, kind):
        for entry in entries:
            code = entry["code"]
            with self.subTest(code=code, side="trigger"):
                out = order_lint.run("lint", build(entry["trigger"]))
                self.assertTrue(out["ok"], out)
                self.assertIn(code, codes_of(out, kind), json.dumps(out[kind]))
                if kind == "errors":
                    self.assertFalse(out["valid"])
            with self.subTest(code=code, side="pass"):
                out = order_lint.run("lint", build(entry["pass"]))
                self.assertTrue(out["ok"], out)
                self.assertNotIn(code, codes_of(out, kind), json.dumps(out[kind]))

    def test_error_pairs(self):
        self.run_pairs(CODES["errors"], "errors")

    def test_warning_pairs(self):
        self.run_pairs(CODES["warnings"], "warnings")


class OcoBoundaryTests(unittest.TestCase):
    def lint(self, tp, sl, market="100.00"):
        order = build({"base": "oco_sell", "params": {"take_profit_limit_price": tp, "stop_loss_stop_price": sl},
                       "context": {"market_price": market, "bid": None, "ask": None}})
        return codes_of(order_lint.run("lint", order), "errors")

    def test_distance_exactly_quarter_percent_passes(self):
        self.assertNotIn("MIN_DISTANCE_0_25PCT", self.lint("100.25", "99.75"))

    def test_distance_0_249_percent_fails(self):
        self.assertIn("MIN_DISTANCE_0_25PCT", self.lint("100.249", "99.75"))
        self.assertIn("MIN_DISTANCE_0_25PCT", self.lint("100.25", "99.751"))

    def test_gap_ten_cents_passes_nine_fails(self):
        self.assertNotIn("MIN_GAP_0_10", self.lint("10.05", "9.95", market="10.00"))
        self.assertIn("MIN_GAP_0_10", self.lint("10.05", "9.96", market="10.00"))

    def test_no_market_price_means_not_checked(self):
        order = build({"base": "oco_sell", "context": {"market_price": None}})
        out = order_lint.run("lint", order)
        self.assertIn("OCO 0.25% distance and trigger checks", [n["check"] for n in out["not_checked"]])


class ScenarioTests(unittest.TestCase):
    def test_scenarios(self):
        self.assertGreaterEqual(len(SCENARIOS), 12)
        for case in SCENARIOS:
            with self.subTest(case=case["name"]):
                out = order_lint.run("lint", case["input"])
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1, ensure_ascii=False))
                for fragment in case.get("notes_contain", []):
                    self.assertTrue(any(fragment in n for n in out["notes"]), out["notes"])
                for key in case.get("place_params_absent", []):
                    self.assertNotIn(key, out["place_params"])

    def test_output_never_contains_a_full_account_number(self):
        for case in SCENARIOS:
            out = order_lint.run("lint", case["input"])
            text = json.dumps(out)
            self.assertNotIn("demo-X4F1", text, case["name"])
            self.assertNotIn("demo-5555", text, case["name"])

    def test_fingerprint_matches_canon_for_review_and_place(self):
        import canon
        base = CODES["bases"]["option_spread"]
        out = order_lint.run("lint", base)
        self.assertEqual(canon.fingerprint(base["tool"], base["params"])["fingerprint"], out["fingerprint"])
        place = build({"base": "option_spread", "tool": "place_option_order", "remove": ["chain_symbol", "underlying_type"]})
        self.assertEqual(out["fingerprint"], order_lint.run("lint", place)["fingerprint"])
        self.assertEqual(out["fingerprint"][7:13], out["ticket_id"])

    def test_bad_values_are_reported_once(self):
        out = order_lint.run("lint", build({"base": "equity_limit_buy", "params": {"limit_price": "abc"},
                                            "context": {"user_wants_immediate": True}}))
        self.assertEqual(1, codes_of(out, "errors").count("BAD_VALUE_TYPE"))

    def test_provenance_shorthand_for_the_confirm_gate(self):
        base = copy.deepcopy(CODES["bases"]["option_spread"])
        base["provenance"] = "user"
        self.assertNotIn("NO_USER_SOURCE", codes_of(order_lint.run("lint", base), "errors"))
        base["provenance"] = "agent"
        self.assertEqual("BAD_INPUT", order_lint.run("lint", base)["errors"][0]["code"])

    def test_library_entry_point(self):
        out = order_lint.lint(CODES["bases"]["crypto_stop"])
        self.assertTrue(out["valid"])
        self.assertEqual("place_crypto_order", out["place_tool"])


class ContractTests(unittest.TestCase):
    def cli(self, *args, **kw):
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=kw.get("stdin", ""),
                              capture_output=True, text=True, timeout=120)

    def test_selftest_exits_zero(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)

    def test_schema_prints_json_schemas_and_code_lists(self):
        doc = json.loads(self.cli("--schema").stdout)
        self.assertEqual(["lint"], sorted(doc["ops"]))
        self.assertIn("$schema", doc["ops"]["lint"]["input"])
        self.assertIn("$schema", doc["ops"]["lint"]["output"])
        self.assertEqual(order_lint.ERROR_CODES, doc["error_codes"])

    def test_cli_round_trip_and_errors(self):
        res = self.cli("lint", stdin=json.dumps(CODES["bases"]["equity_limit_buy"]))
        self.assertEqual(0, res.returncode)
        self.assertTrue(json.loads(res.stdout)["valid"])
        self.assertEqual("BAD_JSON", json.loads(self.cli("lint", stdin="{").stdout)["errors"][0]["code"])
        self.assertEqual("UNKNOWN_OP", json.loads(self.cli("check", stdin="{}").stdout)["errors"][0]["code"])

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
        self.assertLessEqual(imported, {"copy", "json", "os", "re", "sys", "decimal", "functools", "math", "canon"})


if __name__ == "__main__":
    unittest.main()
