"""Tests for skills/robinhood-exit-guardian/scripts/protection_audit.py (audit statuses, gap codes, levels)."""

import ast
import copy
import json
import os
import subprocess
import sys
import unittest
from datetime import date, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "robinhood-exit-guardian", "scripts")
NAME = "protection_audit"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import protection_audit as pa  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}
# Build spec B.3 gap codes, verbatim.
SPEC_GAPS = [
    "UNCOVERED_SHARES",
    "EXITS_EXCEED_POSITION",
    "GFD_EXPIRES_TODAY",
    "FRACTIONAL_REMAINDER",
    "STOP_MARKET_GAP_RISK",
    "NO_EXTENDED_HOURS_COVERAGE",
    "ALERT_DISABLED",
    "ALERT_NOTIFIES_ONLY",
    "CRYPTO_STOP_DAY_ONLY",
    "CRYPTO_GTC_90D",
    "NOT_AGENT_TRADABLE",
    "EARNINGS_BEFORE_NEXT_SESSION",
    "STALE_QUOTE",
]
# Full fixture account numbers (build spec E.2); none may ever appear in output.
FULL_NUMBERS = ["5QR9X4F1", "8TK2M7Q5", "3HV6P0Z9", "779903418", "551208867", "660417225"]


def strict(expected, actual):
    """Dict subset; lists and scalars must match exactly (same length, same types)."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and strict(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(strict(e, a) for e, a in zip(expected, actual))
        )
    return expected == actual and type(expected) is type(actual)


def load(name):
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def by_key(out):
    return dict(("%s@%s" % (p["symbol"], p["account_last4"]), p) for p in out["positions"])


def all_gap_codes(out):
    return [g["code"] for p in out.get("positions", []) for g in p["gaps"]]


class AuditGolden(unittest.TestCase):
    def check_case(self, case):
        out = pa.run("audit", copy.deepcopy(case["input"]))
        exp = case["expect"]
        dump = json.dumps(out, indent=1, ensure_ascii=False)
        self.assertEqual(exp["ok"], out["ok"], dump)
        if not exp["ok"]:
            self.assertEqual(exp["error_code"], out["errors"][0]["code"], dump)
            return
        self.assertEqual([], pa.schema_errors(out, pa.SCHEMAS["audit"]["output"]))
        for field in ("first_line", "scope_line"):
            if field in exp:
                self.assertEqual(exp[field], out[field])
        if "summary" in exp:
            self.assertTrue(strict(exp["summary"], out["summary"]), json.dumps(out["summary"]))
        got = by_key(out)
        if "position_order" in exp:
            self.assertEqual(
                exp["position_order"], ["%s@%s" % (p["symbol"], p["account_last4"]) for p in out["positions"]]
            )
        for key, want in exp.get("positions", {}).items():
            self.assertIn(key, got, dump)
            have = got[key]
            want = dict(want)
            codes = want.pop("gaps", None)
            texts = want.pop("gap_text", {})
            if codes is not None:
                self.assertEqual(codes, [g["code"] for g in have["gaps"]], key)
            for code, text in texts.items():
                self.assertIn(text, [g["text"] for g in have["gaps"] if g["code"] == code], key)
            self.assertTrue(strict(want, have), "%s: %s" % (key, json.dumps(have, indent=1, ensure_ascii=False)))
        for row in exp.get("table_rows", []):
            self.assertIn(row, out["table"])
        for field in ("orphan_exits", "alert_cleanup"):
            if field in exp:
                self.assertEqual(len(exp[field]), len(out[field]), json.dumps(out[field]))
                for e, a in zip(exp[field], out[field]):
                    self.assertTrue(strict(e, a), json.dumps(a))
        for needle in exp.get("notes_contain", []):
            self.assertTrue(any(needle in n for n in out["notes"]), "%r not in %r" % (needle, out["notes"]))
        if exp.get("footer_exact"):
            self.assertEqual(pa.FOOTER, out["footer"])

    def test_golden_cases(self):
        cases = load("audit.json")
        self.assertGreaterEqual(len(cases), 20)
        for case in cases:
            with self.subTest(case=case["name"]):
                self.check_case(case)

    def test_every_gap_code_is_triggered_and_also_absent_somewhere(self):
        """Each code has at least one triggering golden case and at least one case where it does not fire."""
        outs = [pa.run("audit", copy.deepcopy(c["input"])) for c in load("audit.json") if c["expect"]["ok"]]
        seen = set(code for out in outs for code in all_gap_codes(out))
        self.assertEqual(set(pa.GAP_CODES), seen)
        for code in pa.GAP_CODES:
            self.assertTrue(any(code not in all_gap_codes(o) and o["positions"] for o in outs), code)

    def test_spec_gap_codes_are_all_implemented(self):
        self.assertEqual(SPEC_GAPS, list(pa.SPEC_GAP_CODES))
        self.assertTrue(set(SPEC_GAPS) <= set(pa.GAP_CODES))
        self.assertEqual(len(pa.GAP_CODES), len(set(pa.GAP_CODES)))


class AuditRules(unittest.TestCase):
    def base(self, **kw):
        d = {
            "now": "2026-11-17T01:05:00Z",
            "session_regular_open": False,
            "next_regular_open_et": "2026-11-17T09:30:00-05:00",
            "alerts": [],
            "accounts": [
                {"account_last4": "X4F1", "agentic": True, "read": dict((k, "complete") for k in pa.READ_KEYS)}
            ],
            "positions": [
                {
                    "account_last4": "X4F1",
                    "symbol": "KO",
                    "quantity": "10",
                    "price": "72.05",
                    "price_as_of": "2026-11-17T01:04:00Z",
                }
            ],
            "open_orders": [],
        }
        d.update(kw)
        return d

    def order(self, state, asset="equity", **kw):
        o = {
            "account_last4": "X4F1",
            "asset_class": asset,
            "symbol": "KO" if asset == "equity" else "ETH",
            "side": "sell",
            "type": "stop_market" if asset == "equity" else "stop_loss",
            "state": state,
            "quantity": "10" if asset == "equity" else "1",
            "stop_price": "60.00",
            "time_in_force": "gtc",
        }
        o.update(kw)
        return o

    def test_open_state_lists_follow_the_schema_fix(self):
        self.assertEqual(("new", "queued", "confirmed", "unconfirmed", "partially_filled"), pa.OPEN_STATES["equity"])
        self.assertEqual(("queued", "confirmed", "partially_filled"), pa.OPEN_STATES["crypto"])
        for state in pa.OPEN_STATES["equity"]:
            out = pa.run("audit", self.base(open_orders=[self.order(state)]))
            self.assertEqual("protected", out["positions"][0]["status"], state)
        for state in ("filled", "cancelled", "rejected", "failed", "voided", "pending_cancelled"):
            out = pa.run("audit", self.base(open_orders=[self.order(state)]))
            self.assertEqual("unprotected", out["positions"][0]["status"], state)
        eth = [
            {
                "account_last4": "X4F1",
                "asset_class": "crypto",
                "symbol": "ETH",
                "quantity": "1",
                "price": "3000.00",
                "price_as_of": "2026-11-17T01:04:00Z",
            }
        ]
        for state, want in (
            ("queued", "protected"),
            ("confirmed", "protected"),
            ("partially_filled", "protected"),
            ("canceled", "unprotected"),
            ("filled", "unprotected"),
            ("new", "unprotected"),
        ):
            out = pa.run("audit", self.base(positions=eth, open_orders=[self.order(state, "crypto")]))
            self.assertEqual(want, out["positions"][0]["status"], state)

    def test_limit_and_market_sells_are_not_protection(self):
        lim = self.order("confirmed", type="limit", stop_price=None, price="90.00")
        out = pa.run("audit", self.base(open_orders=[lim]))
        p = out["positions"][0]
        self.assertEqual("unprotected", p["status"])
        self.assertEqual("a limit sell is not downside protection", p["not_counted"][0]["why"])

    def test_buy_side_orders_are_ignored(self):
        out = pa.run("audit", self.base(open_orders=[self.order("confirmed", side="buy")]))
        self.assertEqual("unprotected", out["positions"][0]["status"])

    def test_footer_is_the_honest_framing(self):
        out = pa.run("audit", self.base())
        self.assertIn("Alerts notify your phone; they do not sell.", out["footer"])
        self.assertIn("regular hours only", out["footer"])
        self.assertIn("stop-market", out["footer"])
        self.assertIn("Nothing was placed", out["footer"])
        # Regular hours bind stock stops and OCOs only; the footer sits under crypto rows too.
        self.assertIn("Stock stops and OCOs act in regular hours only", out["footer"])
        self.assertIn("crypto stop orders can trigger at any hour", out["footer"])

    def test_crypto_stop_time_in_force_missing_is_unknown_not_day_only(self):
        eth = [{"account_last4": "X4F1", "asset_class": "crypto", "symbol": "ETH", "quantity": "1",
                "price": "3000.00", "price_as_of": "2026-11-17T01:04:00Z"}]
        for tif, want, absent in ((None, "TIF_UNKNOWN", "CRYPTO_STOP_DAY_ONLY"),
                                  ("gfd", "CRYPTO_STOP_DAY_ONLY", "TIF_UNKNOWN"),
                                  ("gtc", "CRYPTO_GTC_90D", "CRYPTO_STOP_DAY_ONLY")):
            out = pa.run("audit", self.base(positions=eth, open_orders=[self.order("confirmed", "crypto",
                                                                                   time_in_force=tif)]))
            codes = [g["code"] for g in out["positions"][0]["gaps"]]
            self.assertIn(want, codes, tif)
            self.assertNotIn(absent, codes, tif)
            if tif is None:
                self.assertNotIn("GFD", out["positions"][0]["covering"][0])

    def test_crypto_reads_default_to_not_read_on_every_account(self):
        """get_crypto_positions takes any account's rhs number, so an omitted crypto read is never 'none held'."""
        accounts = [
            {"account_last4": "X4F1", "agentic": True, "read": dict((k, "complete") for k in pa.READ_KEYS)},
            {"account_last4": "M7Q5", "agentic": False, "label": "Individual",
             "read": {"positions": "complete", "equity_orders": "complete", "advanced_orders": "complete"}},
        ]
        out = pa.run("audit", self.base(accounts=accounts, open_orders=[self.order("confirmed")]))
        m7 = [a for a in out["accounts"] if a["account_last4"] == "M7Q5"][0]
        self.assertEqual("not_read", m7["read"]["crypto_positions"])
        self.assertTrue(out["first_line"].startswith("UNKNOWN:"), out["first_line"])
        self.assertIn("Individual ••••M7Q5: crypto orders not_read, crypto positions not_read", out["scope_line"])
        for status in ("failed", "partial"):
            accounts[1]["read"]["crypto_positions"] = status
            out = pa.run("audit", self.base(accounts=accounts, open_orders=[self.order("confirmed")]))
            self.assertTrue(out["first_line"].startswith("UNKNOWN:"), (status, out["first_line"]))
            self.assertIn("crypto positions %s" % status, out["scope_line"])
        accounts[1]["read"].update({"crypto_positions": "not_applicable", "crypto_orders": "not_applicable"})
        out = pa.run("audit", self.base(accounts=accounts, open_orders=[self.order("confirmed")]))
        self.assertTrue(out["first_line"].startswith("CLEAR:"), out["first_line"])
        self.assertNotIn("Incomplete", out["scope_line"])

    def test_first_line_starts_with_a_status(self):
        for case in load("audit.json"):
            if not case["expect"]["ok"]:
                continue
            out = pa.run("audit", copy.deepcopy(case["input"]))
            status = out["first_line"].split(":", 1)[0]
            self.assertIn(status, ("ACTION NEEDED", "UNKNOWN", "CLEAR", "NO ACTION"), case["name"])
            self.assertTrue(out["first_line"].startswith(status + ": Dollars at stake: "), case["name"])

    def test_no_full_account_number_in_any_output(self):
        for case in load("audit.json") + load("levels.json"):
            op = "levels" if "prices" in case["input"] or "rules" in case["input"] else "audit"
            text = json.dumps(pa.run(op, copy.deepcopy(case["input"])), ensure_ascii=False)
            for number in FULL_NUMBERS:
                if number in json.dumps(case["input"]):
                    continue  # the refusal case: the number is in the input, and the output must not echo it
                self.assertNotIn(number, text)
        refusal = pa.run(
            "audit",
            self.base(positions=[{"account_last4": "5QR9X4F1", "symbol": "KO", "quantity": "1", "agentic": True}]),
        )
        self.assertEqual("FULL_ACCOUNT_NUMBER", refusal["errors"][0]["code"])
        self.assertNotIn("5QR9X4F1", json.dumps(refusal))

    def test_deterministic(self):
        case = load("audit.json")[1]["input"]
        self.assertEqual(
            json.dumps(pa.run("audit", copy.deepcopy(case)), sort_keys=True),
            json.dumps(pa.run("audit", copy.deepcopy(case)), sort_keys=True),
        )

    def test_the_input_is_not_mutated(self):
        case = load("audit.json")[1]["input"]
        before = json.dumps(case, sort_keys=True)
        pa.run("audit", case)
        self.assertEqual(before, json.dumps(case, sort_keys=True))


class LevelsGolden(unittest.TestCase):
    def test_golden_cases(self):
        cases = load("levels.json")
        self.assertGreaterEqual(len(cases), 15)
        for case in cases:
            with self.subTest(case=case["name"]):
                out = pa.run("levels", copy.deepcopy(case["input"]))
                exp = dict(case["expect"])
                if not exp["ok"]:
                    self.assertFalse(out["ok"])
                    self.assertEqual(exp["error_code"], out["errors"][0]["code"])
                    continue
                self.assertEqual([], pa.schema_errors(out, pa.SCHEMAS["levels"]["output"]))
                levels = exp.pop("levels")
                self.assertTrue(strict(exp, out), json.dumps(out, indent=1))
                for sym, want in levels.items():
                    have = out["levels"][sym]
                    self.assertEqual([], pa.schema_errors(have, pa._LEVEL))
                    for k, v in want.items():
                        if k in ("errors", "warnings"):
                            self.assertEqual(len(v), len(have.get(k, [])), json.dumps(have))
                            for e, a in zip(v, have[k]):
                                self.assertTrue(strict(e, a), json.dumps(a))
                        else:
                            self.assertTrue(strict(v, have.get(k)), "%s.%s: %r" % (sym, k, have.get(k)))


class LevelsNeverInvent(unittest.TestCase):
    PRICES = {"AMD": "161.40", "NVDA": "228.10"}

    def test_unset_or_ask_never_yields_a_number(self):
        for stop_rule in ("UNSET", "ask", None):
            for target_rule in ("UNSET", "ask", "r:2", "pct:15", "none", None):
                rules = {"stop_rule": stop_rule}
                if target_rule is not None:
                    rules["target_rule"] = target_rule
                out = pa.run("levels", {"rules": rules, "prices": self.PRICES, "atr": {"AMD": "6.20"}})
                self.assertTrue(out["ok"], out)
                self.assertIn("stop", out["ask"])
                for sym, lv in out["levels"].items():
                    self.assertIsNone(lv["stop"], (stop_rule, target_rule, sym))
                    if target_rule in ("UNSET", "ask", "r:2", "none", None):
                        self.assertIsNone(lv["target"], (stop_rule, target_rule, sym))

    def test_unrequested_target_is_never_computed(self):
        out = pa.run("levels", {"rules": {"stop_rule": "pct:8"}, "prices": self.PRICES})
        for lv in out["levels"].values():
            self.assertIsNone(lv["target"])
            self.assertNotIn("ask", lv)
        self.assertNotIn("ask", out)

    def test_atr_is_used_only_by_an_atr_rule(self):
        with_atr = pa.run(
            "levels", {"rules": {"stop_rule": "pct:8"}, "prices": self.PRICES, "atr": {"AMD": "6.20", "NVDA": "9.80"}}
        )
        without = pa.run("levels", {"rules": {"stop_rule": "pct:8"}, "prices": self.PRICES})
        self.assertEqual(with_atr["levels"], without["levels"])

    def test_every_computed_level_carries_user_provenance(self):
        for case in load("levels.json"):
            out = pa.run("levels", copy.deepcopy(case["input"]))
            if not out["ok"]:
                continue
            for lv in out["levels"].values():
                for field in ("stop", "target"):
                    if lv[field] is not None:
                        self.assertIn(lv["provenance_by_field"][field], ("user", "user_config"))


class TimeHelpers(unittest.TestCase):
    def test_embedded_dst_matches_zoneinfo(self):
        if pa._ZONE is None:
            self.skipTest("zoneinfo data unavailable")
        cases = [
            (date(2026, 3, 8), time(8, 0)),
            (date(2026, 3, 7), time(16, 0)),
            (date(2026, 11, 1), time(8, 0)),
            (date(2026, 10, 31), time(16, 0)),
            (date(2027, 3, 14), time(9, 30)),
            (date(2026, 11, 16), time(16, 0)),
        ]
        want = [pa.et_to_utc(d, t) for d, t in cases]
        saved = pa._ZONE
        try:
            pa._ZONE = None
            got = [pa.et_to_utc(d, t) for d, t in cases]
            ets = [pa.to_et(w).strftime("%Y-%m-%d %H:%M") for w in want]
        finally:
            pa._ZONE = saved
        self.assertEqual(want, got)
        self.assertEqual(["%s %s" % (d.isoformat(), t.strftime("%H:%M")) for d, t in cases], ets)

    def test_nanosecond_timestamps_parse(self):
        dt = pa.parse_ts("2026-11-17T01:04:00.123456789Z", "t")
        self.assertEqual(123456, dt.microsecond)


class Contract(unittest.TestCase):
    def cli(self, *args, stdin=""):
        return subprocess.run(
            [sys.executable, SCRIPT] + list(args), input=stdin, capture_output=True, text=True, timeout=60
        )

    def test_selftest_passes(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)
        self.assertTrue(json.loads(res.stdout)["ok"])

    def test_schema_is_json(self):
        res = self.cli("--schema")
        self.assertEqual(0, res.returncode)
        doc = json.loads(res.stdout)
        self.assertEqual(["audit", "levels"], sorted(doc["ops"]))
        self.assertEqual(list(pa.GAP_CODES), doc["gap_codes"])

    def test_cli_runs_an_op(self):
        case = load("audit.json")[0]
        res = self.cli("audit", stdin=json.dumps(case["input"]))
        self.assertEqual(0, res.returncode)
        self.assertEqual(case["expect"]["first_line"], json.loads(res.stdout)["first_line"])

    def test_bad_json_and_unknown_op_still_exit_zero(self):
        res = self.cli("audit", stdin="{not json")
        self.assertEqual(0, res.returncode)
        self.assertEqual("BAD_JSON", json.loads(res.stdout)["errors"][0]["code"])
        res = self.cli("plan", stdin="{}")
        self.assertEqual("UNKNOWN_OP", json.loads(res.stdout)["errors"][0]["code"])
        res = self.cli()
        self.assertEqual("MISSING_OP", json.loads(res.stdout)["errors"][0]["code"])

    def test_no_network_imports_and_py39_syntax(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src, feature_version=(3, 9))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        self.assertFalse(mods & FORBIDDEN_IMPORTS, mods)
        self.assertNotIn("open(", src.replace("json.loads", ""))  # no file writes (or reads)


if __name__ == "__main__":
    unittest.main()
