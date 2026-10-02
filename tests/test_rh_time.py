"""Tests for shared/scripts/rh_time.py (session clock, NYSE calendar, ET/UTC)."""

import ast
import glob
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "shared", "scripts")
NAME = "rh_time"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import rh_time  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}


def subset(expected, actual):
    """True when every key/value in expected appears in actual (lists compare element-wise
    from the start; scalars compare by value and type)."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) <= len(actual) and all(
            subset(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def golden_cases():
    for path in sorted(glob.glob(os.path.join(GOLDEN, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        for case in doc["cases"]:
            yield os.path.basename(path), case


class GoldenTests(unittest.TestCase):
    def test_golden_files_exist(self):
        self.assertTrue(glob.glob(os.path.join(GOLDEN, "*.json")))

    def test_golden_cases(self):
        count = 0
        for fname, case in golden_cases():
            with self.subTest(file=fname, case=case["name"]):
                out = rh_time.run(case["op"], case["input"])
                if case.get("match") == "exact":
                    self.assertEqual(case["expected"], out)
                else:
                    self.assertTrue(subset(case["expected"], out),
                                    "expected subset %s\n got %s" % (case["expected"], json.dumps(out, indent=1)))
                count += 1
        self.assertGreater(count, 40)


class SessionTests(unittest.TestCase):
    def session(self, now):
        return rh_time.run("session", {"now": now, "now_source": "user_stated"})

    def test_boundaries_on_a_normal_day(self):
        expected = {"09:29:59": False, "09:30:00": True, "15:59:59": True, "16:00:00": False}
        for clock, inside in expected.items():
            with self.subTest(clock=clock):
                out = self.session("2026-10-14T%s-04:00" % clock)
                self.assertTrue(out["ok"])
                self.assertIs(out["in_regular_session"], inside)

    def test_early_close_boundary(self):
        self.assertTrue(self.session("2026-11-27T12:59:59-05:00")["in_regular_session"])
        self.assertFalse(self.session("2026-11-27T13:00:00-05:00")["in_regular_session"])
        self.assertEqual("13:00", self.session("2026-11-27T10:00:00-05:00")["regular_close_et"])

    def test_every_listed_holiday_is_closed_and_every_early_close_trades(self):
        for day, name in rh_time.HOLIDAYS.items():
            out = self.session("%sT11:00:00-05:00" % day.isoformat())
            self.assertEqual("closed_day", out["session"], day)
            self.assertEqual(name, out["holiday"])
        for day in rh_time.EARLY_CLOSES:
            out = self.session("%sT11:00:00-05:00" % day.isoformat())
            self.assertTrue(out["in_regular_session"], day)

    def test_pinned_calendar_entries(self):
        from datetime import date
        self.assertIn(date(2026, 11, 26), rh_time.HOLIDAYS)
        self.assertIn(date(2026, 11, 27), rh_time.EARLY_CLOSES)
        self.assertIn(date(2027, 1, 1), rh_time.HOLIDAYS)
        self.assertNotIn(date(2027, 12, 31), rh_time.HOLIDAYS)  # 2028-01-01 is a Saturday, not observed
        self.assertEqual(20, len(rh_time.HOLIDAYS))
        self.assertTrue(all(d.weekday() < 5 for d in rh_time.HOLIDAYS))

    def test_system_clock_default(self):
        out = rh_time.run("session", {})
        if out["ok"]:
            self.assertEqual("system", out["now_source"])
        else:  # only once the embedded calendar has expired
            self.assertEqual("CALENDAR_EXPIRED", out["errors"][0]["code"])

    def test_user_time_overrides_everything(self):
        out = self.session("2026-11-16T20:05:00-05:00")
        self.assertEqual("2026-11-16T20:05:00-05:00", out["now_et"])
        self.assertEqual("user_stated", out["now_source"])

    def test_non_regular_windows_ship_unverified(self):
        self.assertFalse(rh_time.NON_REGULAR_WINDOWS["verified"])
        out = self.session("2026-11-16T20:05:00-05:00")
        self.assertIsNone(out["non_regular"]["candidate"])
        self.assertIn("all_day_hours", out["non_regular"]["ask_user"])

    def test_candidate_logic_once_verified(self):
        saved = rh_time.NON_REGULAR_WINDOWS["verified"]
        rh_time.NON_REGULAR_WINDOWS["verified"] = True
        try:
            cases = {
                "2026-11-16T08:00:00-05:00": "extended_hours",   # pre-market, Monday
                "2026-11-16T17:00:00-05:00": "extended_hours",   # post-market
                "2026-11-16T20:05:00-05:00": "all_day_hours",    # overnight, Monday night
                "2026-11-17T02:00:00-05:00": "all_day_hours",    # overnight, Tuesday morning
                "2026-11-15T21:00:00-05:00": "all_day_hours",    # Sunday night
                "2026-11-14T12:00:00-05:00": None,               # Saturday midday
                "2026-11-16T05:00:00-05:00": None,               # the 04:00-07:00 gap
            }
            for now, expected in cases.items():
                with self.subTest(now=now):
                    out = self.session(now)
                    self.assertEqual(expected, out["non_regular"]["candidate"])
                    self.assertEqual(expected is None, out["non_regular"]["ask_user"] is not None)
        finally:
            rh_time.NON_REGULAR_WINDOWS["verified"] = saved


class TimeZoneTests(unittest.TestCase):
    def tearDown(self):
        rh_time.FORCE_EMBEDDED_TZ = False

    def test_to_utc_spec_example(self):
        self.assertEqual("2026-10-17T04:00:00Z", rh_time.run("to_utc", {"date": "2026-10-17"})["utc"])

    @unittest.skipIf(rh_time._ZONE is None, "zoneinfo/tzdata not available")
    def test_embedded_rules_match_zoneinfo_hourly_2026_2027(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        cur = start
        while cur < datetime(2028, 1, 1, tzinfo=timezone.utc):
            rh_time.FORCE_EMBEDDED_TZ = False
            zone = rh_time.utc_to_et(cur)
            rh_time.FORCE_EMBEDDED_TZ = True
            embedded = rh_time.utc_to_et(cur)
            self.assertEqual(zone.isoformat(), embedded.isoformat(), cur.isoformat())
            cur += timedelta(hours=1)

    @unittest.skipIf(rh_time._ZONE is None, "zoneinfo/tzdata not available")
    def test_embedded_local_to_utc_matches_zoneinfo_around_transitions(self):
        for day in ("2026-03-08", "2026-11-01", "2027-03-14", "2027-11-07"):
            base = datetime.strptime(day, "%Y-%m-%d")
            for minutes in range(0, 24 * 60, 15):
                naive = base + timedelta(minutes=minutes)
                for fold in (0, 1):
                    rh_time.FORCE_EMBEDDED_TZ = False
                    zone = rh_time.et_local_to_utc(naive, fold)
                    rh_time.FORCE_EMBEDDED_TZ = True
                    embedded = rh_time.et_local_to_utc(naive, fold)
                    self.assertEqual(zone[1], embedded[1], (naive, fold))
                    if zone[1] != "nonexistent":
                        self.assertEqual(zone[0], embedded[0], (naive, fold))

    def test_golden_sessions_hold_with_embedded_rules(self):
        rh_time.FORCE_EMBEDDED_TZ = True
        with open(os.path.join(GOLDEN, "sessions.json"), encoding="utf-8") as fh:
            cases = json.load(fh)["cases"]
        for case in cases:
            with self.subTest(case=case["name"]):
                out = rh_time.run(case["op"], case["input"])
                self.assertTrue(subset(case["expected"], out), json.dumps(out))

    def test_other_iana_zone(self):
        if rh_time.ZoneInfo is None:
            self.skipTest("zoneinfo not available")
        try:
            rh_time.ZoneInfo("America/Chicago")
        except Exception:
            self.skipTest("tzdata not available")
        out = rh_time.run("to_utc", {"local": "2026-10-17T00:00:00", "tz": "America/Chicago"})
        self.assertEqual("2026-10-17T05:00:00Z", out["utc"])
        bad = rh_time.run("to_utc", {"local": "2026-10-17T00:00:00", "tz": "Mars/Olympus"})
        self.assertEqual("BAD_TZ", bad["errors"][0]["code"])


class ContractTests(unittest.TestCase):
    def cli(self, *args, **kw):
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=kw.get("stdin", ""),
                              capture_output=True, text=True, timeout=120)

    def test_selftest_exits_zero(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        self.assertTrue(json.loads(res.stdout)["ok"])

    def test_schema_prints_json_schemas_for_every_op(self):
        res = self.cli("--schema")
        self.assertEqual(0, res.returncode)
        doc = json.loads(res.stdout)
        self.assertEqual(sorted(rh_time.OPS), sorted(doc["ops"]))
        for op, pair in doc["ops"].items():
            for side in ("input", "output"):
                schema = pair[side]
                self.assertIn("$schema", schema, (op, side))
                self.assertTrue("type" in schema or "anyOf" in schema, (op, side))

    def test_cli_round_trip(self):
        res = self.cli("to_utc", stdin=json.dumps({"date": "2026-10-17"}))
        self.assertEqual(0, res.returncode)
        self.assertEqual("2026-10-17T04:00:00Z", json.loads(res.stdout)["utc"])

    def test_bad_json_and_unknown_op_exit_zero(self):
        res = self.cli("session", stdin="{not json")
        self.assertEqual(0, res.returncode)
        self.assertEqual("BAD_JSON", json.loads(res.stdout)["errors"][0]["code"])
        res = self.cli("nope", stdin="{}")
        self.assertEqual(0, res.returncode)
        self.assertEqual("UNKNOWN_OP", json.loads(res.stdout)["errors"][0]["code"])
        res = self.cli()
        self.assertEqual(0, res.returncode)
        self.assertFalse(json.loads(res.stdout)["ok"])

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
        self.assertNotIn("open(", source.replace("fh = open", ""))  # rh_time never touches files


if __name__ == "__main__":
    unittest.main()
