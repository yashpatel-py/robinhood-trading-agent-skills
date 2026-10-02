"""Tests for skills/robinhood-options-screener/scripts/options_screen.py (preflight and screen).

Unofficial; not affiliated with Robinhood Markets, Inc. Stdlib unittest only.

Goldens live in tests/golden/options_screen/ and were hand-computed from the owner's formula tables.
Parity tests check the screener's payoff numbers against shared/scripts/options_math.py, and the
config tests check the shipped template against shared/scripts/kitconfig.py, so the three can't drift.
"""

import ast
import copy
import glob
import json
import os
import re
import subprocess
import sys
import unittest
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "robinhood-options-screener")
SCRIPT = os.path.join(SKILL, "scripts", "options_screen.py")
TEMPLATE = os.path.join(SKILL, "assets", "options-screener.example.toml")
SHARED = os.path.join(ROOT, "shared", "scripts")
GOLDEN = os.path.join(ROOT, "tests", "golden", "options_screen")
sys.path.insert(0, os.path.dirname(SCRIPT))
sys.path.insert(1, SHARED)

import options_screen as scr  # noqa: E402

try:
    import options_math  # noqa: E402
except ImportError:  # pragma: no cover - shared/ is always present in the repo
    options_math = None
try:
    import kitconfig  # noqa: E402
except ImportError:  # pragma: no cover
    kitconfig = None

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}


def strict(expected, actual):
    """Every key in expected matches exactly (types included); lists match in length and order."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and strict(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            strict(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def load(name):
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as fh:
        return json.load(fh)


def apply_patch(base, case):
    inp = copy.deepcopy(base)
    for key in case.get("remove", []):
        inp.pop(key, None)
    for key, val in case.get("patch", {}).items():
        if key == "criteria":
            inp["criteria"].update(copy.deepcopy(val))
        else:
            inp[key] = copy.deepcopy(val)
    return inp


def base_input():
    return copy.deepcopy(load("screen.json")["base"])


class GoldenTests(unittest.TestCase):
    def test_preflight_goldens(self):
        cases = load("preflight.json")["cases"]
        self.assertGreaterEqual(len(cases), 15)
        for case in cases:
            with self.subTest(case=case["name"]):
                out = scr.run(case["op"], case["input"])
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1, ensure_ascii=False))
                self.assertFalse(scr.schema_errors(out, scr.SCHEMAS["preflight"]["output"]))

    def test_screen_goldens(self):
        doc = load("screen.json")
        self.assertGreaterEqual(len(doc["cases"]), 60)
        for case in doc["cases"]:
            with self.subTest(case=case["name"]):
                inp = apply_patch(doc["base"], case)
                out = scr.run(case["op"], inp)
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1, ensure_ascii=False)[:6000])
                self.assertFalse(scr.schema_errors(out, scr.SCHEMAS["screen"]["output"]))


class SpecChecks(unittest.TestCase):
    """The numbers and behaviors the build spec names explicitly (section B.6)."""

    def screen(self, **patch):
        inp = base_input()
        for key, val in patch.items():
            if key == "criteria":
                inp["criteria"].update(val)
            else:
                inp[key] = val
        return scr.run("screen", inp)

    def test_long_call_breakeven_required_move_cost(self):
        cand = self.screen()["candidates"][0]
        self.assertEqual("171.30", cand["breakeven"])
        self.assertEqual(6.1, cand["required_move_pct"])
        self.assertEqual("630.00", cand["cost_usd"])

    def test_spread_risks_80_to_make_20(self):
        out = self.screen(criteria={"structure": "debit_call_spread", "spread_width_min": "1", "spread_width_max": "1",
                                    "max_spread_pct": "10"},
                          contracts=[{"option_id": "l", "symbol": "AMD", "type": "call", "strike": "165",
                                      "expiration": "2026-12-18", "bid": "2.50", "ask": "2.60", "delta": "0.40",
                                      "open_interest": 500},
                                     {"option_id": "s", "symbol": "AMD", "type": "call", "strike": "166",
                                      "expiration": "2026-12-18", "bid": "1.80", "ask": "1.90", "delta": "0.35",
                                      "open_interest": 500}])
        rr = out["candidates"][0]["risk_reward"]
        self.assertEqual(("80.00", "20.00"), (rr["risk_usd"], rr["reward_usd"]))
        self.assertIn("$80.00", rr["text"])

    @staticmethod
    def chain(typ, rows):
        return [{"option_id": "%s%s" % (typ[0], strike), "symbol": "AMD", "type": typ, "strike": strike,
                 "expiration": "2026-12-18", "bid": bid, "ask": ask, "delta": delta, "open_interest": 500}
                for strike, bid, ask, delta in rows]

    def test_in_the_money_call_spread_is_past_breakeven_not_a_needed_fall(self):
        # AMD 161.40. 155/160C at 10.00 - 7.20 = 2.80 breaks even at 157.80: it makes money at expiration if
        # AMD does not move and can absorb a 2.2% drop. 160/165C at 7.40 - 4.90 = 2.50 still needs +0.7%.
        out = self.screen(criteria={"structure": "debit_call_spread", "spread_width_min": "5",
                                    "spread_width_max": "5", "delta_min": "0.20", "delta_max": "0.60",
                                    "max_spread_pct": "10"},
                          contracts=self.chain("call", [("155", "9.80", "10.00", "0.60"),
                                                        ("160", "7.20", "7.40", "0.52"),
                                                        ("165", "4.90", "5.10", "0.45")]))
        itm, otm = out["candidates"]
        self.assertEqual(("AMD 2026-12-18 155/160C debit call spread", 1), (itm["label"], itm["rank"]))
        self.assertEqual(("AMD 2026-12-18 160/165C debit call spread", 2), (otm["label"], otm["rank"]))
        self.assertEqual("157.80", itm["breakeven"])
        self.assertEqual(-2.2, itm["required_move_pct"])  # the signed price move, as options_math.py reports it
        self.assertIs(True, itm["past_breakeven"])
        self.assertEqual((0.0, 2.2), (itm["move_needed_pct"], itm["breakeven_cushion_pct"]))
        self.assertEqual("Breakeven already passed: the stock can fall 2.2% by expiration (32 days) before the "
                         "position loses money", itm["move_text"])
        self.assertIs(False, otm["past_breakeven"])
        self.assertEqual((0.7, 0.7, None), (otm["required_move_pct"], otm["move_needed_pct"],
                                            otm["breakeven_cushion_pct"]))
        self.assertEqual("Required move +0.7% (a rise) in 32 days", otm["move_text"])
        self.assertIn("candidates already past breakeven come first", out["sort"]["disclosure"])
        self.assertFalse(scr.schema_errors(out, scr.SCHEMAS["screen"]["output"]))

    def test_in_the_money_put_spreads_sort_by_cushion_before_moves_still_needed(self):
        # abs(signed move) would list 165/160P (0.5) first, then 160/155P (1.9), then 170/165P (2.7);
        # the first two of those are already past breakeven, so they lead, the larger cushion first.
        out = self.screen(criteria={"structure": "debit_put_spread", "spread_width_min": "5",
                                    "spread_width_max": "5", "delta_min": "0.20", "delta_max": "0.75",
                                    "max_spread_pct": "10"},
                          contracts=self.chain("put", [("170", "10.00", "10.20", "-0.70"),
                                                       ("165", "6.00", "6.20", "-0.55"),
                                                       ("160", "3.40", "3.60", "-0.42"),
                                                       ("155", "1.95", "2.10", "-0.30")]))
        rows = [(c["label"].split()[2], c["breakeven"], c["past_breakeven"], c["move_needed_pct"],
                 c["breakeven_cushion_pct"], c["required_move_pct"]) for c in out["candidates"]]
        self.assertEqual([("170/165P", "165.80", True, 0.0, 2.7, 2.7),
                          ("165/160P", "162.20", True, 0.0, 0.5, 0.5),
                          ("160/155P", "158.35", False, 1.9, None, -1.9)], rows)
        texts = [c["move_text"] for c in out["candidates"]]
        self.assertTrue(texts[0].startswith("Breakeven already passed: the stock can rise 2.7%"), texts[0])
        self.assertEqual("Required move -1.9% (a fall) in 32 days", texts[2])

    def test_out_of_the_money_long_options_still_show_the_move_they_need(self):
        call = self.screen()["candidates"][0]
        self.assertEqual((6.1, False, 6.1, None), (call["required_move_pct"], call["past_breakeven"],
                                                   call["move_needed_pct"], call["breakeven_cushion_pct"]))
        put = self.screen(criteria={"structure": "long_put"},
                          contracts=self.chain("put", [("155", "4.00", "4.20", "-0.35")]))["candidates"][0]
        self.assertEqual((-6.6, False, 6.6), (put["required_move_pct"], put["past_breakeven"],
                                              put["move_needed_pct"]))
        self.assertEqual("Required move -6.6% (a fall) in 32 days", put["move_text"])

    def test_missing_delta_or_oi_is_rejected_by_name(self):
        inp = base_input()
        for c in inp["contracts"]:
            c.pop("delta")
            c.pop("open_interest")
        out = scr.run("screen", inp)
        self.assertEqual([], out["candidates"])
        self.assertIn("DELTA_OI_UNAVAILABLE", out["counts_by_reason"])
        self.assertIn("DELTA_OI_UNAVAILABLE", out["status_line"])
        self.assertEqual("UNKNOWN", out["report_status"])

    def test_unset_is_a_stop_never_a_default(self):
        for key in scr.CRITERIA_KEYS:
            if key in ("symbols_allowlist", "symbols_blocklist", "sort_by", "spread_width_min", "spread_width_max"):
                continue
            with self.subTest(key=key):
                out = self.screen(criteria={key: "UNSET"})
                self.assertEqual("stopped", out["status"])
                self.assertEqual("CRITERIA_UNSET", out["stopped"]["code"])
                self.assertIn("options.criteria." + key, out["stopped"]["fields"])
                self.assertEqual([], out["candidates"])

    def test_iv_rank_set_means_enforced_or_stopped(self):
        out = self.screen(criteria={"iv_rank_min": "20", "iv_rank_max": "OFF"})
        self.assertEqual("IV_RANK_SOURCE_UNVERIFIED", out["stopped"]["code"])

    def test_stop_at_or_beyond_the_premium_is_disclosed_not_refused(self):
        # kitconfig puts no upper bound on options.exits.stop_loss_pct (a short can lose more than its premium),
        # so a shared-section value such as 150 must screen, with the total-loss disclosure on long-only trades.
        base = base_input()["exits"]
        exits = dict(base, stop_loss_pct="150")
        out = self.screen(exits=exits)
        self.assertEqual("candidates", out["status"], out.get("stopped"))
        cand = out["candidates"][0]
        self.assertEqual("0.00", cand["exits"]["stop_price"])
        self.assertIn("STOP_AT_TOTAL_LOSS", cand["flags"])
        self.assertTrue(any("total loss" in n for n in cand["exits"]["notes"]))
        out = scr.run("screen", dict(base_input(), exits=dict(base, stop_loss_pct="0")))
        self.assertNotEqual("candidates", out.get("status"))

    def test_zero_candidates_is_reported_plainly(self):
        out = self.screen(criteria={"max_cost_per_contract_usd": "100.00"})
        self.assertEqual("NO ACTION", out["report_status"])
        self.assertTrue(out["status_line"].startswith("NO ACTION: 0 of 1 contract passed your criteria"))
        self.assertEqual({"COST_PER_CONTRACT": 1}, out["counts_by_reason"])

    def test_sort_is_disclosed(self):
        out = self.screen()
        self.assertEqual("required_move_pct", out["sort"]["by"])
        self.assertIn("not a ranking of quality", out["sort"]["disclosure"])
        self.assertIn("change sort_by in your config", out["sort"]["disclosure"])

    def test_candidate_carries_no_account_number_and_only_review_keys(self):
        cand = self.screen()["candidates"][0]
        allowed = set(scr.SCHEMAS["screen"]["output"]["anyOf"][0]["properties"]["candidates"]["items"]["properties"])
        self.assertTrue(set(cand) <= allowed, set(cand) - allowed)
        params = cand["order_params"]
        self.assertNotIn("account_number", params)
        review_keys = {"account_number", "chain_symbol", "direction", "legs", "market_hours", "price", "quantity",
                       "stop_price", "time_in_force", "type", "underlying_type"}
        self.assertTrue(set(params) <= review_keys, set(params) - review_keys)
        self.assertEqual({"option_id", "side", "position_effect", "ratio_quantity"}, set(params["legs"][0]))
        self.assertIsInstance(params["legs"][0]["ratio_quantity"], int)
        self.assertIsInstance(params["quantity"], str)

    def test_cost_checks_never_use_the_mid(self):
        # mid 6.20 -> $620 would pass a $625 cap; the natural price 6.30 -> $630 must not.
        out = self.screen(criteria={"max_cost_per_contract_usd": "625.00"},
                          entry={"entry_price_rule": "mid", "contracts_per_entry": "1"})
        self.assertEqual([], out["candidates"])

    def test_every_rejected_contract_has_a_reason(self):
        inp = base_input()
        inp["contracts"].append(dict(inp["contracts"][0], option_id="x2", strike="200", bid="0.10", ask="0.40",
                                     delta="0.05", open_interest=3))
        out = scr.run("screen", inp)
        for row in out["rejected"]:
            self.assertTrue(row["reasons"])
            for reason in row["reasons"]:
                self.assertRegex(reason, r"^[A-Z_]+( |$)")


class ParityWithOptionsMath(unittest.TestCase):
    """The screener's payoff numbers must equal shared/scripts/options_math.py payoff (natural basis)."""

    SINGLES = [("call", "165", "6.10", "6.30", "161.40"), ("call", "30", "1.95", "2.05", "31.20"),
               ("put", "155", "4.00", "4.20", "161.40"), ("put", "30", "0.80", "0.85", "31.20"),
               ("call", "230", "12.00", "12.20", "228.10"), ("put", "240", "15.10", "15.60", "228.10")]
    SPREADS = [("call", "165", "2.50", "2.60", "166", "1.80", "1.90", "161.40"),
               ("call", "160", "8.60", "8.80", "165", "6.10", "6.30", "161.40"),
               ("put", "160", "4.80", "5.00", "155", "3.10", "3.30", "161.40"),
               ("put", "32", "1.40", "1.45", "30", "0.60", "0.65", "31.20")]

    def setUp(self):
        if options_math is None:
            self.skipTest("shared/scripts/options_math.py not importable")

    def run_screen(self, structure, contracts, price, width=None):
        inp = base_input()
        sym = "XYZ"
        inp["underlyings"] = {sym: {"price": price, "avg_volume": "5000000"}}
        inp["earnings"] = {sym: {"reports": []}}
        crit = {"structure": structure, "delta_min": "0", "delta_max": "1", "max_spread_pct": "50",
                "min_open_interest": "0", "max_cost_per_contract_usd": "5000.00", "max_position_pct": "100",
                "price_min": "1.00", "max_concurrent": "10"}
        if width:
            crit.update(spread_width_min=width, spread_width_max=width)
        inp["criteria"].update(crit)
        inp["account"] = {"total_value_usd": "100000.00", "buying_power_usd": "50000.00", "open_option_positions": 0}
        for c in contracts:
            c.update(symbol=sym, expiration="2026-12-18", delta="0.5", open_interest=10)
        inp["contracts"] = contracts
        out = scr.run("screen", inp)
        self.assertEqual("candidates", out["status"], json.dumps(out)[:3000])
        return out["candidates"]

    def test_single_legs(self):
        for typ, strike, bid, ask, spot in self.SINGLES:
            with self.subTest(typ=typ, strike=strike):
                cand = self.run_screen("long_" + typ, [{"option_id": "a", "type": typ, "strike": strike, "bid": bid,
                                                        "ask": ask}], spot)[0]
                ref = options_math.run("payoff", {"legs": [{"type": typ, "side": "buy", "strike": strike, "bid": bid,
                                                            "ask": ask}], "quantity": "1", "underlying_price": spot})
                self.assertEqual(ref["breakevens"], [cand["breakeven"]])
                self.assertEqual(ref["required_move_pct"], cand["required_move_pct"])
                self.assertEqual(ref["cost_usd"], cand["cost_usd"])
                self.assertEqual(ref["max_loss_usd"], cand["max_loss_usd"])
                self.assertEqual(ref["max_gain_usd"], cand["max_gain_usd"])
                self.assertEqual(ref["spread_pct_each_leg"][0], cand["spread_pct"])

    def test_debit_spreads(self):
        for typ, ls, lb, la, ss, sb, sa, spot in self.SPREADS:
            with self.subTest(typ=typ, long=ls, short=ss):
                width = str(abs(Decimal(ls) - Decimal(ss)))
                cands = self.run_screen("debit_%s_spread" % typ,
                                        [{"option_id": "l", "type": typ, "strike": ls, "bid": lb, "ask": la},
                                         {"option_id": "s", "type": typ, "strike": ss, "bid": sb, "ask": sa}],
                                        spot, width)
                cand = [c for c in cands if c["legs"][0]["option_id"] == "l"][0]
                ref = options_math.run("payoff", {"legs": [{"type": typ, "side": "buy", "strike": ls, "bid": lb,
                                                            "ask": la},
                                                           {"type": typ, "side": "sell", "strike": ss, "bid": sb,
                                                            "ask": sa}],
                                                  "quantity": "1", "underlying_price": spot})
                self.assertEqual("debit_%s_spread" % typ, ref["structure"])
                self.assertEqual(ref["natural_net"], cand["natural_price"])
                self.assertEqual(ref["breakevens"], [cand["breakeven"]])
                self.assertEqual(ref["required_move_pct"], cand["required_move_pct"])
                self.assertEqual(ref["max_gain_usd"], cand["max_gain_usd"])
                self.assertEqual(ref["max_loss_usd"], cand["max_loss_usd"])
                self.assertEqual(ref["risk_reward"]["reward_per_dollar_risked"],
                                 cand["risk_reward"]["reward_per_dollar_risked"])


class ConfigTemplateTests(unittest.TestCase):
    """The shipped template, kitconfig's schema and the script's own key list agree."""

    def setUp(self):
        if kitconfig is None:
            self.skipTest("shared/scripts/kitconfig.py not importable")
        with open(TEMPLATE, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_template_parses_and_every_value_is_unset(self):
        cfg = kitconfig.parse_text(self.text)
        crit, entry = cfg["options"]["criteria"], cfg["options"]["entry"]
        financial = {k: v for k, v in crit.items() if k not in ("symbols_allowlist", "symbols_blocklist", "sort_by")}
        self.assertTrue(all(v == "UNSET" for v in financial.values()), financial)
        self.assertTrue(all(v == "UNSET" for v in entry.values()), entry)
        self.assertEqual([], crit["symbols_allowlist"])
        self.assertEqual([], crit["symbols_blocklist"])
        self.assertNotIn("exits", cfg["options"], "the shared [options.exits] block ships commented out")

    def test_template_keys_match_kitconfig_and_the_script(self):
        cfg = kitconfig.parse_text(self.text)
        for section, script_keys in (("criteria", scr.CRITERIA_KEYS), ("entry", scr.ENTRY_KEYS)):
            with self.subTest(section=section):
                schema = set(kitconfig.SECTION_SCHEMAS["options." + section]["keys"])
                self.assertEqual(schema, set(cfg["options"][section]))
                self.assertEqual(schema, set(script_keys))
        exits = set(kitconfig.SECTION_SCHEMAS["options.exits"]["keys"]) - {"exit_price_rule"}
        self.assertEqual(exits, set(scr.EXIT_KEYS))

    def test_template_validates_as_unset_not_as_errors(self):
        out = kitconfig.run("validate", {"text": self.text, "sections": ["options.criteria", "options.entry"]})
        self.assertTrue(out["ok"])
        self.assertFalse(out["valid"])
        self.assertEqual([], out["errors"])
        self.assertEqual([], out["warnings"])
        self.assertIn("options.criteria.structure", out["missing"])

    def test_commented_exits_block_is_valid_when_uncommented(self):
        lines = self.text.splitlines()
        start = lines.index("# [options.exits]")
        block = [ln[2:] if ln.startswith("# ") else ln.lstrip("#") for ln in lines[start:]]
        text = self.text + "\n" + "\n".join(block) + "\n"
        cfg = kitconfig.parse_text(text)
        self.assertEqual({"profit_target_pct", "stop_loss_pct", "time_stop_dte", "max_hold_days"},
                         set(cfg["options"]["exits"]))

    def test_filled_template_validates_and_screens(self):
        values = {"max_position_pct": "30", "max_concurrent": "3", "max_cost_per_contract_usd": "700.00",
                  "reserve_cash_usd": "200.00", "price_min": "20.00", "price_max": "700.00",
                  "min_avg_volume": "1000000", "structure": "long_call", "spread_width_min": "OFF",
                  "spread_width_max": "OFF", "dte_min": "14", "dte_max": "45", "delta_min": "0.30",
                  "delta_max": "0.60", "max_spread_pct": "5", "min_open_interest": "100", "iv_rank_min": "OFF",
                  "iv_rank_max": "OFF", "earnings_policy": "avoid", "earnings_buffer_days": "2",
                  "scan_sessions": "regular_hours_only", "entry_price_rule": "natural", "contracts_per_entry": "1"}
        text = self.text
        for key, val in values.items():
            text, n = re.subn(r'(?m)^%s = "UNSET"' % key, '%s = "%s"' % (key, val), text)
            self.assertEqual(1, n, key)
        text += '\n[options.exits]\nprofit_target_pct = "50"\nstop_loss_pct = "40"\ntime_stop_dte = "7"\n' \
                'max_hold_days = "30"\n'
        out = kitconfig.run("validate", {"text": text,
                                         "sections": ["options.criteria", "options.entry", "options.exits"]})
        self.assertTrue(out["valid"], out)
        sections = {s: kitconfig.run("get", {"text": text, "section": "options." + s})["section"]
                    for s in ("criteria", "entry", "exits")}
        inp = base_input()
        inp["criteria"], inp["entry"], inp["exits"] = sections["criteria"], sections["entry"], sections["exits"]
        out = scr.run("screen", inp)
        self.assertEqual("candidates", out["status"], out.get("stopped"))

    def test_owner_rationale_kept_and_v1_keys_removed(self):
        prose = " ".join(re.sub(r"(?m)^[ \t]*#|(?<=\S)[ \t]+#", " ", self.text).split())
        for phrase in ("these numbers *are* the strategy", "open-ended bet", "round-trip cost",
                       "theta decay accelerates into expiry", "overnight gap risk", "unexitable position",
                       "Width x 100 is the ceiling", "IV rank is not IV percentile"):
            self.assertIn(phrase, prose)
        for gone in ("max_premium_per_contract", "pdt_budget", "scan_interval", "\naccount ="):
            self.assertNotIn(gone, self.text)


class SkillFileTests(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(SKILL, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_every_tool_named_in_the_skill_exists_or_is_named_as_absent(self):
        with open(os.path.join(ROOT, "connector", "tools.snapshot.json"), encoding="utf-8") as fh:
            names = {t["name"] for t in json.load(fh)["tools"]}
        pattern = re.compile(r"\b(?:get|review|place|cancel|create|update|delete|add|remove|follow|unfollow|mark|run|"
                             r"preview|exercise|replace)_[a-z0-9_]+\b")
        files = ["SKILL.md"] + [os.path.relpath(p, SKILL) for p in glob.glob(os.path.join(SKILL, "references", "*.md"))]
        for rel in files:
            if rel.endswith("connector-rules.md"):
                continue  # synced from shared/, checked by tools/check_drift.py
            text = self.read(rel)
            for m in pattern.finditer(text):
                name = m.group(0)
                if name == "get_market_hours":
                    window = text[max(0, m.start() - 80):m.end() + 80].lower()
                    self.assertTrue("there is no" in window or "never existed" in window, (rel, window))
                    continue
                self.assertIn(name, names, "%s names unknown tool %s" % (rel, name))

    def test_old_owner_files_are_gone(self):
        for rel in ("robinhood-trading/references/options-criteria.md",
                    "robinhood-trading/references/options-workflow.md"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_no_pdt_counting_and_the_screener_never_places(self):
        text = "\n".join(self.read(r) for r in ("SKILL.md", "references/workflow.md", "references/formulas.md"))
        self.assertNotRegex(text.lower(), r"pdt_budget|day trades? (remaining|left|budget)|3-in-5")
        self.assertIn("never calls `place_option_order`", self.read("SKILL.md"))

    def test_frontmatter_shape(self):
        text = self.read("SKILL.md")
        head = text.split("---", 2)[1]
        self.assertRegex(head, r"(?m)^name: robinhood-options-screener$")
        self.assertRegex(head, r"(?m)^description: >-$")
        self.assertRegex(head, r'(?m)^  version: "2\.0\.0"$')
        self.assertIn("Not for", head)
        self.assertLessEqual(len(text.splitlines()), 300)


class ContractTests(unittest.TestCase):
    def cli(self, *args, **kw):
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=kw.get("stdin", ""),
                              capture_output=True, text=True, timeout=120)

    def test_selftest_exits_zero(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)

    def test_schema_prints_json_schemas_for_every_op(self):
        doc = json.loads(self.cli("--schema").stdout)
        self.assertEqual(sorted(scr.OPS), sorted(doc["ops"]))
        for pair in doc["ops"].values():
            self.assertIn("$schema", pair["input"])
            self.assertIn("$schema", pair["output"])

    def test_golden_inputs_match_the_input_schema(self):
        doc = load("screen.json")
        for case in doc["cases"]:
            inp = apply_patch(doc["base"], case)
            if case["expected"].get("ok") is False:
                continue
            with self.subTest(case=case["name"]):
                self.assertEqual([], scr.schema_errors(inp, scr.SCHEMAS["screen"]["input"]))

    def test_cli_round_trip_and_errors(self):
        res = self.cli("screen", stdin=json.dumps(base_input()))
        self.assertEqual(0, res.returncode)
        self.assertEqual("171.30", json.loads(res.stdout)["candidates"][0]["breakeven"])
        self.assertEqual("BAD_JSON", json.loads(self.cli("screen", stdin="{").stdout)["errors"][0]["code"])
        self.assertEqual("UNKNOWN_OP", json.loads(self.cli("place", stdin="{}").stdout)["errors"][0]["code"])
        self.assertEqual("MISSING_OP", json.loads(self.cli().stdout)["errors"][0]["code"])

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
        self.assertFalse(re.search(r"\bopen\([^)]*['\"]w", source), "the script must not write files")


if __name__ == "__main__":
    unittest.main()
