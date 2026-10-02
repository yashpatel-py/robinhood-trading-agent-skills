"""Tests for shared/scripts/wash_sale.py (the cross-account wash-sale window, build spec B.7.9)."""

import ast
import glob
import itertools
import json
import os
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True  # keep __pycache__ out of the shipped skill folders
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "shared", "scripts")
NAME = "wash_sale"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import wash_sale  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}
HOUSE = [{"last4": "X4F1", "type": "taxable", "read_status": "complete"},
         {"last4": "M7Q5", "type": "taxable", "read_status": "complete"},
         {"last4": "P0Z9", "type": "retirement", "read_status": "complete"}]


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
            doc = json.load(fh)
        for case in doc["cases"]:
            yield os.path.basename(path), case


def sale(buys=None, accounts=None, **extra):
    data = {"mode": "sale", "symbol": "XYZ", "as_of": "2026-11-16", "accounts": accounts or HOUSE,
            "sale": {"date": "2026-11-16", "account_last4": "M7Q5", "price_per_share": "90.00",
                     "lots_sold": [{"lot_id": "A", "acquired": "2025-01-02", "shares": "10", "cost_per_share": "100.00"}]},
            "buys": buys or []}
    data.update(extra)
    return data


class GoldenTests(unittest.TestCase):
    def test_golden_cases(self):
        count = 0
        for fname, case in golden_cases():
            with self.subTest(file=fname, case=case["name"]):
                out = wash_sale.run(case["op"], case["input"])
                if case.get("match") == "exact":
                    self.assertEqual(case["expected"], out)
                else:
                    self.assertTrue(subset(case["expected"], out),
                                    "expected subset %s\n got %s" % (case["expected"], json.dumps(out, indent=1)))
                count += 1
        self.assertGreaterEqual(count, 70)

    def test_every_spec_golden_is_present(self):
        names = " ".join(case["name"] for _, case in golden_cases())
        for needle in ("2027-02-01", "2027-01-04", "2026-10-23", "earliest clean sale 2026-12-07",
                       "clean buy 2026-12-04", "5 of 20", "12 days before", "part of the sale",
                       "one account failed", "excluded account",
                       # review fixes: acquisition order, own purchase, 1.1091-1(e), December look-back, overnight,
                       # options to acquire
                       "earliest-acquired lot has the smaller loss", "no acquisition date",
                       "1.1091-1(e)", "December look-back", "overnight loss sale", "option to acquire"):
            self.assertIn(needle, names)


class RegulationOrderTests(unittest.TestCase):
    """Treas. Reg. 1.1091-1(b)-(e): the order of disposition and acquisition, not the size of the loss."""

    LOTS = [{"lot_id": "A", "acquired": "2024-02-01", "shares": "4", "cost_per_share": "95.00"},
            {"lot_id": "B", "acquired": "2025-03-01", "shares": "4", "cost_per_share": "130.00"},
            {"lot_id": "C", "acquired": "2025-09-01", "shares": "4", "cost_per_share": "110.00"}]

    def test_listing_order_never_changes_the_headline(self):
        seen = set()
        for perm in itertools.permutations(self.LOTS):
            data = sale([{"account_last4": "P0Z9", "date": "2026-11-06", "shares": "6"}])
            data["sale"]["lots_sold"] = list(perm)
            out = wash_sale.run("run", data)
            seen.add((out["disallowed_total_usd"], out["per_lot"]["A"]["washed_shares"],
                      out["per_lot"]["B"]["washed_shares"], out["per_lot"]["C"]["washed_shares"]))
        # A (acquired first) takes 4, B takes 2: 4 x 5.00 + 2 x 40.00 = 100.00; C is untouched
        self.assertEqual({("100.00", "4", "2", "0")}, seen)

    def test_missing_acquisition_date_reports_the_range_not_a_rule(self):
        data = sale([{"account_last4": "P0Z9", "date": "2026-11-06", "shares": "6"}])
        data["sale"]["lots_sold"] = [dict(l) for l in self.LOTS]
        del data["sale"]["lots_sold"][2]["acquired"]
        out = wash_sale.run("run", data)
        self.assertEqual("largest_loss_first", out["matching_order"])
        self.assertEqual("200.00", out["disallowed_total_usd"])  # 4 x 40.00 on B + 2 x 20.00 on C
        self.assertEqual("60.00", out["disallowed_low_usd"])  # 4 x 5.00 on A + 2 x 20.00 on C
        self.assertIn("acquisition date of C is missing", out["matching_note"])
        self.assertNotIn("do not say", out["matching_note"])

    def test_own_purchase_of_a_past_sale_is_never_a_definite_conflict(self):
        data = {"mode": "sale", "symbol": "XYZ", "as_of": "2026-11-16", "accounts": HOUSE,
                "sale": {"date": "2026-11-10", "account_last4": "X4F1", "shares": "10", "realized_usd": "-50.00"},
                "buys": [{"account_last4": "X4F1", "date": "2026-11-02", "shares": "10"}]}
        out = wash_sale.run("run", data)
        self.assertEqual("possible", out["status"])
        self.assertEqual([], out["conflicts"])
        data["sale"]["acquired"] = "2026-11-02"
        self.assertEqual("clear", wash_sale.run("run", data)["status"])
        data["sale"]["acquired"] = "2026-10-01"  # bought elsewhere: the 11-02 buy is a replacement after all
        self.assertEqual("conflict", wash_sale.run("run", data)["status"])

    def test_bad_acquired_lots_sum_is_an_error(self):
        data = {"mode": "sale", "symbol": "XYZ", "as_of": "2026-11-16", "accounts": HOUSE,
                "sale": {"date": "2026-11-10", "account_last4": "X4F1", "shares": "10", "realized_usd": "-50.00",
                         "acquired_lots": [{"acquired": "2026-11-02", "shares": "6"}]}}
        self.assertEqual("BAD_VALUE", wash_sale.run("run", data)["errors"][0]["code"])

    def test_planned_buy_with_a_declared_harvest_or_an_underwater_lot_is_never_clear(self):
        base = {"mode": "planned_buy", "symbol": "TSLA", "as_of": "2026-12-05", "accounts": HOUSE,
                "planned_buy": {"date": "2026-12-05", "account_last4": "P0Z9", "shares": "5",
                                "price_per_share": "262.00"}}
        declared = dict(base, planned_sales=[{"account_last4": "M7Q5", "date": "2026-12-31", "shares": "20",
                                              "loss_per_share": "78.00"}])
        self.assertEqual("conflict", wash_sale.run("run", declared)["status"])
        lots = dict(base, tax_lots=[{"account_last4": "M7Q5", "open_lot_id": "L7", "open_date": "2026-06-02",
                                     "quantity": "20", "cost_per_share": "340.00"}])
        out = wash_sale.run("run", lots)
        self.assertEqual("possible", out["status"])
        self.assertIn("permanently", out["status_line"])
        out = wash_sale.run("run", base)
        self.assertEqual("clear", out["status"])
        self.assertTrue(any("PERMANENTLY" in n for n in out["notes"]))


class CoverageNeverClear(unittest.TestCase):
    """Build spec B.7.9: the status is never clear unless every account in get_accounts was read completely."""

    def test_every_read_status_combination(self):
        statuses = ("complete", "partial", "failed", "not_in_scope")
        for combo in itertools.product(statuses, repeat=3):
            accounts = [dict(a, read_status=s) for a, s in zip(HOUSE, combo)]
            for buys in ([], [{"account_last4": "X4F1", "date": "2026-11-10", "shares": "1"}]):
                with self.subTest(combo=combo, buys=len(buys)):
                    out = wash_sale.run("run", sale(buys, accounts))
                    self.assertTrue(out["ok"], out)
                    status = out["status"]
                    if buys:
                        self.assertEqual("conflict", status)
                        continue
                    if any(s in ("partial", "failed") for s in combo):
                        self.assertEqual("unknown", status)
                    elif "not_in_scope" in combo:
                        self.assertEqual("clear_in_scope", status)
                    else:
                        self.assertEqual("clear", status)
                    if status == "clear":
                        self.assertTrue(all(s == "complete" for s in combo))

    def test_planned_buy_with_unclassified_rows_is_never_clear(self):
        out = wash_sale.run("run", {
            "mode": "planned_buy", "symbol": "AMD", "as_of": "2026-11-16", "accounts": HOUSE,
            "planned_buy": {"date": "2026-11-16", "account_last4": "X4F1", "shares": "1"},
            "realized_losses": [{"account_last4": "M7Q5", "date": "2026-11-09", "shares": "1", "realized_usd": "-5.00"}]})
        self.assertEqual("unknown", out["status"])
        self.assertTrue(out["status_line"].startswith("UNKNOWN: Dollars at stake:"))


class BehaviourTests(unittest.TestCase):
    def test_status_line_is_first_line_format(self):
        for fname, case in golden_cases():
            if case["op"] != "run":
                continue
            out = wash_sale.run("run", case["input"])
            if out.get("ok"):
                with self.subTest(case=case["name"]):
                    self.assertRegex(out["status_line"], r"^(CONFLICT|UNKNOWN|POSSIBLE|CLEAR|NO ACTION): Dollars at stake: ")

    def test_outputs_carry_only_last4(self):
        full = "5QR9" + "X4F1"  # built at runtime so the source has no account-like literal
        data = sale([{"account_last4": full, "date": "2026-11-10", "shares": "1"}],
                    [dict(HOUSE[0], last4=full)] + HOUSE[1:])
        out = wash_sale.run("run", data)
        self.assertTrue(out["ok"])
        self.assertNotIn(full, json.dumps(out))
        self.assertEqual("X4F1", out["conflicts"][0]["account_last4"])

    def test_money_is_cents_and_decimal_exact(self):
        data = sale([{"account_last4": "X4F1", "date": "2026-11-10", "shares": "0.3"}])
        data["sale"]["price_per_share"] = "99.995"
        data["sale"]["lots_sold"] = [{"lot_id": "A", "acquired": "2025-01-02", "shares": "0.1", "cost_per_share": "100.00"},
                                     {"lot_id": "B", "acquired": "2025-01-03", "shares": "0.2", "cost_per_share": "100.00"}]
        out = wash_sale.run("run", data)
        self.assertEqual("0.3", out["washed_shares"])
        self.assertEqual("0.00", out["disallowed_total_usd"])  # 0.3 x 0.005 = 0.0015 -> 0.00
        self.assertEqual("0", out["clean_shares"])

    def test_without_rh_time_dates_still_compute_and_trading_day_is_null(self):
        saved = wash_sale.rh_time
        wash_sale.rh_time = None
        try:
            out = wash_sale.run("run", sale())
            self.assertTrue(out["ok"])
            self.assertEqual("2026-12-17", out["do_not_buy_until"])
            self.assertIsNone(out["first_trading_day_after"])
            self.assertTrue(any("rh_time.py not found" in n for n in out["notes"]))
            ts = wash_sale.run("run", sale([{"account_last4": "X4F1", "timestamp": "2026-11-10T15:00:00Z", "shares": "1"}]))
            self.assertEqual("RH_TIME_MISSING", ts["errors"][0]["code"])
        finally:
            wash_sale.rh_time = saved

    def test_bad_inputs_are_errors_not_crashes(self):
        cases = [
            ("run", {"mode": "sell"}, "BAD_VALUE"),
            ("run", dict(sale(), as_of="11/16/2026"), "BAD_DATE"),
            ("run", dict(sale(), accounts=[{"last4": "X4F1", "type": "ira", "read_status": "complete"}]), "BAD_VALUE"),
            ("run", dict(sale(), accounts=[{"last4": "X4F1", "type": "taxable", "read_status": "done"}]), "BAD_VALUE"),
            ("run", {"mode": "sale", "symbol": "XYZ", "as_of": "2026-11-16", "accounts": HOUSE}, "MISSING_FIELD"),
            ("window", {"date": "2026-02-30"}, "BAD_DATE"),
            ("window", {"date": "2026-11-16", "gtc_lookback_days": "ninety"}, "BAD_VALUE"),
            ("classify_pnl", {"rows": "x"}, "MISSING_FIELD"),
            ("classify_pnl", {"rows": [], "tolerance_seconds": "soon"}, "BAD_VALUE"),
            ("nope", {}, "UNKNOWN_OP"),
        ]
        for op, data, code in cases:
            with self.subTest(op=op, code=code):
                out = wash_sale.run(op, data)
                self.assertFalse(out["ok"])
                self.assertEqual(code, out["errors"][0]["code"])


class ContractTests(unittest.TestCase):
    def cli(self, args, stdin=""):
        return subprocess.run([sys.executable, SCRIPT] + args, input=stdin, capture_output=True, text=True, timeout=60)

    def test_selftest_passes(self):
        proc = self.cli(["--selftest"])
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ok"])

    def test_schema_is_json_for_every_op(self):
        proc = self.cli(["--schema"])
        self.assertEqual(0, proc.returncode)
        doc = json.loads(proc.stdout)
        self.assertEqual({"window", "classify_pnl", "run"}, set(doc["ops"]))
        for op in doc["ops"].values():
            self.assertIn("input", op)
            self.assertIn("output", op)

    def test_exit_zero_with_json_on_bad_input(self):
        for stdin in ("not json", "[]", "{}"):
            proc = self.cli(["run"], stdin)
            self.assertEqual(0, proc.returncode)
            self.assertFalse(json.loads(proc.stdout)["ok"])
        proc = self.cli([])
        self.assertEqual(0, proc.returncode)
        self.assertEqual("MISSING_OP", json.loads(proc.stdout)["errors"][0]["code"])

    def test_cli_run(self):
        proc = self.cli(["window"], json.dumps({"date": "2026-12-31"}))
        self.assertEqual("2027-02-01", json.loads(proc.stdout)["first_trading_day_after"])

    def test_stdlib_only_no_network_and_py39_grammar(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src, feature_version=(3, 9))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                self.assertNotIn(name, FORBIDDEN_IMPORTS)
        for word in ("socket", "urllib", "http.client", "requests", "curl", "wget"):
            self.assertNotIn(word, src)
        self.assertNotIn("open(", src)  # no file writes


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

        seeds = [(op, data) for op, data, _ in wash_sale.EXAMPLES] + [(c["op"], c["input"]) for _, c in golden_cases()]
        for n in range(600):
            op, data = rng.choice(seeds)
            for _ in range(rng.randint(1, 3)):
                data = mutate(data)
            with self.subTest(n=n):
                out = wash_sale.run(op, data)
                self.assertIsInstance(out, dict)
                self.assertIn("ok", out)
                json.dumps(out)


if __name__ == "__main__":
    unittest.main()
