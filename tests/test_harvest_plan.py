"""Tests for skills/robinhood-tax-loss-harvesting/scripts/harvest_plan.py (lot-level harvest candidates, build spec B.2 workflow B)."""

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
NAME = "harvest_plan"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SHARED)
sys.path.insert(0, SCRIPTS)

import harvest_plan  # noqa: E402

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
                out = harvest_plan.run(case["op"], case["input"])
                self.assertTrue(subset(case["expected"], out),
                                "expected subset %s\n got %s" % (case["expected"], json.dumps(out, indent=1)))
                self.check_extra(case, out)
                count += 1
        self.assertGreaterEqual(count, 8)

    def check_extra(self, case, out):
        pass


class ScanTests(unittest.TestCase):
    def test_rules_note_cites_the_limits_and_sources(self):
        out = harvest_plan.run("run", {"as_of": "2026-11-16", "accounts": [], "lots": []})
        text = " ".join(out["rules_note"])
        for needle in ("$3,000", "$1,500", "Topic 409", "Rev. Rul. 2008-5", "Pub 550", "2026-12-31"):
            self.assertIn(needle, text)
        self.assertEqual("none", out["status"])
        self.assertTrue(out["status_line"].startswith("NO ACTION: Dollars at stake:"))

    def test_candidates_never_include_retirement_accounts(self):
        for _, case in golden_cases():
            out = harvest_plan.run("run", case["input"])
            if not out.get("ok"):
                continue
            retirement = set(a["last4"] for a in case["input"].get("accounts", []) if a["type"] == "retirement")
            for cand in out["candidates"]:
                self.assertNotIn(cand["account_last4"], retirement)

    def test_golden_outputs_match_the_output_schema_and_status_line_shape(self):
        for _, case in golden_cases():
            out = harvest_plan.run(case["op"], case["input"])
            self.assertEqual([], harvest_plan.schema_errors(out, harvest_plan.SCHEMAS["run"]["output"]), case["name"])
            if out.get("ok"):
                self.assertRegex(out["status_line"],
                                 r"^(CONFLICT|UNKNOWN|POSSIBLE|CLEAR|NO ACTION): Dollars at stake: ")

    def test_an_account_left_out_of_the_read_scope_never_yields_clear(self):
        # "Status is never clear unless every account was read" (build spec; wash-sweep.md), whichever account it is.
        for _, case in golden_cases():
            base = harvest_plan.run(case["op"], case["input"])
            if not base.get("ok"):
                continue
            for i, acct in enumerate(case["input"].get("accounts") or []):
                data = json.loads(json.dumps(case["input"]))
                data["accounts"][i]["read_status"] = "not_in_scope"
                out = harvest_plan.run("run", data)
                with self.subTest(case=case["name"], account=acct["last4"]):
                    self.assertNotEqual("clear", out["status"])
                    self.assertIn(acct["last4"], out["accounts_not_in_scope"])
                    self.assertIn("••••" + acct["last4"], out["status_line"])
                    if base["status"] == "clear":
                        self.assertEqual("clear_in_scope", out["status"])
                        self.assertIn("accounts read", out["status_line"])

    def test_wash_dollars_never_exceed_the_candidate_loss(self):
        for _, case in golden_cases():
            out = harvest_plan.run(case["op"], case["input"])
            if not out.get("ok"):
                continue
            for c in out["candidates"]:
                if c["disallowed_if_sold_now_usd"] is None:
                    continue
                harvest, dis = float(c["harvestable_loss_usd"]), float(c["disallowed_if_sold_now_usd"])
                self.assertTrue(0 <= float(c["permanent_if_sold_now_usd"]) <= dis <= harvest, case["name"])
                self.assertGreaterEqual(float(c["net_loss_after_wash_usd"]), 0, case["name"])
            t = out["totals"]
            self.assertLessEqual(float(t["permanent_if_sold_now_usd"]), float(t["harvestable_loss_usd"]), case["name"])

    def test_a_sweep_is_never_applied_to_another_accounts_lots(self):
        lots = [{"account_last4": a, "symbol": "TSLA", "open_lot_id": a + "-1", "open_date": "2026-06-02",
                 "quantity": "10", "cost_per_share": "340.00"} for a in ("M7Q5", "X4F1")]
        sweep = {"ok": True, "mode": "sale", "symbol": "TSLA", "sale_account_last4": "M7Q5", "status": "conflict",
                 "disallowed_total_usd": "390.00", "permanent_total_usd": "390.00",
                 "per_lot": {"M7Q5-1": {"disallowed_usd": "390.00", "permanent_usd": "390.00"}}}
        accounts = [{"last4": "M7Q5", "type": "taxable"}, {"last4": "X4F1", "type": "taxable", "agentic": True}]
        for key in ("TSLA", "M7Q5:TSLA", "X4F1:TSLA"):
            out = harvest_plan.run("run", {"as_of": "2026-11-16", "accounts": accounts, "lots": lots,
                                           "prices": {"TSLA": "262.00"}, "wash": {key: sweep}})
            by_acct = {c["account_last4"]: c for c in out["candidates"]}
            with self.subTest(key=key):
                self.assertEqual("not_checked", by_acct["X4F1"]["wash_status"])
                self.assertIsNone(by_acct["X4F1"]["disallowed_if_sold_now_usd"])
                self.assertIn('"X4F1:TSLA"', by_acct["X4F1"]["wash_note"])
                self.assertLessEqual(float(out["totals"]["permanent_if_sold_now_usd"]), 390.0)
                if key != "X4F1:TSLA":
                    self.assertEqual("390.00", by_acct["M7Q5"]["permanent_if_sold_now_usd"])

    @staticmethod
    def _sweep(lots_sold, sale_acct):
        """A real wash_sale.py sale check: TSLA at $262.00, the Roth IRA bought 5 on 2026-11-06."""
        sys.path.insert(0, SHARED)
        import wash_sale  # noqa: E402
        return wash_sale.run("run", {
            "mode": "sale", "as_of": "2026-11-16", "symbol": "TSLA",
            "accounts": [{"last4": a, "type": t, "read_status": "complete"}
                         for a, t in (("X4F1", "taxable"), ("M7Q5", "taxable"), ("P0Z9", "retirement"))],
            "sale": {"date": "2026-11-16", "account_last4": sale_acct, "price_per_share": "262.00",
                     "lots_sold": lots_sold},
            "buys": [{"account_last4": "P0Z9", "date": "2026-11-06", "shares": "5"}]})

    LOTS = [{"account_last4": "M7Q5", "symbol": "TSLA", "open_lot_id": "L7", "open_date": "2026-06-02",
             "quantity": "20", "cost_per_share": "340.00"},
            {"account_last4": "X4F1", "symbol": "TSLA", "open_lot_id": "A1", "open_date": "2026-09-01",
             "quantity": "10", "cost_per_share": "300.00"}]
    ACCOUNTS = [{"last4": "X4F1", "type": "taxable", "agentic": True}, {"last4": "M7Q5", "type": "taxable"},
                {"last4": "P0Z9", "type": "retirement"}]

    def sold(self, *accts):
        return [{"lot_id": l["open_lot_id"], "account_last4": l["account_last4"], "acquired": l["open_date"],
                 "shares": l["quantity"], "cost_per_share": l["cost_per_share"]}
                for l in self.LOTS if l["account_last4"] in accts]

    def test_one_combined_sweep_prices_every_accounts_lots_once(self):
        combined = self._sweep(self.sold("M7Q5", "X4F1"), "M7Q5")
        self.assertTrue(combined["ok"], combined)
        out = harvest_plan.run("run", {"as_of": "2026-11-16", "accounts": self.ACCOUNTS, "lots": self.LOTS,
                                       "prices": {"TSLA": "262.00"}, "wash": {"TSLA": combined}})
        by_acct = {c["account_last4"]: c for c in out["candidates"]}
        self.assertEqual({"conflict"}, {c["wash_status"] for c in by_acct.values()})
        # The 5 Roth shares wash the earliest-acquired lot (L7, $78.00 a share) once: $390.00, not once per account.
        self.assertEqual("390.00", out["totals"]["disallowed_if_sold_now_usd"])
        self.assertEqual(combined["disallowed_total_usd"], out["totals"]["disallowed_if_sold_now_usd"])
        self.assertEqual("0.00", by_acct["X4F1"]["disallowed_if_sold_now_usd"])

    def test_separate_sweeps_that_share_a_buy_are_never_added(self):
        wash = {"M7Q5:TSLA": self._sweep(self.sold("M7Q5"), "M7Q5"),
                "X4F1:TSLA": self._sweep(self.sold("X4F1"), "X4F1")}
        out = harvest_plan.run("run", {"as_of": "2026-11-16", "accounts": self.ACCOUNTS, "lots": self.LOTS,
                                       "prices": {"TSLA": "262.00"}, "wash": wash,
                                       "ytd_realized": {"M7Q5": "100.00"}})
        by_acct = {c["account_last4"]: c for c in out["candidates"]}
        self.assertEqual("390.00", by_acct["M7Q5"]["disallowed_if_sold_alone_usd"])
        self.assertEqual("190.00", by_acct["X4F1"]["disallowed_if_sold_alone_usd"])
        for c in by_acct.values():
            self.assertIsNone(c["disallowed_if_sold_now_usd"])
            self.assertTrue(c["shared_replacement_buys"])
            self.assertTrue(any("combined sweep" in n for n in c["notes"]))
        self.assertIsNone(out["totals"]["disallowed_if_sold_now_usd"])
        self.assertIsNone(out["totals"]["net_loss_after_wash_usd"])
        self.assertIsNone(out["ytd"]["if_every_candidate_were_sold_usd"])
        self.assertIn("run one combined sweep", out["status_line"])

    def test_robinhood_term_is_shown_and_a_disagreement_is_flagged(self):
        out = harvest_plan.run("run", {
            "as_of": "2026-11-16", "accounts": [{"last4": "M7Q5", "type": "taxable"}], "prices": {"TSLA": "262.00"},
            "lots": [{"account_last4": "M7Q5", "symbol": "TSLA", "open_lot_id": "R", "open_date": "2026-10-10",
                      "quantity": "20", "cost_per_share": "300.00", "term": "Long"}]})
        c = out["candidates"][0]
        # Robinhood's term wins (as in lot_select); the date arithmetic's split stays beside it.
        self.assertEqual(("0.00", "760.00"), (c["short_term_loss_usd"], c["long_term_loss_usd"]))
        self.assertTrue(c["term_split_disputed"])
        self.assertEqual({"short_term_loss_usd": "760.00", "long_term_loss_usd": "0.00"}, c["date_term_split"])
        self.assertTrue(any(n.startswith("TERM_DISAGREES") and "Robinhood's tax documents govern" in n
                            for n in c["notes"]))
        self.assertEqual(1, out["totals"]["term_disagreements"])

    def test_rules_note_warns_that_replacement_lots_hide_deferred_losses(self):
        out = harvest_plan.run("run", {"as_of": "2026-11-16", "accounts": [], "lots": []})
        self.assertTrue(any("Robinhood does not track washes across accounts" in n for n in out["rules_note"]))
        self.assertIn(harvest_plan.REPLACEMENT_NOT_EVALUATED, out["not_evaluated"])


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
            out = harvest_plan.run(case["op"], case["input"])
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

        seeds = [(op, data) for op, data, _ in harvest_plan.EXAMPLES] + [(c["op"], c["input"]) for _, c in golden_cases()]
        for n in range(600):
            op, data = rng.choice(seeds)
            for _ in range(rng.randint(1, 3)):
                data = mutate(data)
            with self.subTest(n=n):
                out = harvest_plan.run(op, data)
                self.assertIsInstance(out, dict)
                self.assertIn("ok", out)
                json.dumps(out)


if __name__ == "__main__":
    unittest.main()
