"""Tests for skills/robinhood-agent-report-card/scripts/reconcile.py (broker orders vs the local audit log)."""

import ast
import copy
import json
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "robinhood-agent-report-card", "scripts")
NAME = "reconcile"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import audit_verify  # noqa: E402
import reconcile  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}
PERFORMANCE_WORDS = re.compile(r"\b(beat|beats|beating|outperform\w*|underperform\w*|alpha|grade)\b", re.I)
CATEGORIES = ("matched", "broker_only", "unattributed", "source_unknown", "user_orders_in_agentic")


def strict(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and strict(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            strict(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def load(name):
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as fh:
        return json.load(fh)


def fixture_week_input():
    doc = load("fixture_week.json")
    audit = audit_verify.run("run", {"dir": os.path.join(ROOT, doc["audit_fixture"]), "window": doc["window"]})
    inp = {k: copy.deepcopy(doc[k]) for k in ("window", "agentic_account_last4", "accounts", "broker_orders", "reads")}
    inp["audit"] = audit
    return doc, inp


class GoldenTests(unittest.TestCase):
    def test_synthetic_cases(self):
        doc = load("cases.json")
        self.assertGreaterEqual(len(doc["cases"]), 20)
        for case in doc["cases"]:
            with self.subTest(case=case["name"]):
                out = reconcile.run("run", copy.deepcopy(case["input"]))
                self.assertEqual(reconcile.schema_errors(out, reconcile.SCHEMAS["run"]["output"]), [])
                self.assertTrue(strict(case["expected"], out), json.dumps(out, indent=1, ensure_ascii=False)[:3000])

    def test_fixture_week(self):
        doc, inp = fixture_week_input()
        out = reconcile.run("run", inp)
        self.assertEqual(reconcile.schema_errors(out, reconcile.SCHEMAS["run"]["output"]), [])
        self.assertTrue(strict(doc["expected"], out), json.dumps(out, indent=1, ensure_ascii=False)[:4000])

    def test_fixture_week_hand_checks(self):
        _doc, inp = fixture_week_input()
        out = reconcile.run("run", inp)
        self.assertEqual(out["status"], "ACTION NEEDED")
        self.assertTrue(out["status_line"].startswith(
            "ACTION NEEDED: Dollars at stake: 1 order ($412.00) in Agentic \u2022\u2022\u2022\u2022X4F1 has no local "
            "audit entry (placed by another agent, app or machine) \u00b7 1 order ($285.60) placed without a matching "
            "review in the log \u00b7 1 order ($140.00)"))
        self.assertTrue(out["status_line"].endswith("1 possible prompt-injection attempt right after get_equity_news"))
        by_id = {o["order_id"]: o for o in inp["broker_orders"]}
        self.assertEqual([(by_id[o["order_id"]]["symbol"], o["notional_usd"]) for o in out["broker_only"]],
                         [("PLTR", "412.00")])
        self.assertEqual([by_id[o["order_id"]]["quantity"] for o in out["placed_without_review"]], ["4"])
        self.assertEqual([(o["placed_agent"], o["notional_usd"]) for o in out["user_orders_in_agentic"]],
                         [("user", "140.00")])
        self.assertEqual([o["asset"] for o in out["source_unknown"]], ["crypto"])
        self.assertTrue(all(o["reason"].startswith("source not distinguishable") for o in out["source_unknown"]))
        self.assertEqual({m["match_method"] for m in out["matched"]}, {"order_id"})
        self.assertEqual(len(out["matched"]), 8)
        self.assertEqual([v["status"] for v in out["read_only_verification"]], ["verified_none", "verified_none"])


class BehaviourTests(unittest.TestCase):
    def test_every_order_in_the_window_lands_in_exactly_one_category(self):
        _doc, inp = fixture_week_input()
        out = reconcile.run("run", inp)
        seen = []
        for cat in CATEGORIES:
            seen.extend(o["order_id"] for o in out[cat])
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), out["counts"]["agentic_orders"])

    def test_status_precedence(self):
        _doc, inp = fixture_week_input()
        # an incomplete read does not hide ACTION NEEDED
        inp["reads"][0]["status"] = "partial"
        out = reconcile.run("run", inp)
        self.assertEqual(out["status"], "ACTION NEEDED")
        self.assertTrue(any(n.startswith("also incomplete") for n in out["notes"]))

    def test_unknown_is_never_clear(self):
        base = {"window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
                "broker_orders": [], "reads": []}
        out = reconcile.run("run", base)
        self.assertEqual(out["status"], "UNKNOWN")
        self.assertEqual(len(out["missing_reads"]), 6)

    def test_masking_only_last4_in_output(self):
        _doc, inp = fixture_week_input()
        for o in inp["broker_orders"]:
            o["account_last4"] = "ACCT-NUMBER-" + o["account_last4"]
        out = reconcile.run("run", inp)
        self.assertNotIn("ACCT-NUMBER", json.dumps(out))
        self.assertEqual(out["counts"]["broker_only"], 1)

    def test_no_performance_wording(self):
        _doc, inp = fixture_week_input()
        self.assertIsNone(PERFORMANCE_WORDS.search(json.dumps(reconcile.run("run", inp))))

    def test_money_is_decimal_exact(self):
        out = reconcile.run("run", {
            "window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
            "reads": [], "broker_orders": [{"account_last4": "X4F1", "asset": "equity", "order_id": "o1",
                                            "symbol": "PLTR", "side": "buy", "quantity": "0.1", "state": "filled",
                                            "created_at": "2026-11-10T16:00:00Z", "placed_agent": "user",
                                            "average_price": "0.2", "cumulative_quantity": "0.3"}]})
        self.assertEqual(out["user_orders_in_agentic"][0]["notional_usd"], "0.06")


AGENTIC_READS = [
    {"account_last4": "X4F1", "tool": "get_equity_orders", "filter": "agentic", "status": "complete"},
    {"account_last4": "X4F1", "tool": "get_equity_orders", "filter": "all", "status": "complete"},
    {"account_last4": "X4F1", "tool": "get_option_orders", "filter": "agentic", "status": "complete"},
    {"account_last4": "X4F1", "tool": "get_option_orders", "filter": "all", "status": "complete"},
    {"account_last4": "X4F1", "tool": "get_crypto_orders", "status": "complete"},
    {"account_last4": "X4F1", "tool": "get_advanced_orders", "status": "complete"},
]
RO_READS = [
    {"account_last4": "M7Q5", "tool": "get_equity_orders", "filter": "agentic", "status": "complete"},
    {"account_last4": "M7Q5", "tool": "get_option_orders", "filter": "agentic", "status": "complete"},
]
EMPTY_LOG = {"ok": True, "available": True, "first_ts": "2026-11-01T00:00:00Z", "chain_ok": True, "breaks": [],
             "possible_injection": [], "places": []}


def base_input(orders, reads=None, audit=None):
    return {"window": {"start_date": "2026-11-09", "end_date": "2026-11-15"}, "agentic_account_last4": "X4F1",
            "accounts": [{"last4": "X4F1", "agentic": True}, {"last4": "M7Q5", "agentic": False}],
            "broker_orders": orders, "reads": copy.deepcopy(AGENTIC_READS + RO_READS if reads is None else reads),
            "audit": copy.deepcopy(EMPTY_LOG if audit is None else audit)}


def eq(oid, placed_agent, **kw):
    row = {"account_last4": "X4F1", "asset": "equity", "order_id": oid, "symbol": "KO", "side": "buy",
           "quantity": "1", "state": "filled", "created_at": "2026-11-10T15:00:00Z", "average_price": "70.00",
           "cumulative_quantity": "1"}
    if placed_agent is not None:
        row["placed_agent"] = placed_agent
    row.update(kw)
    return row


def oco_with_legs(leg_ids=True, placed_agent_legs="agentic"):
    oco = {"account_last4": "X4F1", "asset": "oco", "order_id": "a1", "symbol": "PLTR", "side": "sell",
           "quantity": "10", "state": "active", "created_at": "2026-11-12T15:00:00Z", "price": "34.00",
           "stop_price": "26.00"}
    if leg_ids:
        oco["leg_order_ids"] = ["l1", "l2"]
    legs = [{"account_last4": "X4F1", "asset": "equity", "order_id": "l1", "symbol": "PLTR", "side": "sell",
             "quantity": "10", "state": "confirmed", "created_at": "2026-11-12T15:00:00Z", "type": "limit",
             "price": "34.00", "placed_agent": placed_agent_legs},
            {"account_last4": "X4F1", "asset": "equity", "order_id": "l2", "symbol": "PLTR", "side": "sell",
             "quantity": "10", "state": "confirmed", "created_at": "2026-11-12T15:00:00Z", "type": "market",
             "stop_price": "26.00", "placed_agent": placed_agent_legs}]
    return [oco] + legs


class NonAgentSourceTests(unittest.TestCase):
    """placed_agent is an open set (user, agentic, recurring, drip, ...): every non-agent source is a finding."""

    def test_recurring_drip_and_user_option_orders_are_action_needed(self):
        orders = [eq("r1", "recurring"), eq("d1", "drip", created_at="2026-11-11T15:00:00Z"),
                  {"account_last4": "X4F1", "asset": "option", "order_id": "o1", "symbol": "KO", "side": "sell",
                   "quantity": "1", "state": "filled", "created_at": "2026-11-12T15:00:00Z",
                   "placed_agent": "not_agentic", "price": "1.10"}]
        out = reconcile.run("run", base_input(orders))
        self.assertEqual(out["status"], "ACTION NEEDED")
        self.assertEqual([(o["order_id"], o["placed_agent"]) for o in out["user_orders_in_agentic"]],
                         [("r1", "recurring"), ("d1", "drip"), ("o1", "not_agentic")])
        self.assertEqual(out["source_unknown"], [])
        self.assertIn("3 orders ($250.00) in Agentic \u2022\u2022\u2022\u2022X4F1 that Robinhood marks as not placed by "
                      "an agent", out["status_line"])

    def test_all_sources_reads_are_required_and_matched_exactly(self):
        # the old read set (agentic + user filters, options agentic only) is not enough to call it CLEAR
        old = [r for r in AGENTIC_READS if r.get("filter") != "all"] + [
            {"account_last4": "X4F1", "tool": "get_equity_orders", "filter": "user", "status": "complete"}]
        out = reconcile.run("run", base_input([], reads=old + RO_READS))
        self.assertEqual(out["status"], "UNKNOWN")
        self.assertEqual(out["missing_reads"], ["get_equity_orders (all sources)", "get_option_orders (all sources)"])
        # an agentic-filtered or filterless record cannot stand in for the all-sources read
        stand_in = [r for r in AGENTIC_READS if r.get("filter") != "all"] + [
            {"account_last4": "X4F1", "tool": "get_equity_orders", "status": "complete"},
            {"account_last4": "X4F1", "tool": "get_option_orders", "filter": "agentic", "status": "complete"}]
        out = reconcile.run("run", base_input([], reads=stand_in + RO_READS))
        self.assertEqual(out["status"], "UNKNOWN")
        out = reconcile.run("run", base_input([]))
        self.assertEqual(out["status"], "CLEAR")

    def test_same_order_from_both_reads_counts_once_as_the_agents(self):
        out = reconcile.run("run", base_input([eq("e1", "not_agentic"), eq("e1", "agentic")]))
        self.assertEqual(out["counts"]["agentic_orders"], 1)
        self.assertEqual(out["counts"]["user_orders_in_agentic"], 0)
        self.assertEqual([o["order_id"] for o in out["broker_only"]], ["e1"])

    def test_untagged_equity_row_is_never_clear(self):
        out = reconcile.run("run", base_input([eq("x1", None)]))
        self.assertEqual(out["status"], "UNKNOWN")
        self.assertEqual(out["source_unknown"][0]["reason"], reconcile.UNTAGGED_REASON)


class OcoLegTests(unittest.TestCase):
    """get_advanced_orders hydrates each OCO's legs, and get_equity_orders lists them too: one OCO is one order."""

    def test_oco_placed_elsewhere_counts_once_with_one_amount(self):
        out = reconcile.run("run", base_input(oco_with_legs()))
        self.assertEqual(out["counts"]["agentic_orders"], 1)
        self.assertEqual(out["counts"]["oco_legs_folded"], 2)
        self.assertEqual([(o["order_id"], o["placed_agent"], o["notional_usd"], o["legs_folded"])
                          for o in out["broker_only"]], [("a1", "agentic", "340.00", ["l1", "l2"])])
        self.assertTrue(out["status_line"].startswith("ACTION NEEDED: Dollars at stake: 1 order ($340.00)"))

    def test_oco_sent_by_this_kit_matches_and_its_legs_are_never_broker_only(self):
        audit = dict(EMPTY_LOG, places=[{"ts": "2026-11-12T15:00:01Z", "tool": "place_advanced_order",
                                         "order_id": "a1", "ok": True, "symbol": "PLTR", "side": "sell",
                                         "quantity": "10", "matched_review": True, "review_age_s": 30}])
        out = reconcile.run("run", base_input(oco_with_legs(), audit=audit))
        self.assertEqual([m["order_id"] for m in out["matched"]], ["a1"])
        self.assertEqual(out["broker_only"], [])
        self.assertEqual(out["status"], "CLEAR")

    def test_legs_fold_without_leg_ids_by_symbol_quantity_and_price(self):
        out = reconcile.run("run", base_input(oco_with_legs(leg_ids=False)))
        self.assertEqual(out["counts"]["agentic_orders"], 1)
        self.assertEqual(out["broker_only"][0]["legs_folded"], ["l1", "l2"])
        # an OCO row mapped without its prices still folds legs created with it (one stop leg, one limit leg)
        orders = oco_with_legs(leg_ids=False)
        del orders[0]["price"], orders[0]["stop_price"]
        out = reconcile.run("run", base_input(orders))
        self.assertEqual((out["counts"]["agentic_orders"], out["broker_only"][0]["notional_usd"]), (1, "340.00"))
        # a standalone limit sell a day later at the same price is not a leg
        orders = oco_with_legs(leg_ids=False)
        orders[1]["created_at"] = "2026-11-13T15:00:00Z"
        out = reconcile.run("run", base_input(orders))
        self.assertEqual(out["counts"]["agentic_orders"], 2)

    def test_oco_with_untagged_legs_stays_source_unknown(self):
        orders = oco_with_legs(placed_agent_legs=None)
        for leg in orders[1:]:
            del leg["placed_agent"]
        out = reconcile.run("run", base_input(orders))
        self.assertEqual([(o["order_id"], o["reason"]) for o in out["source_unknown"]],
                         [("a1", reconcile.SOURCE_UNKNOWN_REASON)])

    def test_advanced_orders_not_readable_is_said(self):
        reads = [dict(r, status="not_enabled") if r["tool"] == "get_advanced_orders" else r for r in AGENTIC_READS]
        out = reconcile.run("run", base_input([eq("e1", "agentic")], reads=reads + RO_READS))
        self.assertTrue(any(n.startswith("OCO legs can't be told apart") for n in out["notes"]), out["notes"])


class ReadScopeTests(unittest.TestCase):
    """A read-only account no one was asked about is not excluded: it is unverified, so never CLEAR."""

    def test_no_reads_for_a_listed_read_only_account_is_unknown(self):
        out = reconcile.run("run", base_input([], reads=AGENTIC_READS))
        self.assertEqual(out["status"], "UNKNOWN")
        self.assertEqual(out["read_only_verification"], [{"account_last4": "M7Q5", "status": "not_checked",
                                                          "checked": []}])
        self.assertIn("read-only account(s) not verified: \u2022\u2022\u2022\u2022M7Q5", out["status_line"])

    def test_declined_or_agentic_only_is_noted_not_unknown(self):
        reads = AGENTIC_READS + [{"account_last4": "M7Q5", "tool": "get_equity_orders", "status": "not_in_scope"}]
        out = reconcile.run("run", base_input([], reads=reads))
        self.assertEqual(out["status"], "CLEAR")
        self.assertIn("excluded by you (not checked): \u2022\u2022\u2022\u2022M7Q5", out["notes"])


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
