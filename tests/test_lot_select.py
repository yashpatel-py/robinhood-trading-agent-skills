"""Tests for shared/scripts/lot_select.py (specific-lot comparisons and tax_lots validation, build spec B.7.10)."""

import ast
import glob
import json
import os
import subprocess
import sys
import unittest
from datetime import date, timedelta

sys.dont_write_bytecode = True  # keep __pycache__ out of the shipped skill folders
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "shared", "scripts")
TAX_SCRIPTS = os.path.join(ROOT, "skills", "robinhood-tax-loss-harvesting", "scripts")
NAME = "lot_select"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, TAX_SCRIPTS)  # holding_period.py, for the parity test
sys.path.insert(0, SCRIPTS)  # the shared source must win over the (possibly stale) synced copy next to it

import lot_select  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) <= len(actual) and all(
            subset(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def has_key(obj, key):
    if isinstance(obj, dict):
        return key in obj or any(has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(has_key(v, key) for v in obj)
    return False


def golden_cases():
    for path in sorted(glob.glob(os.path.join(GOLDEN, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            for case in json.load(fh)["cases"]:
                yield os.path.basename(path), case


class GoldenTests(unittest.TestCase):
    def test_golden_cases(self):
        count = 0
        for fname, case in golden_cases():
            with self.subTest(file=fname, case=case["name"]):
                out = lot_select.run(case["op"], case["input"])
                self.assertTrue(subset(case["expected"], out),
                                "expected subset %s\n got %s" % (case["expected"], json.dumps(out, indent=1)))
                count += 1
        self.assertGreaterEqual(count, 20)


class NoBestAnywhere(unittest.TestCase):
    """The kit shows choices side by side and never ranks them (spec B.7.10, rubric case T3)."""

    def test_no_best_or_recommended_key_in_any_output(self):
        for fname, case in golden_cases():
            out = lot_select.run(case["op"], case["input"])
            for key in ("best", "recommended", "recommendation"):
                with self.subTest(case=case["name"], key=key):
                    self.assertFalse(has_key(out, key))

    def test_fifo_label_and_disclosed_order(self):
        out = lot_select.run("compare", {"sell_quantity": "1", "price": "10", "as_of": "2026-11-16", "lots": [
            {"open_lot_id": "A", "acquired": "2026-01-02", "quantity_available": "1", "cost_per_share": "9"}]})
        self.assertEqual("Robinhood's default", out["fifo_label"])
        self.assertEqual("Robinhood's default", out["strategies"]["fifo"]["label"])
        for name, result in out["strategies"].items():
            self.assertTrue(result["order"], name)


class HoldingPeriodParity(unittest.TestCase):
    """lot_select embeds the holding-period rule (the core skill ships without holding_period.py); both agree."""

    def test_same_long_term_date_for_every_day_2023_to_2028(self):
        import holding_period
        d = date(2023, 1, 1)
        while d <= date(2028, 12, 31):
            self.assertEqual(holding_period.long_term_on(d), lot_select.long_term_on(d), d.isoformat())
            d += timedelta(days=1)

    def test_spec_examples(self):
        self.assertEqual((date(2026, 10, 4), False), lot_select.long_term_on(date(2025, 10, 3)))
        self.assertEqual((date(2026, 12, 4), False), lot_select.long_term_on(date(2025, 12, 3)))
        self.assertEqual((date(2025, 3, 1), False), lot_select.long_term_on(date(2024, 2, 29)))  # Rev. Rul. 66-7
        self.assertEqual((date(2028, 3, 1), False), lot_select.long_term_on(date(2027, 2, 28)))
        self.assertEqual("short", lot_select.term_on(date(2025, 12, 3), date(2026, 12, 3)))
        self.assertEqual("long", lot_select.term_on(date(2025, 12, 3), date(2026, 12, 4)))


class DatesAndBrokerTerm(unittest.TestCase):
    def test_timestamp_is_the_us_eastern_date_same_as_holding_period(self):
        import holding_period
        for ts in ("2025-12-04T00:30:00Z", "2026-07-01T03:59:59Z", "2026-07-01T04:00:00Z", "2026-03-08T06:30:00+00:00"):
            with self.subTest(ts=ts):
                self.assertEqual(holding_period.parse_day(ts, "x"), lot_select.parse_day(ts, "x"))

    def test_without_rh_time_a_timestamp_is_refused_not_read_as_utc(self):
        saved = lot_select.rh_time
        lot_select.rh_time = None
        try:
            lots = [{"open_lot_id": "E", "open_date": "2025-12-04T00:30:00Z", "quantity_available": "1",
                     "cost_per_share": "200"}]
            out = lot_select.run("compare", {"sell_quantity": "1", "price": "262", "as_of": "2026-12-04", "lots": lots})
            self.assertEqual("RH_TIME_MISSING", out["errors"][0]["code"])
            lots[0]["open_date"] = "2025-12-03"
            out = lot_select.run("compare", {"sell_quantity": "1", "price": "262", "as_of": "2026-12-04", "lots": lots})
            self.assertEqual("long", out["strategies"]["fifo"]["lots_detail"][0]["term"])
        finally:
            lot_select.rh_time = saved

    def test_broker_term_decides_the_split_and_no_countdown_for_a_broker_long_lot(self):
        lots = [{"open_lot_id": "R", "open_date": "2026-10-10", "quantity_available": "10", "cost_per_share": "300",
                 "term": "long_term"}]
        out = lot_select.run("compare", {"sell_quantity": "10", "price": "262", "as_of": "2026-11-16", "lots": lots})
        for name, result in out["strategies"].items():
            with self.subTest(strategy=name):
                self.assertEqual("-380.00", result["realized_long_usd"])
                self.assertEqual("0.00", result["realized_short_usd"])
                row = result["lots_detail"][0]
                self.assertNotIn("long_term_on", row)
                self.assertNotIn("days_until_long_term", row)
                self.assertEqual(["TERM_DISAGREES"], [w["code"] for w in result["warnings"]])
        lots[0]["term"] = "short"
        out = lot_select.run("compare", {"sell_quantity": "10", "price": "262", "as_of": "2026-11-16", "lots": lots})
        self.assertEqual([], out["strategies"]["fifo"]["warnings"])
        self.assertTrue(out["strategies"]["fifo"]["lots_detail"][0]["term_agrees"])


class ValidateMatchesReviewRules(unittest.TestCase):
    def test_every_error_code_has_a_case(self):
        codes = set()
        for _, case in golden_cases():
            if case["op"] == "validate":
                for err in lot_select.run("validate", case["input"]).get("errors", []):
                    codes.add(err["code"])
        for code in ("TAX_LOTS_SUM_MISMATCH", "TAX_LOTS_EXCEEDS_AVAILABLE", "TAX_LOTS_MAX_30", "TAX_LOTS_NOT_ALLOWED_WITH",
                     "TAX_LOTS_SELL_ONLY", "UNKNOWN_LOT", "DUPLICATE_LOT", "BAD_QUANTITY", "LOT_NOT_SELECTABLE"):
            self.assertIn(code, codes)

    def test_compare_output_validates(self):
        lots = [{"open_lot_id": "L7", "open_date": "2026-06-02", "quantity_available": "20", "cost_per_share": "340.00"},
                {"open_lot_id": "L1", "open_date": "2025-03-10", "quantity_available": "20", "cost_per_share": "280.00"}]
        out = lot_select.run("compare", {"sell_quantity": "33.5", "price": "262", "as_of": "2026-11-16", "lots": lots})
        for name, result in out["strategies"].items():
            with self.subTest(strategy=name):
                check = lot_select.run("validate", {"sell_quantity": "33.5", "tax_lots": result["tax_lots"], "lots": lots,
                                                    "order": {"side": "sell", "type": "market",
                                                              "market_hours": "regular_hours"}})
                self.assertTrue(check["valid"], check)


class ContractTests(unittest.TestCase):
    def cli(self, args, stdin=""):
        return subprocess.run([sys.executable, SCRIPT] + args, input=stdin, capture_output=True, text=True, timeout=60)

    def test_selftest_and_schema(self):
        proc = self.cli(["--selftest"])
        self.assertEqual(0, proc.returncode, proc.stdout)
        doc = json.loads(self.cli(["--schema"]).stdout)
        self.assertEqual({"compare", "validate"}, set(doc["ops"]))

    def test_bad_input_exit_zero(self):
        for args, stdin in ((["compare"], "{"), (["compare"], "{}"), (["validate"], "{}"), (["other"], "{}"), ([], "")):
            proc = self.cli(args, stdin)
            self.assertEqual(0, proc.returncode)
            self.assertFalse(json.loads(proc.stdout)["ok"])

    def test_stdlib_only_no_network_and_py39_grammar(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src, feature_version=(3, 9))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for m in mods:
                    self.assertNotIn(m.split(".")[0], FORBIDDEN_IMPORTS)
        for word in ("socket", "urllib", "http.client", "requests", "curl", "wget", "open("):
            self.assertNotIn(word, src)


class MalformedInputTests(unittest.TestCase):
    """Wrong JSON types must come back as {"ok": false, "errors": [...]}, never as a crash (script contract)."""

    def test_seeded_mutations_never_crash(self):
        import random
        rng = random.Random(20260922)
        bad_values = [None, 1, 1.5, True, "x", "", [], {}, [1], {"a": 1}, "2026-13-45", "-1", "NaN"]

        def mutate(obj):
            if isinstance(obj, dict) and obj:
                out = dict(obj)
                key = rng.choice(sorted(out))
                out[key] = mutate(out[key]) if rng.random() < 0.5 else rng.choice(bad_values)
                return out
            if isinstance(obj, list) and obj:
                out = list(obj)
                i = rng.randrange(len(out))
                out[i] = mutate(out[i]) if rng.random() < 0.5 else rng.choice(bad_values)
                return out
            return rng.choice(bad_values)

        seeds = [(op, data) for op, data, _ in lot_select.EXAMPLES] + [(c["op"], c["input"]) for _, c in golden_cases()]
        for n in range(600):
            op, data = rng.choice(seeds)
            for _ in range(rng.randint(1, 3)):
                data = mutate(data)
            with self.subTest(n=n):
                out = lot_select.run(op, data)
                self.assertIsInstance(out, dict)
                self.assertIn("ok", out)
                json.dumps(out)


if __name__ == "__main__":
    unittest.main()
