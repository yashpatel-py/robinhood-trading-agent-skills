"""Tests for shared/scripts/options_math.py (payoff, breakevens, P&L)."""

import ast
import json
import os
import subprocess
import sys
import unittest
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "shared", "scripts")
NAME = "options_math"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import options_math  # noqa: E402

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
        self.assertGreater(len(cases), 25)
        for case in cases:
            with self.subTest(case=case["name"]):
                out = options_math.run(case["op"], case["input"])
                if case.get("match") == "exact":
                    self.assertEqual(case["expected"], out)
                else:
                    self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1, ensure_ascii=False))


class SpecChecks(unittest.TestCase):
    """The numbers the build spec names explicitly."""

    def test_long_call_breakeven_and_required_move(self):
        out = options_math.run("payoff", {"legs": [{"type": "call", "side": "buy", "strike": "165", "bid": "6.10",
                                                    "ask": "6.30"}], "quantity": "1", "underlying_price": "161.40"})
        self.assertEqual(["171.30"], out["breakevens"])
        self.assertEqual(6.1, out["required_move_pct"])

    def test_iron_condor_quantity_two_is_eight_contracts(self):
        legs = [{"type": "put", "side": "sell", "strike": "95", "bid": "1.00"},
                {"type": "put", "side": "buy", "strike": "90", "ask": "0.50"},
                {"type": "call", "side": "sell", "strike": "105", "bid": "1.10"},
                {"type": "call", "side": "buy", "strike": "110", "ask": "0.55"}]
        out = options_math.run("payoff", {"legs": legs, "quantity": "2", "underlying_price": "100"})
        self.assertEqual("8", out["total_contracts"])
        self.assertEqual("iron_condor", out["structure"])

    def test_debit_spread_max_gain_is_width_minus_debit(self):
        out = options_math.run("payoff", {"legs": [{"type": "call", "side": "buy", "strike": "165", "ask": "6.30"},
                                                   {"type": "call", "side": "sell", "strike": "170", "bid": "4.10"}],
                                          "quantity": "3", "underlying_price": "161.40"})
        self.assertEqual("840.00", out["max_gain_usd"])  # (5.00 - 2.20) x 100 x 3
        self.assertEqual("660.00", out["max_loss_usd"])

    def test_corrected_trust_example_is_410_not_424(self):
        out = options_math.run("pnl", {"positions": [{"side": "long", "quantity": "2", "open_price": "4.05",
                                                      "bid": "6.10", "ask": "6.25"}]})
        row = out["positions"][0]
        self.assertEqual("410.00", row["pnl_usd"])
        self.assertEqual(50.6, row["pnl_pct"])
        self.assertNotEqual("424.00", row["pnl_usd"])


class PropertyTests(unittest.TestCase):
    def test_payoff_at_breakevens_is_zero_and_signs_flip_for_mirror_positions(self):
        for case in load("run.json"):
            if case["op"] != "payoff" or not case["expected"].get("ok"):
                continue
            inp = case["input"]
            out = options_math.run("payoff", inp)
            if not out["breakevens"]:
                continue
            legs = options_math.parse_legs(inp["legs"])
            net = Decimal(out["net_price"]) * (1 if out["direction"] == "debit" else -1)
            for be in out["breakevens"]:
                value = options_math.payoff_per_unit(legs, Decimal(be), net)
                self.assertLessEqual(abs(value), Decimal("0.01"), (case["name"], be, value))

    def test_short_is_mirror_of_long(self):
        long_leg = {"type": "put", "side": "buy", "strike": "150", "bid": "5.00", "ask": "5.00"}
        short_leg = dict(long_leg, side="sell")
        long_out = options_math.run("payoff", {"legs": [long_leg], "quantity": "1", "underlying_price": "160"})
        short_out = options_math.run("payoff", {"legs": [short_leg], "quantity": "1", "underlying_price": "160"})
        self.assertEqual(long_out["max_loss_usd"], short_out["max_gain_usd"])
        self.assertEqual(long_out["max_gain_usd"], short_out["max_loss_usd"])
        self.assertEqual(long_out["breakevens"], short_out["breakevens"])


class ContractTests(unittest.TestCase):
    def cli(self, *args, **kw):
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=kw.get("stdin", ""),
                              capture_output=True, text=True, timeout=120)

    def test_selftest_exits_zero(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)

    def test_schema_prints_json_schemas_for_every_op(self):
        doc = json.loads(self.cli("--schema").stdout)
        self.assertEqual(sorted(options_math.OPS), sorted(doc["ops"]))
        for pair in doc["ops"].values():
            self.assertIn("$schema", pair["input"])
            self.assertIn("$schema", pair["output"])

    def test_cli_round_trip_and_errors(self):
        res = self.cli("pnl", stdin=json.dumps({"positions": [{"side": "long", "quantity": "1", "open_price": "2.05",
                                                               "bid": "3.30"}]}))
        self.assertEqual(0, res.returncode)
        self.assertEqual("125.00", json.loads(res.stdout)["total_pnl_usd"])
        self.assertEqual("BAD_JSON", json.loads(self.cli("payoff", stdin="{").stdout)["errors"][0]["code"])
        self.assertEqual("UNKNOWN_OP", json.loads(self.cli("greeks", stdin="{}").stdout)["errors"][0]["code"])

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
