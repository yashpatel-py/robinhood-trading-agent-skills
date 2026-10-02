"""Tests for skills/robinhood-options-monitor/scripts/option_exits.py (the user's exit rules on held options)."""

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "robinhood-options-monitor")
SCRIPTS = os.path.join(SKILL, "scripts")
SHARED = os.path.join(ROOT, "shared", "scripts")
NAME = "option_exits"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}
if SHARED not in sys.path:
    sys.path.append(SHARED)  # kitconfig for the asset test; synced next to the script at release


def load_module(name, path):
    spec = importlib.util.spec_from_file_location("wpg_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


option_exits = load_module(NAME, SCRIPT)


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


def amd(**kw):
    base = {"option_id": "o1", "account_last4": "X4F1", "agentic": True, "symbol": "AMD", "type": "call",
            "side": "long", "quantity": "1", "strike": "165", "expiration": "2026-11-27",
            "avg_open_price_per_share": "2.05", "opened_date": "2026-10-20", "underlying_type": "equity"}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


class GoldenTests(unittest.TestCase):
    def test_golden_cases(self):
        cases = load("run.json")
        self.assertGreaterEqual(len(cases), 30)
        for case in cases:
            with self.subTest(case=case["name"]):
                out = option_exits.run("run", case["input"])
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1, ensure_ascii=False))
                self.assertEqual([], option_exits.schema_errors(out, option_exits.SCHEMAS["run"]["output"]))


class SpecChecks(unittest.TestCase):
    """The numbers the build spec names for option_exits (section E.6)."""

    def test_amd_profit_target(self):
        out = option_exits.run("run", {"as_of": "2026-11-16", "rules": {"profit_target_pct": "50"},
                                       "positions": [amd()], "quotes": {"o1": {"bid": "3.30", "ask": "3.40"}}})
        row = out["fired"][0]
        self.assertEqual((61.0, "125.00", ["PROFIT_TARGET"]), (row["pnl_pct"], row["pnl_usd"], row["rules"]))

    def test_corrected_trust_example_is_410_not_424(self):
        out = option_exits.run("run", {"as_of": "2026-11-16", "rules": {"profit_target_pct": "50"},
                                       "positions": [amd(quantity="2", avg_open_price_per_share="4.05")],
                                       "quotes": {"o1": {"bid": "6.10", "ask": "6.25", "mark": "6.17"}}})
        row = out["fired"][0]
        self.assertEqual("410.00", row["pnl_usd"])
        self.assertEqual(50.6, row["pnl_pct"])
        self.assertNotEqual("424.00", row["pnl_usd"])
        self.assertEqual("424.00", row["mark_pnl_usd"])  # the old number is the mark-based view, labeled as such

    def test_short_side_sign_is_the_mirror_of_long(self):
        quotes = {"o1": {"bid": "1.90", "ask": "1.90"}}
        long_row = option_exits.run("run", {"as_of": "2026-11-16", "positions": [amd(avg_open_price_per_share="1.50")],
                                            "quotes": quotes})["watch"][0]
        short_row = option_exits.run("run", {"as_of": "2026-11-16",
                                             "positions": [amd(side="short", avg_open_price_per_share="1.50")],
                                             "quotes": quotes})["watch"][0]
        self.assertEqual("40.00", long_row["pnl_usd"])
        self.assertEqual("-40.00", short_row["pnl_usd"])
        self.assertEqual(-long_row["pnl_pct"], short_row["pnl_pct"])

    def test_max_hold_with_unknown_opening_date_is_skipped_not_estimated(self):
        out = option_exits.run("run", {"as_of": "2026-11-16", "rules": {"max_hold_days": "1"},
                                       "positions": [amd(opened_date=None)],
                                       "quotes": {"o1": {"bid": "3.30", "ask": "3.40"}}})
        self.assertEqual([], out["fired"])
        self.assertEqual("unknown", out["watch"][0]["days_held"])
        self.assertEqual("MAX_HOLD", out["watch"][0]["rule_skips"][0]["rule"])


class AveragePriceUnit(unittest.TestCase):
    """average_price may be per share or per contract; the quote alone must never pick the unit (a 20x move and a
    100x unit error look the same against it). The opening fill settles it."""

    RULES = {"profit_target_pct": "50", "stop_loss_pct": "40"}

    def short_20x(self, **kw):
        pos = amd(side="short", avg_open_price_per_share=None, average_price="-10.0000", average_price_unit="unknown")
        pos.update(kw)
        return option_exits.run("run", {"as_of": "2026-11-16", "rules": self.RULES, "positions": [pos],
                                        "quotes": {"o1": {"bid": "1.95", "ask": "2.05", "mark": "2.00"}}})

    def test_unknown_unit_without_fill_never_reports_a_false_profit(self):
        out = self.short_20x()
        self.assertEqual([], out["fired"])
        row = out["watch"][0]
        self.assertIsNone(row["pnl_usd"])
        self.assertIsNone(row["pnl_pct"])
        self.assertIsNone(row["open_price"])
        self.assertEqual({"PROFIT_TARGET", "STOP_LOSS"}, {r["rule"] for r in out["rules_not_evaluated"]})
        self.assertNotIn("795.00", json.dumps(out))

    def test_the_opening_fill_settles_the_unit_and_the_stop_fires(self):
        for unit in ("unknown", "per_contract"):
            with self.subTest(unit=unit):
                out = self.short_20x(average_price_unit=unit, open_fill_price_per_share="0.10")
                row = out["fired"][0]
                self.assertEqual((["STOP_LOSS"], "-195.00", -1950.0), (row["rules"], row["pnl_usd"], row["pnl_pct"]))
                self.assertEqual([], out["rules_not_evaluated"])

    def test_a_20x_long_winner_never_fires_a_stop(self):
        quotes = {"o1": {"bid": "4.95", "ask": "5.05", "mark": "5.00"}}
        for fill, expect in ((None, []), ("0.25", ["PROFIT_TARGET"])):
            pos = amd(avg_open_price_per_share=None, average_price="25.0000", average_price_unit="unknown",
                      open_fill_price_per_share=fill)
            out = option_exits.run("run", {"as_of": "2026-11-16", "rules": self.RULES, "positions": [pos],
                                           "quotes": quotes})
            with self.subTest(fill=fill):
                fired = [r for row in out["fired"] for r in row["rules"]]
                self.assertNotIn("STOP_LOSS", fired)
                self.assertEqual(expect, fired)

    def test_quote_alone_never_yields_a_pnl_for_an_unknown_unit(self):
        # Sweep moves from 1/1000x to 1000x against the mark: no reading is ever chosen without a stated unit or fill.
        for raw in ("0.05", "0.20", "1.00", "2.05", "10.0000", "25.0000", "205.0000", "900.0000"):
            for side in ("long", "short"):
                pos = amd(side=side, avg_open_price_per_share=None, average_price=raw, average_price_unit="unknown")
                out = option_exits.run("run", {"as_of": "2026-11-16", "rules": self.RULES, "positions": [pos],
                                               "quotes": {"o1": {"bid": "1.95", "ask": "2.05", "mark": "2.00"}}})
                with self.subTest(raw=raw, side=side):
                    self.assertEqual([], out["fired"])
                    self.assertIsNone(out["watch"][0]["pnl_usd"])
                    self.assertEqual(2, len(out["rules_not_evaluated"]))

    def test_time_stop_still_applies_when_the_open_price_is_unsettled(self):
        out = option_exits.run("run", {"as_of": "2026-11-16", "rules": {"stop_loss_pct": "40", "time_stop_dte": "14"},
                                       "positions": [amd(avg_open_price_per_share=None, average_price="205.0000")],
                                       "quotes": {"o1": {"bid": "3.30", "ask": "3.40", "mark": "3.35"}}})
        self.assertEqual(["TIME_STOP"], out["fired"][0]["rules"])
        self.assertIsNone(out["fired"][0]["pnl_usd"])
        self.assertEqual(["STOP_LOSS"], [r["rule"] for r in out["rules_not_evaluated"]])


class SafetyProperties(unittest.TestCase):
    """The script prepares specs; it never chooses a price or a quantity."""

    def all_rows(self):
        for case in load("run.json"):
            out = option_exits.run("run", case["input"])
            if out.get("ok"):
                for row in out["fired"] + out["watch"]:
                    yield case, row

    def test_limit_price_and_quantity_are_always_blank(self):
        for case, row in self.all_rows():
            with self.subTest(case=case["name"]):
                self.assertIsNone(row["limit_price"])
                if row["rules"]:
                    self.assertIsNone(row["quantity"])
                    self.assertIn("shown, not applied", row["default_quantity_text"])

    def test_a_rule_price_appears_only_when_the_user_saved_exit_price_rule(self):
        for case, row in self.all_rows():
            saved = (case["input"].get("rules") or {}).get("exit_price_rule") not in (None, "UNSET")
            with self.subTest(case=case["name"]):
                self.assertEqual(saved and bool(row["rules"]), "rule_limit_price" in row)
                if row.get("rule_limit_price"):
                    self.assertEqual("user_config", row["rule_limit_price_provenance"])

    def test_closing_legs_flip_the_side_and_close(self):
        for case, row in self.all_rows():
            for leg in row.get("close_legs", []):
                with self.subTest(case=case["name"], leg=leg["option_id"]):
                    self.assertEqual("close", leg["position_effect"])
                    held = next(p for p in case["input"]["positions"] if p["option_id"] == leg["option_id"])
                    self.assertEqual("sell" if held["side"] == "long" else "buy", leg["side"])

    def test_no_advice_wording_in_any_output(self):
        banned = re.compile(r"\b(you should|i recommend|we recommend|should (sell|buy|close|roll))\b", re.I)
        for case in load("run.json"):
            text = json.dumps(option_exits.run("run", case["input"]))
            with self.subTest(case=case["name"]):
                self.assertIsNone(banned.search(text))

    def test_unset_rules_are_never_invented(self):
        out = option_exits.run("run", {"as_of": "2026-11-16", "positions": [amd()],
                                       "quotes": {"o1": {"bid": "0.01", "ask": "9.00"}}})
        self.assertEqual({}, out["rules_applied"])
        self.assertEqual([], out["fired"])
        self.assertTrue(any("No exit rules" in n for n in out["notes"]))


class AssetTemplate(unittest.TestCase):
    """assets/options-exits.example.toml ships every financial value UNSET and parses with kitconfig."""

    def test_template_parses_and_every_rule_is_unset(self):
        import kitconfig  # shared/scripts (synced into the skill at release)
        with open(os.path.join(SKILL, "assets", "options-exits.example.toml"), encoding="utf-8") as fh:
            text = fh.read()
        exits = kitconfig.run("get", {"text": text, "section": "options.exits"})
        self.assertTrue(exits["ok"], exits)
        self.assertEqual([], exits["invalid"])
        self.assertEqual(sorted(["profit_target_pct", "stop_loss_pct", "time_stop_dte", "max_hold_days",
                                 "exit_price_rule"]), sorted(exits["unset"]))
        monitor = kitconfig.run("get", {"text": text, "section": "options.monitor"})
        self.assertEqual({"radar_days": "7"}, monitor["section"])

    def test_the_m1_pasted_rules_feed_the_script(self):
        import kitconfig
        pasted = ("# robinhood-skills:config\n[options.exits]\nprofit_target_pct = \"50\"\nstop_loss_pct = \"40\"\n"
                  "time_stop_dte = \"7\"\nmax_hold_days = \"UNSET\"\nexit_price_rule = \"bid\"\n")
        section = kitconfig.run("get", {"text": pasted, "section": "options.exits"})
        self.assertEqual(["max_hold_days"], section["unset"])
        out = option_exits.run("run", {"as_of": "2026-11-16", "rules": section["section"], "positions": [amd()],
                                       "quotes": {"o1": {"bid": "3.30", "ask": "3.40"}}})
        self.assertEqual(["max_hold_days"], out["not_configured"])
        self.assertEqual("3.30", out["fired"][0]["rule_limit_price"])


class ContractTests(unittest.TestCase):
    def cli(self, *args, **kw):
        env = dict(os.environ, PYTHONPATH=SHARED)
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=kw.get("stdin", ""), env=env,
                              capture_output=True, text=True, timeout=120)

    def test_selftest_exits_zero(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        self.assertTrue(json.loads(res.stdout)["ok"])

    def test_schema_prints_json_schemas_for_every_op(self):
        doc = json.loads(self.cli("--schema").stdout)
        self.assertEqual(sorted(option_exits.OPS), sorted(doc["ops"]))
        self.assertIn("input", doc["ops"]["run"])
        self.assertIn("output", doc["ops"]["run"])

    def test_run_over_stdin(self):
        case = load("run.json")[0]
        res = self.cli("run", stdin=json.dumps(case["input"]))
        self.assertEqual(0, res.returncode)
        self.assertTrue(strict(case["expected"], json.loads(res.stdout)))

    def test_bad_json_missing_op_and_unknown_op_still_emit_json_and_exit_zero(self):
        for args, stdin, code in ((["run"], "{not json", "BAD_JSON"), ([], "", "MISSING_OP"),
                                  (["explode"], "{}", "UNKNOWN_OP")):
            res = self.cli(*args, stdin=stdin)
            self.assertEqual(0, res.returncode, res.stderr)
            out = json.loads(res.stdout)
            self.assertFalse(out["ok"])
            self.assertEqual(code, out["errors"][0]["code"])

    def test_source_is_stdlib_py39_ascii_and_writes_no_files(self):
        with open(SCRIPT, "rb") as fh:
            raw = fh.read()
        raw.decode("ascii")
        tree = ast.parse(raw.decode("ascii"), feature_version=(3, 9))
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
