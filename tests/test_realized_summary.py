"""Tests for skills/robinhood-tax-loss-harvesting/scripts/realized_summary.py (YTD realized by account, build spec B.2 workflow E)."""

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
NAME = "realized_summary"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SHARED)
sys.path.insert(0, SCRIPTS)

import realized_summary  # noqa: E402

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
                out = realized_summary.run(case["op"], case["input"])
                self.assertTrue(subset(case["expected"], out),
                                "expected subset %s\n got %s" % (case["expected"], json.dumps(out, indent=1)))
                self.check_extra(case, out)
                count += 1
        self.assertGreaterEqual(count, 7)

    def check_extra(self, case, out):
        pass


class SummaryTests(unittest.TestCase):
    def test_notes_about_prediction_markets_and_cross_account_washes(self):
        out = realized_summary.run("run", {"as_of": "2026-11-16", "accounts": [{"last4": "X4F1", "type": "taxable"}],
                                           "trades": []})
        self.assertIn("prediction-market", out["prediction_markets_note"])
        self.assertIn("1099", out["wash_note"])
        self.assertEqual("0.00", out["taxable_total_usd"])


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
            out = realized_summary.run(case["op"], case["input"])
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

        seeds = [(op, data) for op, data, _ in realized_summary.EXAMPLES] + [(c["op"], c["input"]) for _, c in golden_cases()]
        for n in range(600):
            op, data = rng.choice(seeds)
            for _ in range(rng.randint(1, 3)):
                data = mutate(data)
            with self.subTest(n=n):
                out = realized_summary.run(op, data)
                self.assertIsInstance(out, dict)
                self.assertIn("ok", out)
                json.dumps(out)


if __name__ == "__main__":
    unittest.main()
