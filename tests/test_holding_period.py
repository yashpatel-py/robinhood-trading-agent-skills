"""Tests for skills/robinhood-tax-loss-harvesting/scripts/holding_period.py (holding period and long-term countdown, build spec B.7.11)."""

import ast
import glob
import json
import os
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True  # keep __pycache__ out of the shipped skill folders
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "robinhood-tax-loss-harvesting", "scripts")
SHARED = os.path.join(ROOT, "shared", "scripts")
NAME = "holding_period"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SHARED)
sys.path.insert(0, SCRIPTS)

import holding_period  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) <= len(actual) and all(
            subset(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def golden_cases():
    for path in sorted(glob.glob(os.path.join(GOLDEN, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            for case in json.load(fh)["cases"]:
                yield os.path.basename(path), case


def has_key(obj, keys):
    if isinstance(obj, dict):
        return any(k in obj for k in keys) or any(has_key(v, keys) for v in obj.values())
    if isinstance(obj, list):
        return any(has_key(v, keys) for v in obj)
    return False


class GoldenTests(unittest.TestCase):
    def test_golden_cases(self):
        count = 0
        for fname, case in golden_cases():
            with self.subTest(file=fname, case=case["name"]):
                out = holding_period.run(case["op"], case["input"])
                self.assertTrue(subset(case["expected"], out),
                                "expected subset %s\n got %s" % (case["expected"], json.dumps(out, indent=1)))
                self.check_extra(case, out)
                count += 1
        self.assertGreaterEqual(count, 10)

    def check_extra(self, case, out):
        pass


class RuleTests(unittest.TestCase):
    def test_spec_goldens_are_present(self):
        names = " ".join(case["name"] for _, case in golden_cases())
        for needle in ("2025-10-03 -> long-term from 2026-10-04", "2025-12-03 -> long-term from 2026-12-04",
                       "2024-02-29 -> long-term from 2025-03-01", "2027-02-28 (last day of the month) turns "
                       "long-term 2028-03-01"):
            self.assertIn(needle, names)

    def test_every_day_of_six_years_is_long_term_exactly_after_one_year(self):
        # Pub 550: long-term from the day after the one-year anniversary. Rev. Rul. 66-7: a lot acquired on the
        # last day of a month is long-term from the first day of the 13th month after it.
        from datetime import date, timedelta
        d = date(2023, 1, 1)
        while d <= date(2028, 12, 31):
            lt, amb = holding_period.long_term_on(d)
            self.assertIs(False, amb)
            nxt = d + timedelta(days=1)
            if nxt.day == 1:
                self.assertEqual(date(nxt.year + 1, nxt.month, 1), lt, d)
                self.assertEqual(1, lt.day)
            else:
                self.assertEqual(d.replace(year=d.year + 1) + timedelta(days=1), lt, d)
            self.assertGreaterEqual((lt - d).days, 366, d)  # held more than one year, never less
            d += timedelta(days=1)

    def test_month_end_edge_cases_follow_rev_rul_66_7(self):
        from datetime import date
        for acquired, expected, moved in (("2027-02-28", "2028-03-01", True), ("2028-02-29", "2029-03-01", True),
                                          ("2024-02-29", "2025-03-01", True), ("2025-02-28", "2026-03-01", False),
                                          ("2025-01-31", "2026-02-01", False), ("2025-12-31", "2027-01-01", False),
                                          ("2025-04-30", "2026-05-01", False), ("2028-02-28", "2029-03-01", False)):
            d = date.fromisoformat(acquired)
            self.assertEqual((date.fromisoformat(expected), False), holding_period.long_term_on(d), acquired)
            self.assertIs(moved, holding_period.month_end_rule_moves_date(d), acquired)

    def test_a_feb_28_lot_sold_on_the_leap_day_is_short_term_with_a_note(self):
        out = holding_period.run("run", {"as_of": "2028-02-29", "lots": [
            {"lot_id": "P", "acquired": "2027-02-28", "shares": "1"}]})
        row = out["lots"][0]
        self.assertEqual(("short", "2028-03-01", 1), (row["term"], row["long_term_on"], row["days_until_long_term"]))
        self.assertIn("Rev. Rul. 66-7", row["month_end_note"])
        self.assertIn("Rev. Rul. 66-7", out["rule"])


class ContractTests(unittest.TestCase):
    def cli(self, args, stdin="", cwd=None):
        return subprocess.run([sys.executable, SCRIPT] + args, input=stdin, capture_output=True, text=True,
                              timeout=60, cwd=cwd)

    def test_selftest_and_schema(self):
        proc = self.cli(["--selftest"])
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ok"])
        doc = json.loads(self.cli(["--schema"]).stdout)
        self.assertEqual({"run"}, set(doc["ops"]))
        self.assertIn("input", doc["ops"]["run"])
        self.assertIn("output", doc["ops"]["run"])

    def test_bad_input_prints_json_and_exits_zero(self):
        for args, stdin in ((["run"], "not json"), (["run"], "{}"), (["other"], "{}"), ([], "")):
            proc = self.cli(args, stdin)
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertFalse(json.loads(proc.stdout)["ok"])

    def test_no_ranking_keys(self):
        for _, case in golden_cases():
            out = holding_period.run(case["op"], case["input"])
            self.assertFalse(has_key(out, ("best", "recommended", "recommendation")), case["name"])

    def test_stdlib_only_no_network_and_py39_grammar(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src, feature_version=(3, 9))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for m in mods:
                    self.assertNotIn(m.split(".")[0], FORBIDDEN_IMPORTS)
        for word in ("socket", "urllib", "http.client", "requests", "curl", "wget"):
            self.assertNotIn(word, src)

    def test_no_file_writes(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            self.assertNotIn("open(", fh.read())


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

        seeds = [(op, data) for op, data, _ in holding_period.EXAMPLES] + [(c["op"], c["input"]) for _, c in golden_cases()]
        for n in range(600):
            op, data = rng.choice(seeds)
            for _ in range(rng.randint(1, 3)):
                data = mutate(data)
            with self.subTest(n=n):
                out = holding_period.run(op, data)
                self.assertIsInstance(out, dict)
                self.assertIn("ok", out)
                json.dumps(out)


if __name__ == "__main__":
    unittest.main()
