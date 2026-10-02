"""Tests for shared/scripts/alert_spec.py (create_alert families, bars, refusals, dedupe)."""

import ast
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "shared", "scripts")
NAME = "alert_spec"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import alert_spec  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}
# The 30 condition types listed in the create_alert schema, verbatim order.
SCHEMA_CONDITIONS = (
    "price_above, price_below, price_crosses, sma_above, sma_below, sma_crosses, ema_above, ema_below, ema_crosses, "
    "vwap_above, vwap_below, vwap_crosses, rsi_above, rsi_below, rsi_crosses, price_above_sma, price_below_sma, "
    "price_crosses_sma, price_above_ema, price_below_ema, price_crosses_ema, price_above_vwap, price_below_vwap, "
    "price_crosses_vwap, price_above_boll_upper, price_below_boll_lower, price_crosses_boll_mid, macd_above_signal, "
    "macd_below_signal, macd_crosses_signal").split(", ")


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
        self.assertGreater(len(cases), 35)
        for case in cases:
            with self.subTest(case=case["name"]):
                out = alert_spec.run(case["op"], case["input"])
                if case.get("match") == "exact":
                    self.assertEqual(case["expected"], out)
                else:
                    self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1))


class FamilyTests(unittest.TestCase):
    def test_vocabulary_matches_the_schema(self):
        self.assertEqual(sorted(SCHEMA_CONDITIONS), sorted(alert_spec.ALL_KINDS))
        self.assertEqual(30, len(set(alert_spec.ALL_KINDS)))

    def intent_for(self, kind):
        fam = alert_spec.indicator_family(kind)
        intent = {"kind": kind}
        if kind in alert_spec.PRICE_KINDS or kind in alert_spec.VALUE_KINDS:
            intent["threshold"] = "50"
        if fam in ("sma", "ema", "rsi"):
            intent.update(period=14, bar="1d")
        elif fam == "boll":
            intent.update(period=20, std_dev=2, ma_type="sma", bar="1d")
        elif fam == "macd":
            intent.update(fast_period=12, slow_period=26, signal_period=9, bar="1d")
        return intent

    def test_every_kind_builds_the_documented_shape(self):
        for kind in alert_spec.ALL_KINDS:
            with self.subTest(kind=kind):
                out = alert_spec.run("run", {"symbol": "AMD", "asset_class": "equity", "intent": self.intent_for(kind)})
                self.assertTrue(out["ok"], out)
                params = out["params"]
                self.assertEqual("equity", params["asset_class"])
                if kind in alert_spec.PRICE_KINDS:
                    self.assertIn("threshold", params)
                    self.assertNotIn("indicator", params)
                elif kind in alert_spec.VALUE_KINDS:
                    self.assertIn("threshold", params)
                    self.assertIn("indicator", params)
                else:
                    self.assertNotIn("threshold", params)
                    self.assertIn("indicator", params)
                if "indicator" in params:
                    self.assertIn(params["indicator"]["interval_secs"], (300, 600, 3600, 86400, 604800, 2592000))
                    fam = alert_spec.indicator_family(kind)
                    expected_keys = {"sma": {"period", "interval_secs"}, "ema": {"period", "interval_secs"},
                                     "rsi": {"period", "interval_secs"}, "vwap": {"interval_secs"},
                                     "macd": {"fast_period", "slow_period", "signal_period", "interval_secs"},
                                     "boll": {"period", "std_dev", "ma_type", "interval_secs"}}[fam]
                    self.assertEqual(expected_keys, set(params["indicator"]))

    def test_crypto_accepts_only_price_kinds(self):
        for kind in alert_spec.ALL_KINDS:
            out = alert_spec.run("run", {"symbol": "ETH", "asset_class": "crypto", "intent": self.intent_for(kind)})
            self.assertEqual(kind in alert_spec.PRICE_KINDS, out["ok"], kind)

    def test_asset_class_always_emitted(self):
        for symbol in ("NVDA", "KO", "SPY"):
            out = alert_spec.run("run", {"symbol": symbol, "intent": {"kind": "price_above", "threshold": "1"}})
            self.assertEqual("equity", out["params"]["asset_class"])

    def test_btc_and_eth_are_in_the_ambiguous_list(self):
        self.assertIn("BTC", alert_spec.AMBIGUOUS_CRYPTO)
        self.assertIn("ETH", alert_spec.AMBIGUOUS_CRYPTO)


class ContractTests(unittest.TestCase):
    def cli(self, *args, **kw):
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=kw.get("stdin", ""),
                              capture_output=True, text=True, timeout=120)

    def test_selftest_exits_zero(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)

    def test_schema_prints_json_schemas_for_every_op(self):
        doc = json.loads(self.cli("--schema").stdout)
        self.assertEqual(sorted(alert_spec.OPS), sorted(doc["ops"]))
        for pair in doc["ops"].values():
            self.assertIn("$schema", pair["input"])
            self.assertIn("$schema", pair["output"])
        self.assertEqual(30, len(doc["condition_types"]))

    def test_cli_round_trip_and_errors(self):
        res = self.cli("run", stdin=json.dumps({"symbol": "NVDA", "asset_class": "equity",
                                                "intent": {"kind": "price_below", "threshold": "150"}}))
        self.assertEqual(0, res.returncode)
        self.assertEqual("price_below", json.loads(res.stdout)["params"]["condition_type"])
        self.assertEqual("BAD_JSON", json.loads(self.cli("run", stdin="nope").stdout)["errors"][0]["code"])
        self.assertEqual("UNKNOWN_OP", json.loads(self.cli("create", stdin="{}").stdout)["errors"][0]["code"])

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
