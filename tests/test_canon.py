"""Tests for shared/scripts/canon.py (K14 parity, pinned fingerprints, masking)."""

import ast
import json
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "shared", "scripts")
NAME = "canon"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import canon  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}


def load(name):
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as fh:
        return json.load(fh)["cases"]


class ParityTests(unittest.TestCase):
    """K14: review and place parameters differing only in chain_symbol, underlying_type,
    defaults, decimal format or leg order hash equal."""

    def test_parity_goldens(self):
        cases = load("parity.json")
        self.assertGreaterEqual(len(cases), 15)
        for case in cases:
            with self.subTest(case=case["name"]):
                out = canon.run("compare", {"a": case["a"], "b": case["b"]})
                self.assertTrue(out["ok"], out)
                self.assertIs(case["equal"], out["equal"], out)
                if "diff_fields" in case:
                    self.assertEqual(case["diff_fields"], out["diff_fields"])
                if case["equal"]:
                    self.assertEqual([], out["diff_fields"])

    def test_every_equal_pair_also_matches_through_run(self):
        for case in load("parity.json"):
            if not case["equal"]:
                continue
            a = canon.run("run", case["a"])
            b = canon.run("run", case["b"])
            self.assertEqual(a["fingerprint"], b["fingerprint"], case["name"])
            self.assertEqual(a["ticket_id"], b["ticket_id"])
            self.assertEqual(a["canonical"], b["canonical"])

    def test_pinned_fingerprints(self):
        for case in load("fingerprints.json"):
            with self.subTest(case=case["name"]):
                fp = canon.fingerprint(case["tool"], case["params"])
                self.assertEqual(case["family"], fp["family"])
                self.assertEqual(case["canonical_json_hand_written"], canon.canonical_json(fp["canonical"]))
                self.assertEqual(case["fingerprint"], fp["fingerprint"])
                self.assertEqual(case["ticket_id"], fp["ticket_id"])


class BehaviourTests(unittest.TestCase):
    def test_account_is_hashed_but_never_printed(self):
        params = {"account_number": "demo-X4F1", "symbol": "KO", "side": "buy", "type": "market", "quantity": "1"}
        out = canon.run("run", {"tool": "review_equity_order", "params": params})
        text = json.dumps(out)
        self.assertNotIn("demo-X4F1", text)
        self.assertEqual(canon.MASK + "X4F1", out["canonical"]["account_number"])
        other = canon.run("run", {"tool": "review_equity_order", "params": dict(params, account_number="demo-M7Q5")})
        self.assertNotEqual(out["fingerprint"], other["fingerprint"])

    def test_rhs_account_is_masked_too(self):
        out = canon.run("run", {"tool": "preview_crypto_order", "params": {"rhs_account_number": "demo-5555", "symbol": "BTC",
                                                                        "side": "buy", "type": "market", "quantity": "1"}})
        self.assertEqual(canon.MASK + "5555", out["canonical"]["rhs_account_number"])

    def test_fingerprint_format_and_ticket_id(self):
        out = canon.run("run", {"tool": "review_equity_order", "params": {"account_number": "a", "symbol": "KO", "side": "buy",
                                                                          "type": "market", "quantity": "1"}})
        self.assertRegex(out["fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(out["fingerprint"][7:13], out["ticket_id"])
        self.assertEqual("place_equity_order", out["place_tool"])
        self.assertEqual("review_equity_order", out["review_tool"])

    def test_canonical_json_is_sorted_and_compact(self):
        text = canon.canonical_json({"b": 1, "a": [1, {"d": 2, "c": 3}]})
        self.assertEqual('{"a":[1,{"c":3,"d":2}],"b":1}', text)

    def test_normalize_decimal(self):
        cases = {"10.00": "10", "0.50": "0.5", ".5": "0.5", "31.240": "31.24", "100": "100", "1E+2": "100",
                 "-0.0": "0", "007": "7", "abc": "abc", 10: "10", 2.5: "2.5"}
        for raw, expected in cases.items():
            self.assertEqual(expected, canon.normalize_decimal(raw), raw)

    def test_input_is_not_mutated(self):
        params = {"account_number": "a", "quantity": "2", "legs": [{"option_id": "B", "side": "BUY", "position_effect": "open"}]}
        before = json.dumps(params, sort_keys=True)
        canon.fingerprint("review_option_order", params)
        self.assertEqual(before, json.dumps(params, sort_keys=True))

    def test_unknown_tools_are_refused(self):
        for tool in ("exercise_option", "get_accounts", "cancel_equity_order", "replace_option_order"):
            out = canon.run("run", {"tool": tool, "params": {}})
            self.assertFalse(out["ok"], tool)
            self.assertEqual("UNKNOWN_TOOL", out["errors"][0]["code"])

    def test_bad_inputs(self):
        self.assertEqual("MISSING_FIELD", canon.run("run", {})["errors"][0]["code"])
        self.assertEqual("BAD_INPUT", canon.run("run", {"tool": "review_equity_order", "params": []})["errors"][0]["code"])
        self.assertEqual("MISSING_FIELD", canon.run("compare", {"a": {}})["errors"][0]["code"])

    def test_hooks_lib_copy_matches_when_present(self):
        copy_path = os.path.join(ROOT, "hooks", "lib", "canon.py")
        if not os.path.exists(copy_path):
            self.skipTest("hooks/lib/canon.py is generated by tools/sync_shared.py")
        with open(copy_path, encoding="utf-8") as fh:
            lines = fh.read().splitlines(True)
        # sync_shared.py puts its one-line header right after the shebang (and any coding line),
        # so the copy stays executable; drop that header wherever it sits in the first lines.
        head = [i for i, ln in enumerate(lines[:3]) if ln.startswith("# synced from shared/scripts/canon.py")]
        if head:
            del lines[head[0]]
        with open(SCRIPT, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "".join(lines))


class ContractTests(unittest.TestCase):
    def cli(self, *args, **kw):
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=kw.get("stdin", ""),
                              capture_output=True, text=True, timeout=120)

    def test_selftest_exits_zero(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)

    def test_schema_prints_json_schemas_for_every_op(self):
        doc = json.loads(self.cli("--schema").stdout)
        self.assertEqual(sorted(canon.OPS), sorted(doc["ops"]))
        for pair in doc["ops"].values():
            self.assertIn("$schema", pair["input"])
            self.assertIn("$schema", pair["output"])

    def test_cli_round_trip_and_errors(self):
        res = self.cli("run", stdin=json.dumps({"tool": "review_equity_order", "params": {"account_number": "demo-X4F1",
                                                                                           "symbol": "KO", "side": "buy",
                                                                                           "type": "market", "quantity": "1"}}))
        self.assertEqual(0, res.returncode)
        out = json.loads(res.stdout)
        self.assertTrue(out["ok"])
        self.assertNotIn("demo-X4F1", res.stdout)
        self.assertEqual("BAD_JSON", json.loads(self.cli("run", stdin="[").stdout)["errors"][0]["code"])
        self.assertEqual("UNKNOWN_OP", json.loads(self.cli("hash", stdin="{}").stdout)["errors"][0]["code"])

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
        self.assertIsNone(re.search(r"\bopen\(", source))


if __name__ == "__main__":
    unittest.main()
