"""Tests for skills/robinhood-agent-report-card/scripts/scorecard.py (counts, realized P&L, slippage, flags)."""

import ast
import copy
import json
import os
import re
import subprocess
import sys
import unittest
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "robinhood-agent-report-card", "scripts")
NAME = "scorecard"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import audit_verify  # noqa: E402
import reconcile  # noqa: E402
import scorecard  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}
PERFORMANCE_WORDS = re.compile(r"\b(beat|beats|beating|outperform\w*|underperform\w*|alpha|grade|best|worst)\b", re.I)


def strict(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and strict(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            strict(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return json.load(fh)


def fixture_week_input(with_bars):
    g = load("tests/golden/scorecard/fixture_week.json")
    week = load(g["orders_from"])
    audit = audit_verify.run("run", {"dir": os.path.join(ROOT, g["audit_fixture"]), "window": week["window"]})
    rin = {k: copy.deepcopy(week[k]) for k in ("window", "agentic_account_last4", "accounts", "broker_orders", "reads")}
    rin["audit"] = audit
    rc = reconcile.run("run", rin)
    inp = {"window": week["window"], "agentic_account_last4": week["agentic_account_last4"],
           "orders": copy.deepcopy(week["broker_orders"]), "matched": rc["matched"],
           "realized_pnl": copy.deepcopy(g["realized_pnl"]), "realized_trades": copy.deepcopy(g["realized_trades"]),
           "trade_history_span": g["trade_history_span"], "reviews": audit["quotes_at_review"],
           "portfolio_value_usd": g["portfolio_value_usd"]}
    if with_bars:
        inp["spy_bars"] = copy.deepcopy(g["spy_bars"])
    return g, inp


class GoldenTests(unittest.TestCase):
    def test_synthetic_cases(self):
        doc = load("tests/golden/scorecard/cases.json")
        self.assertGreaterEqual(len(doc["cases"]), 20)
        for case in doc["cases"]:
            with self.subTest(case=case["name"]):
                out = scorecard.run("run", copy.deepcopy(case["input"]))
                self.assertEqual(scorecard.schema_errors(out, scorecard.SCHEMAS["run"]["output"]), [])
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1)[:3000])

    def test_fixture_week_without_and_with_spy_bars(self):
        for with_bars, key in ((False, "expected_without_bars"), (True, "expected_with_bars")):
            with self.subTest(with_bars=with_bars):
                g, inp = fixture_week_input(with_bars)
                out = scorecard.run("run", inp)
                self.assertEqual(scorecard.schema_errors(out, scorecard.SCHEMAS["run"]["output"]), [])
                self.assertTrue(strict(g[key], out), json.dumps(out, indent=1)[:4000])

    def test_fixture_week_hand_checks(self):
        _g, inp = fixture_week_input(True)
        out = scorecard.run("run", inp)
        self.assertEqual((out["counts"]["orders"], out["counts"]["filled"], out["counts"]["rejected"],
                          out["counts"]["cancelled"]), (9, 7, 1, 1))
        self.assertEqual(out["counts_source_unknown"], {"orders": 1, "by_asset": {"crypto": 1}})
        self.assertEqual(out["realized_usd"], "237.23")
        self.assertEqual(out["win_rate"], {"value": 100.0, "n": 4, "value_raw": "100"})
        self.assertEqual((out["avg_win_usd"], out["avg_loss_usd"], out["profit_factor"]), ("31.88", None, None))
        self.assertEqual([u["symbol"] for u in out["per_trade"]["unclassified"]], ["ETH"])
        slip = out["slippage_bps_vs_review"]
        self.assertEqual((slip["avg"], slip["n"]), (4.4, 5))
        by_symbol_side = {(r["symbol"], r["side"], r["fill"]): Decimal(r["bps_raw"]) for r in slip["rows"]}
        self.assertEqual(by_symbol_side[("PLTR", "buy", "29.20")],
                         (Decimal("29.20") - Decimal("29.18")) / Decimal("29.18") * 10000)
        self.assertEqual(by_symbol_side[("AMD", "sell", "163.00")],
                         (Decimal("163.05") - Decimal("163.00")) / Decimal("163.05") * 10000)
        spy = out["trade_matched_spy"]
        self.assertEqual((spy["agent_pnl_usd"], spy["spy_pnl_usd"], spy["n"]), ("13.70", "2.20", 2))
        self.assertEqual([p["symbol"] for p in spy["pairs"]], ["PLTR", "KO"])
        flags = {f["flag"]: f for f in out["flags"]}
        self.assertEqual((flags["averaging_down"]["count"], flags["trade_within_24h_of_loss"]["count"]), (0, 0))
        self.assertEqual((flags["turnover_pct"]["value"], flags["turnover_pct"]["traded_usd"]), (15.2, "2788.70"))


class SpyRules(unittest.TestCase):
    def test_spy_only_with_both_fill_timestamps_and_always_with_n(self):
        _g, inp = fixture_week_input(True)
        for o in inp["orders"]:
            o.pop("last_transaction_at", None)
        out = scorecard.run("run", inp)
        self.assertIsNone(out["trade_matched_spy"])
        self.assertIsNone(out["spy_request"])
        _g, inp = fixture_week_input(True)
        out = scorecard.run("run", inp)
        spy = out["trade_matched_spy"]
        self.assertEqual(spy["n"], len(spy["pairs"]))
        self.assertEqual(spy["caveat"], "small sample; not a performance claim")
        for pair in spy["pairs"]:
            self.assertTrue(pair["entry_filled_at"] and pair["exit_filled_at"])
            self.assertLess(pair["entry_filled_at"], pair["exit_filled_at"])

    def test_spy_math_matches_same_dollars_same_period(self):
        _g, inp = fixture_week_input(True)
        out = scorecard.run("run", inp)
        for p in out["trade_matched_spy"]["pairs"]:
            entry = Decimal(p["entry_usd"])
            spy = entry * (Decimal(p["spy_exit"]) / Decimal(p["spy_entry"]) - 1)
            self.assertEqual(p["spy_pnl_usd"], str(spy.quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")))

    def test_no_performance_wording_anywhere_in_the_output(self):
        for with_bars in (False, True):
            _g, inp = fixture_week_input(with_bars)
            text = json.dumps(scorecard.run("run", inp))
            self.assertIsNone(PERFORMANCE_WORDS.search(text), PERFORMANCE_WORDS.search(text))


def agent_order(oid, side, qty, avg, created, filled, state="filled", asset="equity", placed_agent="agentic", **kw):
    row = {"account_last4": "X4F1", "asset": asset, "order_id": oid, "symbol": "PLTR", "side": side,
           "quantity": qty, "state": state, "created_at": created, "placed_agent": placed_agent,
           "average_price": avg, "cumulative_quantity": qty if state == "filled" else "0",
           "last_transaction_at": filled}
    row.update(kw)
    return row


def week(orders, **extra):
    inp = {"window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
           "orders": orders, "portfolio_value_usd": "3000.00"}
    inp.update(extra)
    return inp


class StateAndDedupeRules(unittest.TestCase):
    def test_pending_cancel_is_not_cancelled(self):
        out = scorecard.run("run", week([agent_order("op1", "sell", "1", None, "2026-11-12T15:00:00Z", None,
                                                     state="pending_cancelled", asset="option", price="1.10")]))
        self.assertEqual(out["counts"]["cancelled"], 0)
        self.assertEqual(out["counts"]["cancel_pending"], 1)
        self.assertNotIn("pending_cancelled", scorecard.CANCELLED_STATES)
        self.assertIn("may fill", out["definitions"]["counts"])

    def test_same_order_from_two_reads_counts_once(self):
        row = agent_order("e1", "buy", "10", "29.48", "2026-11-09T15:05:12Z", "2026-11-09T15:05:14Z")
        out = scorecard.run("run", week([row, dict(row, placed_agent="not_agentic")]))
        self.assertEqual(out["counts"]["orders"], 1)
        self.assertEqual(out["flags"][2]["traded_usd"], "294.80")


class OcoLegRules(unittest.TestCase):
    def legs(self, leg_ids=True):
        oco = {"account_last4": "X4F1", "asset": "oco", "order_id": "a1", "symbol": "PLTR", "side": "sell",
               "quantity": "10", "state": "filled", "created_at": "2026-11-09T16:00:00Z", "price": "31.00",
               "stop_price": "27.00"}
        if leg_ids:
            oco["leg_order_ids"] = ["l1", "l2"]
        return [
            agent_order("e1", "buy", "10", "29.48", "2026-11-09T15:05:12Z", "2026-11-09T15:05:14Z"),
            oco,
            agent_order("l1", "sell", "10", "31.00", "2026-11-09T16:00:00Z", "2026-11-10T15:00:03Z", type="limit",
                        price="31.00"),
            agent_order("l2", "sell", "10", None, "2026-11-09T16:00:00Z", "2026-11-10T15:00:03Z",
                        state="cancelled", type="market", stop_price="27.00"),
        ]

    def test_oco_and_its_legs_count_once_and_the_sibling_cancel_is_not_an_agent_cancel(self):
        for leg_ids in (True, False):
            with self.subTest(leg_ids=leg_ids):
                trades = [{"timestamp": "2026-11-10T15:00:03Z", "symbol": "PLTR", "side": "sell", "quantity": "10",
                           "price": "31.00", "realized_gain": "15.20"}]
                out = scorecard.run("run", week(self.legs(leg_ids), realized_trades=trades))
                self.assertEqual(out["counts"]["orders"], 2)
                self.assertEqual(out["counts"]["filled"], 2)
                self.assertEqual(out["counts"]["cancelled"], 0)
                # the filled take-profit leg is still the agent's sell: turnover once, the trade row attributed
                self.assertEqual(out["flags"][2]["traded_usd"], "604.80")
                self.assertEqual(out["per_trade"]["agent_rows"], 1)

    def test_oco_matched_by_reconcile_counts_as_the_agents(self):
        rows = self.legs()
        for r in rows[2:]:
            r["placed_agent"] = None
        out = scorecard.run("run", week(rows, matched=[{"order_id": "a1", "fingerprint": "sha256:oc"}]))
        self.assertEqual(out["counts"]["orders"], 2)
        self.assertEqual(out["counts_source_unknown"]["orders"], 0)


class ContractTests(unittest.TestCase):
    def cli(self, args, stdin=""):
        return subprocess.run([sys.executable, SCRIPT] + args, input=stdin, capture_output=True, text=True,
                              timeout=60)

    def test_selftest_and_schema(self):
        self.assertEqual(self.cli(["--selftest"]).returncode, 0)
        schema = json.loads(self.cli(["--schema"]).stdout)
        self.assertEqual(schema["script"], NAME)
        self.assertEqual(sorted(schema["ops"]["run"]), ["input", "output"])

    def test_cli_errors_are_json_with_exit_0(self):
        for args, stdin, code in (([], "", "MISSING_OP"), (["run"], "{bad", "BAD_JSON"), (["run"], "{}", "BAD_WINDOW")):
            proc = self.cli(args, stdin)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(json.loads(proc.stdout)["errors"][0]["code"], code)

    def test_no_network_imports_and_no_file_access(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            source = fh.read()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            self.assertFalse(set(names) & (FORBIDDEN_IMPORTS | {"os", "shutil"}), names)
        self.assertNotIn("open(", source)


if __name__ == "__main__":
    unittest.main()
