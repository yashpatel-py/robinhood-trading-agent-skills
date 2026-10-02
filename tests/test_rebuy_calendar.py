"""Tests for skills/robinhood-tax-loss-harvesting/scripts/rebuy_calendar.py (the RFC 5545 rebuy calendar, build spec B.2 workflow G)."""

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
NAME = "rebuy_calendar"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SHARED)
sys.path.insert(0, SCRIPTS)

import rebuy_calendar  # noqa: E402

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
                out = rebuy_calendar.run(case["op"], case["input"])
                self.assertTrue(subset(case["expected"], out),
                                "expected subset %s\n got %s" % (case["expected"], json.dumps(out, indent=1)))
                self.check_extra(case, out)
                count += 1
        self.assertGreaterEqual(count, 5)

    def check_extra(self, case, out):
        pass


def check_ics(test, case, out):
    ics = out.get("ics", "")
    for s in case.get("ics_contains", []):
        test.assertIn(s, ics)
    for s in case.get("ics_not_contains", []):
        test.assertNotIn(s, ics)
    for s in case.get("ics_contains_unfolded", []):
        test.assertIn(s, ics.replace("\r\n ", ""))
    if out.get("ok"):
        test.assertEqual([], rebuy_calendar.ics_problems(ics))


GoldenTests.check_extra = lambda self, case, out: check_ics(self, case, out)


class IcsTests(unittest.TestCase):
    def test_long_lines_fold_at_75_octets_even_with_multibyte_text(self):
        out = rebuy_calendar.run("run", {"items": [{"symbol": "TSLA", "do_not_buy_until": "2026-12-17",
                                                    "note": "\u2022" * 120}], "dtstamp": "2026-11-17T01:05:00Z"})
        for line in out["ics"].split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)
        self.assertIn("\u2022" * 120, out["ics"].replace("\r\n ", ""))

    def test_uids_are_stable_and_distinct(self):
        data = {"items": [{"symbol": "TSLA", "sale_date": "2026-11-16", "do_not_buy_until": "2026-12-17"}],
                "dtstamp": "2026-11-17T01:05:00Z"}
        a = rebuy_calendar.run("run", data)["ics"]
        b = rebuy_calendar.run("run", data)["ics"]
        self.assertEqual(a, b)
        uids = [ln for ln in a.split("\r\n") if ln.startswith("UID:")]
        self.assertEqual(2, len(set(uids)))


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
            out = rebuy_calendar.run(case["op"], case["input"])
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

    def test_writes_only_with_out_and_never_overwrites_without_force(self):
        import tempfile
        data = json.dumps({"items": [{"symbol": "AMD", "do_not_buy_until": "2026-12-04"}],
                           "dtstamp": "2026-11-17T01:05:00Z"})
        with tempfile.TemporaryDirectory() as tmp:
            proc = self.cli(["run"], data, cwd=tmp)
            self.assertIsNone(json.loads(proc.stdout)["written_to"])
            self.assertEqual([], os.listdir(tmp))
            target = os.path.join(tmp, "rebuy.ics")
            first = json.loads(self.cli(["run", "--out", target], data).stdout)
            self.assertEqual(os.path.abspath(target), first["written_to"])
            with open(target, "rb") as fh:
                body = fh.read()
            self.assertTrue(body.startswith(b"BEGIN:VCALENDAR\r\n"))
            again = json.loads(self.cli(["run", "--out", target], data).stdout)
            self.assertEqual("FILE_EXISTS", again["errors"][0]["code"])
            forced = json.loads(self.cli(["run", "--out", target, "--force"], data).stdout)
            self.assertTrue(forced["ok"])
            folder = json.loads(self.cli(["run", "--out", tmp], data).stdout)
            self.assertEqual("BAD_PATH", folder["errors"][0]["code"])


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

        seeds = [(op, data) for op, data, _ in rebuy_calendar.EXAMPLES] + [(c["op"], c["input"]) for _, c in golden_cases()]
        for n in range(600):
            op, data = rng.choice(seeds)
            for _ in range(rng.randint(1, 3)):
                data = mutate(data)
            with self.subTest(n=n):
                out = rebuy_calendar.run(op, data)
                self.assertIsInstance(out, dict)
                self.assertIn("ok", out)
                json.dumps(out)


if __name__ == "__main__":
    unittest.main()
