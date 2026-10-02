"""Tests for skills/robinhood-options-monitor/scripts/expiry_risk.py (expiration and assignment radar)."""

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
SHARED = os.path.join(ROOT, "shared", "scripts")
NAME = "expiry_risk"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}
if SHARED not in sys.path:
    sys.path.append(SHARED)  # rh_time.py is synced next to the script at release; before that it lives here


def load_module(alias):
    spec = importlib.util.spec_from_file_location(alias, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


expiry_risk = load_module("wpg_expiry_risk")


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


def opt(**kw):
    base = {"account_last4": "X4F1", "symbol": "KO", "type": "call", "side": "short", "quantity": "1", "strike": "70",
            "expiration": "2026-11-20", "bid": "2.20", "ask": "2.30", "underlying_type": "equity"}
    base.update(kw)
    return base


class GoldenTests(unittest.TestCase):
    def test_calendar_module_is_available(self):
        self.assertIsNotNone(expiry_risk._rh, "rh_time.py should import from the skill or shared/scripts")

    def test_golden_cases(self):
        cases = load("run.json")
        self.assertGreaterEqual(len(cases), 25)
        for case in cases:
            with self.subTest(case=case["name"]):
                out = expiry_risk.run("run", case["input"])
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1, ensure_ascii=False))
                self.assertEqual([], expiry_risk.schema_errors(out, expiry_risk.SCHEMAS["run"]["output"]))


class SpecChecks(unittest.TestCase):
    """The numbers the build spec names for expiry_risk (section E.6)."""

    def spec_out(self):
        return expiry_risk.run("run", {
            "as_of": "2026-11-16", "radar_days": 7,
            "positions": [opt(), opt(symbol="SPY", side="long", quantity="2", strike="650", bid="21.40", ask="21.60")],
            "underlying": {"KO": "72.05", "SPY": "671.20"},
            "ex_dividends": {"KO": {"ex_date": "2026-11-18", "amount": "0.53"}},
            "buying_power": {"X4F1": "2480.00"}, "shares_held": {"X4F1": {"KO": "100"}}})

    def test_spy_auto_exercise_needs_130000(self):
        spy = next(i for i in self.spec_out()["items"] if i["symbol"] == "SPY")
        self.assertEqual("AUTO_EXERCISE_CASH_NEED", spy["risk"])
        self.assertEqual(("130000.00", "2480.00", "127520.00"),
                         (spy["cash_needed_usd"], spy["buying_power_usd"], spy["shortfall_usd"]))

    def test_ko_extrinsic_bid_and_mid_against_the_dividend(self):
        ko = next(i for i in self.spec_out()["items"] if i["symbol"] == "KO")
        self.assertEqual("EARLY_ASSIGNMENT_BEFORE_EX_DIV", ko["risk"])
        self.assertEqual(("0.15", "0.20", "0.53"), (ko["extrinsic_bid"], ko["extrinsic_mid"], ko["dividend"]))
        self.assertIn("estimate", ko["label_estimate"])

    def test_index_option_cannot_be_exercised_manually(self):
        out = expiry_risk.run("run", {"as_of": "2026-11-16", "positions": [
            opt(symbol="SPX", side="long", strike="6600", underlying_type="index", bid="110", ask="112")],
            "underlying": {"SPX": "6712.35"}})
        self.assertEqual("INDEX_NO_MANUAL_EXERCISE", out["items"][0]["risk"])
        self.assertEqual("11235.00", out["items"][0]["settlement_estimate_usd"])
        self.assertNotIn("AUTO_EXERCISE_CASH_NEED", out["items"][0]["also"])


class CalendarFallback(unittest.TestCase):
    """Copied alone without rh_time.py, the radar still runs and says holidays were not excluded."""

    def test_weekday_fallback_counts_thanksgiving_and_says_so(self):
        module = load_module("wpg_expiry_risk_nocal")
        module._rh = None
        out = module.run("run", {"as_of": "2026-11-25", "positions": [
            opt(symbol="AMD", side="long", strike="165", expiration="2026-11-30", bid="1.00", ask="1.10")],
            "underlying": {"AMD": "165.30"}, "now": "2026-11-25T10:00:00-05:00"})
        self.assertTrue(out["ok"])
        self.assertEqual(3, out["expiring"][0]["trading_days_to_expiration"])
        self.assertEqual([], out["pin_risk"])
        self.assertTrue(any("weekdays only" in n for n in out["notes"]))
        self.assertIn("weekdays only", out["calendar"])

    def test_sellout_time_still_converts_without_rh_time(self):
        module = load_module("wpg_expiry_risk_nocal2")
        module._rh = None
        out = module.run("run", {"as_of": "2026-11-16", "positions": [
            opt(symbol="SPY", side="long", strike="650", sellout_datetime="2026-11-20T20:30:00Z")],
            "underlying": {"SPY": "671.20"}})
        self.assertEqual("Fri 2026-11-20 3:30 PM ET", out["items"][0]["sellout_text"])


class SafetyProperties(unittest.TestCase):
    def test_every_item_code_is_known_and_no_advice_wording_appears(self):
        banned = re.compile(r"\b(you should|i recommend|we recommend|should (sell|buy|close|roll|exercise))\b", re.I)
        for case in load("run.json"):
            out = expiry_risk.run("run", case["input"])
            with self.subTest(case=case["name"]):
                self.assertIsNone(banned.search(json.dumps(out)))
                for item in out.get("items", []):
                    self.assertIn(item["risk"], expiry_risk.ITEM_CODES)

    def test_account_numbers_are_masked_in_warnings(self):
        out = expiry_risk.run("run", {"as_of": "2026-11-16", "positions": [
            opt(side="long", type="put", strike="75"), opt(symbol="SPY", side="long", strike="650")],
            "underlying": {"KO": "72.05", "SPY": "671.20"}})
        texts = [w["text"] for i in out["items"] for w in i["warnings"]]
        self.assertTrue(texts)
        for text in texts:
            self.assertIn("\u2022\u2022\u2022\u2022X4F1", text)

    def test_printed_price_label_and_distance_always_agree(self):
        # Sub-penny quotes around the strike: the displayed underlying, the ITM/OTM label and the distance come from
        # one cents-rounded figure, so a distance is never negative and "$100.01" is never labeled out of the money.
        for typ in ("call", "put"):
            for spot in ("99.994", "99.995", "99.999", "100.000", "100.001", "100.004", "100.005", "100.009"):
                out = expiry_risk.run("run", {"as_of": "2026-11-20", "positions": [
                    opt(symbol="AMD", type=typ, side="long", strike="100", bid="0.05", ask="0.10")],
                    "underlying": {"AMD": spot}, "buying_power": {"X4F1": "50000"},
                    "shares_held": {"X4F1": {"AMD": "100"}}})
                row = out["expiring"][0]
                with self.subTest(type=typ, spot=spot):
                    shown = expiry_risk.Decimal(row["underlying_price"])
                    itm_by = (shown - 100) if typ == "call" else (100 - shown)
                    self.assertEqual("ITM" if itm_by >= expiry_risk.Decimal("0.01") else "OTM", row["moneyness"])
                    for w in out["worthless"]:
                        self.assertFalse(w["otm_by"].startswith("-"), w)
                    if row["moneyness"] == "OTM":
                        self.assertFalse(row["otm_by"].startswith("-"), row)
                        self.assertNotIn("AUTO_EXERCISE_CASH_NEED", row["codes"])
                    else:
                        self.assertEqual(expiry_risk.Decimal(row["itm_by"]), itm_by)

    def test_unknown_inputs_never_produce_a_quiet_all_clear(self):
        out = expiry_risk.run("run", {"as_of": "2026-11-16", "positions": [opt()], "underlying": {}})
        self.assertEqual([], out["items"])
        self.assertEqual([], out["quiet"])
        self.assertEqual("UNDERLYING_PRICE_MISSING", out["unknown"][0]["code"])


class ContractTests(unittest.TestCase):
    def cli(self, *args, **kw):
        env = dict(os.environ, PYTHONPATH=SHARED)
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=kw.get("stdin", ""), env=env,
                              capture_output=True, text=True, timeout=120)

    def test_selftest_exits_zero(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)

    def test_schema_prints_json_schemas_for_every_op(self):
        doc = json.loads(self.cli("--schema").stdout)
        self.assertEqual(sorted(expiry_risk.OPS), sorted(doc["ops"]))
        self.assertIn("PIN_RISK", doc["risk_codes"])

    def test_run_over_stdin(self):
        case = load("run.json")[0]
        res = self.cli("run", stdin=json.dumps(case["input"]))
        self.assertEqual(0, res.returncode)
        self.assertTrue(strict(case["expected"], json.loads(res.stdout)))

    def test_bad_json_and_missing_op_still_emit_json_and_exit_zero(self):
        for args, stdin, code in ((["run"], "[", "BAD_JSON"), ([], "", "MISSING_OP"), (["x"], "{}", "UNKNOWN_OP")):
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
