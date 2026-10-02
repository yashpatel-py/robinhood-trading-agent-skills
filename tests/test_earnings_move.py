"""Tests for skills/robinhood-options-monitor/scripts/earnings_move.py (implied move vs past reactions)."""

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "robinhood-options-monitor", "scripts")
NAME = "earnings_move"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}

_spec = importlib.util.spec_from_file_location("wpg_earnings_move", SCRIPT)
earnings_move = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(earnings_move)

NVDA = {"symbol": "NVDA", "spot": "228.10", "report": {"date": "2026-11-18", "timing": "pm", "verified": False},
        "straddle": {"expiration": "2026-11-20", "strike": "230", "call": {"bid": "12.00", "ask": "12.20"},
                     "put": {"bid": "13.60", "ask": "13.80"}}}


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
        self.assertGreaterEqual(len(cases), 12)
        for case in cases:
            with self.subTest(case=case["name"]):
                out = earnings_move.run("run", case["input"])
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1, ensure_ascii=False))
                self.assertEqual([], earnings_move.schema_errors(out, earnings_move.SCHEMAS["run"]["output"]))


class SpecChecks(unittest.TestCase):
    def test_nvda_implied_move_is_25_80_and_11_3_pct(self):
        out = earnings_move.run("run", NVDA)
        self.assertEqual("25.80", out["implied_move_usd"])
        self.assertEqual(11.3, out["implied_move_pct"])
        self.assertEqual("evidence, not a forecast", out["label"])

    def test_every_weakness_of_the_estimate_is_stated(self):
        out = earnings_move.run("run", {"symbol": "ABC", "spot": "50", "report": {"date": "2026-11-18", "timing": "pm"},
                                        "straddle": {"expiration": "2026-11-27", "strike": "55",
                                                     "call": {"bid": "0", "ask": "0.40"},
                                                     "put": {"bid": "4.00", "ask": "6.00"}}})
        notes = " | ".join(out["notes"])
        for fragment in ("not verified", "9 days after the report", "more than 2.5%", "call's bid/ask spread",
                         "put's bid/ask spread", "call has no bid", "No past earnings reactions"):
            self.assertIn(fragment, notes)

    def test_small_sample_is_called_out(self):
        out = earnings_move.run("run", dict(NVDA, past=[{"date": "2026-08-26", "timing": "pm", "close_before": "180",
                                                         "close_after": "171"}]))
        self.assertTrue(any("small sample" in n for n in out["notes"]))


class AdjacentBars(unittest.TestCase):
    """A past reaction is measured between adjacent trading days only; a missing bar is never bridged."""

    def run_pm(self, module, report, bars):
        return module.run("run", dict(NVDA, past=[{"date": report, "timing": "pm"}],
                                      bars=[{"date": d, "close": c} for d, c in bars]))

    def test_the_nyse_table_catches_a_single_missing_weekday(self):
        self.assertIsNotNone(earnings_move._rh, "rh_time.py should import from the skill's scripts folder")
        out = self.run_pm(earnings_move, "2026-03-02", [("2026-03-02", "100"), ("2026-03-04", "90")])
        self.assertEqual(0, out["n"])
        self.assertIn("not adjacent trading days", out["skipped"][0]["reason"])

    def test_weekday_fallback_without_rh_time(self):
        spec = importlib.util.spec_from_file_location("wpg_earnings_move_nocal", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._rh = None
        # one weekday between (room for a holiday) is accepted; two are a gap
        self.assertEqual(1, self.run_pm(module, "2026-03-02", [("2026-03-02", "100"), ("2026-03-04", "90")])["n"])
        out = self.run_pm(module, "2026-03-02", [("2026-03-02", "100"), ("2026-03-05", "90")])
        self.assertEqual(0, out["n"])
        self.assertIn("not adjacent trading days", out["skipped"][0]["reason"])
        self.assertEqual(1, self.run_pm(module, "2026-02-27", [("2026-02-27", "100"), ("2026-03-02", "90")])["n"])

    def test_dates_outside_the_calendar_fall_back_instead_of_crashing(self):
        out = self.run_pm(earnings_move, "2024-08-28", [("2024-08-28", "100"), ("2024-08-29", "94")])
        self.assertTrue(out["ok"])
        self.assertEqual([-6.0], out["past_moves_pct"])


class NoForecastLanguage(unittest.TestCase):
    def test_outputs_carry_no_direction_or_advice(self):
        banned = re.compile(r"\b(will (rise|fall|go up|go down|move)|expect(ed)? to|likely to|you should|recommend|"
                            r"bullish|bearish)\b", re.I)
        for case in load("run.json"):
            out = earnings_move.run("run", case["input"])
            with self.subTest(case=case["name"]):
                self.assertIsNone(banned.search(json.dumps(out)))


class ContractTests(unittest.TestCase):
    def cli(self, *args, **kw):
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=kw.get("stdin", ""),
                              capture_output=True, text=True, timeout=120)

    def test_selftest_exits_zero(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)

    def test_schema_prints_json_schemas_for_every_op(self):
        doc = json.loads(self.cli("--schema").stdout)
        self.assertEqual(sorted(earnings_move.OPS), sorted(doc["ops"]))

    def test_run_over_stdin(self):
        res = self.cli("run", stdin=json.dumps(NVDA))
        self.assertEqual(0, res.returncode)
        self.assertEqual("25.80", json.loads(res.stdout)["implied_move_usd"])

    def test_bad_json_and_missing_op_still_emit_json_and_exit_zero(self):
        for args, stdin, code in ((["run"], "nope", "BAD_JSON"), ([], "", "MISSING_OP"), (["x"], "{}", "UNKNOWN_OP")):
            res = self.cli(*args, stdin=stdin)
            self.assertEqual(0, res.returncode, res.stderr)
            self.assertEqual(code, json.loads(res.stdout)["errors"][0]["code"])

    def test_source_is_stdlib_py39_ascii_and_writes_no_files(self):
        with open(SCRIPT, "rb") as fh:
            text = fh.read().decode("ascii")
        tree = ast.parse(text, feature_version=(3, 9))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
                self.fail("the script must not open files")
        self.assertEqual(set(), imported & FORBIDDEN_IMPORTS)


if __name__ == "__main__":
    unittest.main()
