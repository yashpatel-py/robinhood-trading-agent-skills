"""Tests for tools/check_drift.py (WP-A): each of the seven checks has a passing and a failing case.

Every test builds a small repository in a temp directory from the real connector files plus spec-exact
hook, Gemini and Codex manifests (build spec D.4.1 and D.7), then mutates one thing.
Stdlib unittest; run with python3 -m unittest discover -s tests.
"""

import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONNECTOR = os.path.join(REPO, "connector")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cd = _load("check_drift", os.path.join(REPO, "tools", "check_drift.py"))

HOOKS_JSON = {
    "description": "Preflight guard (test fixture).",
    "hooks": {
        "SessionStart": [
            {
                "hooks": [
                    {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh" session', "timeout": 5}
                ]
            }
        ],
        "PreToolUse": [
            {
                "matcher": "^mcp__.+__(place|exercise|replace)_[A-Za-z0-9_]+$",
                "hooks": [
                    {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh" money', "timeout": 15}
                ],
            },
            {
                "matcher": "^mcp__.*[Rr]obinhood.*__(submit|execute|transfer|withdraw|deposit|stake|unstake|convert|send|buy|sell|trade|liquidate|lend|borrow)_[A-Za-z0-9_]+$",
                "hooks": [
                    {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh" money', "timeout": 15}
                ],
            },
            {
                "matcher": "^mcp__.+__(cancel_[A-Za-z0-9_]+|delete_alert)$",
                "hooks": [
                    {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh" cancel', "timeout": 10}
                ],
            },
            {
                "matcher": "^mcp__.+__[A-Za-z0-9_]+$",
                "hooks": [
                    {"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh" classify', "timeout": 10}
                ],
            },
        ],
    },
}
MONEY = ["place_equity_order", "place_option_order", "place_crypto_order", "place_advanced_order", "exercise_option"]
GEMINI = {
    "name": "robinhood-trading",
    "version": "2.0.0",
    "mcpServers": {"robinhood": {"httpUrl": "https://agent.robinhood.com/mcp/trading", "excludeTools": list(MONEY)}},
}
CODEX = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "mcp__.*__(place|exercise|replace)_.*",
                "hooks": [{"type": "command", "command": "sh <REPO>/hooks/guard.sh money --format codex"}],
            }
        ]
    }
}
DENY = {
    "permissions": {
        "deny": ["mcp__robinhood-trading__" + t for t in MONEY]
        + ["mcp__plugin_unofficial-rh-connector_robinhood__" + t for t in MONEY]
    }
}

RULES_MD = """# Connector rules (test fixture)

## R4: Account-number key and value, per tool

<!-- BEGIN generated:R4 -->
| Key → value | Tools |
|---|---|
| key `account_number` ← the alphanumeric `account_number` | `get_portfolio`, `get_equity_positions`, `get_option_positions`, `get_equity_tax_lots`, `get_equity_tradability`, `get_equity_orders`, `get_advanced_orders`, `get_option_orders`, `review_/place_/cancel_equity_order`, `review_/place_/cancel_advanced_order`, `review_/place_/cancel_option_order`, `exercise_option`, `cancel_option_exercise`, `get_option_level_upgrade_info`, `get_limited_margin_upgrade_info` |
| key `account_number` ← **the `rhs_account_number` VALUE** | `get_realized_pnl`, `get_pnl_trade_history` |
| key `rhs_account_number` ← `rhs_account_number` | `get_crypto_positions`, `get_crypto_orders`, `preview_/place_/cancel_crypto_order`, `get_crypto_quotes` (optional) |
| no account parameter (profile-wide) | every watchlist and alert tool, `get_accounts`, `get_crypto_account_onboarding_info` |
<!-- END generated:R4 -->

## R5: Caching

Re-fetch `get_accounts` before any all-accounts request.
"""

SKILL_MD = """# Demo skill

1. `get_accounts {}` first. Buying power comes from `get_portfolio {account_number}`.
2. `get_option_positions {account_number, nonzero: true, cursor}` for current holdings.
3. `get_realized_pnl {account_number: <rhs_account_number VALUE>, span: day|week|month|3month|year|all}`.
4. `search {query, asset_type: "instrument"}` to resolve names.
5. `create_alert {symbol, condition_type, indicator: {period: 50, interval_secs: 86400}, asset_class}`.
6. `review_option_order {account_number: <agentic>, legs: [{option_id, side, position_effect, ratio_quantity}], quantity (from the user, string), type, price}`.
7. `get_equity_orders {account_number: <agentic>, created_at_gte: <now − 95 days, UTC>, cursor}`.
8. Never call `place_equity_order` or `exercise_option`. The response field `mark_price` is the option mark.

There is **no** `get_market_hours` tool; use the clock rules instead.

- **Absent though referenced:**
  - `replace_option_order`: cancel and resubmit instead; the user carries the fill risk in between.
  - `get_crypto_tax_lots`: specific-lot crypto sells happen in the app, where the lots are visible to the user.
  - `get_quotes`: named in `get_watchlist_items`; route by `object_type`, and price each item with its own quote tool.
"""

SCRIPT = "# canon.py test fixture\n\ndef run(x):\n    return x\n"


def write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content if isinstance(content, str) else json.dumps(content, indent=2, ensure_ascii=False) + "\n")
    return path


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def valid_facts(n=40):
    base = {
        "file": "skills/demo/SKILL.md",
        "claim_regex": r"nonzero: true",
        "tool": "get_option_positions",
        "schema_path": "inputSchema.properties.nonzero.description",
        "must_contain": ["True to return only currently-open positions"],
    }
    facts = []
    for i in range(n):
        f = dict(base)
        f["id"] = "fact-%02d" % i
        facts.append(f)
    return {"version": 1, "facts": facts}


class DriftTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "connector"))
        for fn in ("tools.snapshot.json", "tool-classes.json", "drift-allow.txt"):
            shutil.copy(os.path.join(CONNECTOR, fn), os.path.join(self.root, "connector", fn))
        shutil.copytree(os.path.join(CONNECTOR, "schemas"), os.path.join(self.root, "connector", "schemas"))
        write(self.root, "connector/param-facts.json", valid_facts())
        write(self.root, "hooks/hooks.json", HOOKS_JSON)
        write(self.root, "gemini-extension.json", GEMINI)
        write(self.root, "integrations/codex/hooks.json", CODEX)
        write(self.root, "integrations/claude-code/settings.deny.json", DENY)
        write(self.root, "shared/connector-rules.md", RULES_MD)
        write(self.root, "skills/demo/SKILL.md", SKILL_MD)
        write(self.root, "shared/scripts/canon.py", SCRIPT)
        write(self.root, "hooks/lib/canon.py", SCRIPT)
        write(
            self.root,
            "shared/manifest.json",
            {
                "files": [
                    {"source": "shared/scripts/canon.py", "targets": ["skills/demo/scripts/", "hooks/lib/canon.py"]}
                ]
            },
        )

    def tearDown(self):
        shutil.rmtree(self.root)

    def run_check(self, n, **kw):
        return cd.run_checks(self.root, [n], **kw)[n]

    def assertPasses(self, n, **kw):
        fails = self.run_check(n, **kw)
        self.assertEqual([str(f) for f in fails], [])

    def assertFailsWith(self, n, pattern, **kw):
        fails = [str(f) for f in self.run_check(n, **kw)]
        self.assertTrue(any(re.search(pattern, f) for f in fails), "no failure matching %r in %r" % (pattern, fails))
        return fails

    def edit_json(self, rel, fn):
        path = os.path.join(self.root, rel)
        data = read_json(path)
        fn(data)
        write(self.root, rel, data)


class FullFixtureTests(DriftTestCase):
    def test_all_checks_pass_on_the_fixture(self):
        results = cd.run_checks(self.root)
        self.assertEqual({n: [str(f) for f in fs] for n, fs in results.items() if fs}, {})

    def test_cli_output_format_and_exit_codes(self):
        script = os.path.join(REPO, "tools", "check_drift.py")
        ok = subprocess.run([sys.executable, script, "--root", self.root], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertEqual(ok.stdout, "")
        write(self.root, "skills/demo/SKILL.md", SKILL_MD + "\nCall `get_account_details` next.\n")
        bad = subprocess.run(
            [sys.executable, script, "--root", self.root, "--checks", "1"], capture_output=True, text=True
        )
        self.assertEqual(bad.returncode, 1)
        self.assertRegex(bad.stdout.strip(), r"^skills/demo/SKILL\.md:\d+: unknown tool name `get_account_details`")
        usage = subprocess.run([sys.executable, script, "--checks", "9"], capture_output=True, text=True)
        self.assertEqual(usage.returncode, 2)


class Check1ToolNames(DriftTestCase):
    def test_pass_live_absent_and_allowlisted_names(self):
        self.assertPasses(1)

    def test_fail_unknown_tool(self):
        write(self.root, "docs/guide.md", "Then call `get_account_details` for the balance.\n")
        self.assertFailsWith(1, r"docs/guide\.md:1: unknown tool name `get_account_details`")

    def test_fail_absent_tool_used_as_if_live(self):
        write(
            self.root,
            "skills/demo/references/x.md",
            "Step 1: call `get_market_hours` and read the session.\n" + "." * 400 + "\n",
        )
        self.assertFailsWith(1, r"references/x\.md:1: `get_market_hours` is not exposed")

    def test_fail_prefixed_robinhood_name(self):
        write(
            self.root, "integrations/demo/settings.json", '{"deny": ["mcp__robinhood-trading__place_bracket_order"]}\n'
        )
        self.assertFailsWith(1, r"unknown tool name `place_bracket_order`")

    def test_fail_bad_allowlist_entries(self):
        with open(os.path.join(self.root, "connector/drift-allow.txt"), "a", encoding="utf-8") as fh:
            fh.write("review_color\nget_accounts  # not allowed: live tool\nget_quotes  # not allowed: absent\n")
        fails = self.assertFailsWith(1, r"review_color needs a reason")
        self.assertTrue(any("get_accounts is a live connector tool" in f for f in fails))
        self.assertTrue(any("get_quotes is a known-absent tool" in f for f in fails))

    def test_fail_when_search_disappears(self):
        self.edit_json(
            "connector/tools.snapshot.json",
            lambda d: d.__setitem__("tools", [t for t in d["tools"] if t["name"] != "search"]),
        )
        self.assertFailsWith(1, r"`search` is no longer in")


class Check2Parameters(DriftTestCase):
    def test_pass_usages_and_r4_table(self):
        self.assertPasses(2)

    def test_fail_unknown_parameter(self):
        write(
            self.root, "skills/demo/references/y.md", "Use `get_option_positions {account_number, non_zero: true}`.\n"
        )
        self.assertFailsWith(2, r"y\.md:1: `get_option_positions` has no parameter `non_zero`")

    def test_fail_unknown_nested_parameter(self):
        write(
            self.root,
            "skills/demo/references/y.md",
            "`create_alert {symbol, condition_type, indicator: {period: 50, interval: 86400}}`\n"
            "`review_option_order {account_number, legs: [{option_id, side, effect}], quantity}`\n",
        )
        fails = self.assertFailsWith(2, r"`create_alert\.indicator` has no parameter `interval`")
        self.assertTrue(any("`review_option_order.legs[]` has no parameter `effect`" in f for f in fails))

    def test_fail_paren_form(self):
        write(self.root, "docs/z.md", 'Call get_equity_quotes(symbol="AAPL") to price it.\n')
        self.assertFailsWith(2, r"`get_equity_quotes` has no parameter `symbol`")

    def test_fail_r4_row_wrong_key(self):
        bad = RULES_MD.replace("| `get_realized_pnl`, `get_pnl_trade_history` |", "| `get_realized_pnl` |").replace(
            "`get_portfolio`, ", "`get_portfolio`, `get_pnl_trade_history`, `get_accounts`, "
        )
        write(self.root, "shared/connector-rules.md", bad)
        fails = self.assertFailsWith(
            2, r"R4 table puts `get_pnl_trade_history` under account_number but .* says account_number=rhs_value"
        )
        self.assertTrue(any("R4 table: `get_accounts` has no `account_number` parameter" in f for f in fails))

    def test_fail_r4_table_missing_or_incomplete(self):
        write(self.root, "shared/connector-rules.md", RULES_MD.replace(", `get_crypto_quotes` (optional)", ""))
        self.assertFailsWith(2, r"R4 table omits `get_crypto_quotes`")
        write(self.root, "shared/connector-rules.md", "# Rules\n\nNo table here.\n")
        self.assertFailsWith(2, r"no R4 account-number table found")


class Check3Classes(DriftTestCase):
    def test_pass_classes_and_guard_coverage(self):
        self.assertPasses(3)

    def test_fail_unclassified_and_extra_tools(self):
        def mutate(d):
            d["tools"] = [t for t in d["tools"] if t["name"] != "get_alerts"]
            d["tools"].append(
                {"name": "get_ghost", "family": "accounts", "class": "read", "account_param": None, "paginates": None}
            )

        self.edit_json("connector/tool-classes.json", mutate)
        fails = self.assertFailsWith(3, r"get_alerts is in the snapshot but has no class")
        self.assertTrue(any("get_ghost is classified but not in the snapshot" in f for f in fails))

    def test_fail_money_tool_downgraded(self):
        def mutate(d):
            for t in d["tools"]:
                if t["name"] == "place_advanced_order":
                    t["class"] = "simulate"

        self.edit_json("connector/tool-classes.json", mutate)
        fails = self.assertFailsWith(3, r"money class is .*expected exactly")
        self.assertTrue(any("place_advanced_order places, exercises or replaces orders" in f for f in fails))

    def test_fail_account_param_paginates_and_twins(self):
        def mutate(d):
            for t in d["tools"]:
                if t["name"] == "get_pnl_trade_history":
                    t["account_param"] = "account_number"
                if t["name"] == "get_equity_orders":
                    t["paginates"] = None
                if t["name"] == "review_equity_order":
                    t["place_twin"] = "place_option_order"

        self.edit_json("connector/tool-classes.json", mutate)
        fails = self.assertFailsWith(
            3, r"get_pnl_trade_history: the schema says account_number takes the rhs_account_number"
        )
        self.assertTrue(any("get_equity_orders: paginates is None but the schema" in f for f in fails))
        self.assertTrue(any("review_equity_order.place_twin place_option_order" in f for f in fails))

    def test_fail_hook_matcher_misses_exercise(self):
        self.edit_json(
            "hooks/hooks.json",
            lambda d: d["hooks"]["PreToolUse"][0].__setitem__("matcher", "^mcp__.+__place_[A-Za-z0-9_]+$"),
        )
        self.assertFailsWith(
            3, r"hooks/hooks\.json:\d+: hooks\.json money matcher .* does not match money tool exercise_option"
        )

    def test_fail_hook_matcher_too_broad_or_missing(self):
        self.edit_json("hooks/hooks.json", lambda d: d["hooks"]["PreToolUse"][0].__setitem__("matcher", "^mcp__.+$"))
        self.assertFailsWith(3, r"also matches non-money tool get_accounts")
        os.remove(os.path.join(self.root, "hooks/hooks.json"))
        self.assertFailsWith(3, r"hooks/hooks\.json:1: cannot verify guard coverage: file not found")

    def test_fail_gemini_exclude_list(self):
        self.edit_json(
            "gemini-extension.json",
            lambda d: d["mcpServers"]["robinhood"].__setitem__(
                "excludeTools",
                ["place_equity_order", "place_option_order", "place_crypto_order", "exercise_option", "get_accounts"],
            ),
        )
        fails = self.assertFailsWith(3, r"excludeTools is missing money tool place_advanced_order")
        self.assertTrue(any("excludeTools lists get_accounts" in f for f in fails))

    def test_fail_codex_matcher_and_deny_list(self):
        self.edit_json(
            "integrations/codex/hooks.json",
            lambda d: d["hooks"]["PreToolUse"][0].__setitem__("matcher", "mcp__.*__(place|replace)_.*"),
        )
        self.edit_json(
            "integrations/claude-code/settings.deny.json",
            lambda d: d["permissions"]["deny"].remove("mcp__robinhood-trading__place_advanced_order"),
        )
        fails = self.assertFailsWith(3, r"Codex matcher .* does not match money tool exercise_option")
        self.assertTrue(
            any("deny list for server robinhood-trading is missing place_advanced_order" in f for f in fails)
        )

    def test_fail_bundled_codex_plugin_matcher(self):
        # The Codex plugin bundles its own copy of the guard (.codex-plugin/hooks.json): it must cover the
        # money class too. Absent, it is not checked (the fixture repo has none by default).
        self.assertPasses(3)
        bundled = json.loads(json.dumps(CODEX))
        bundled["hooks"]["PreToolUse"][0]["matcher"] = "mcp__.*__(place|replace)_.*"
        write(self.root, ".codex-plugin/hooks.json", bundled)
        self.assertFailsWith(3, r"\.codex-plugin/hooks\.json:\d+: Codex plugin matcher .* does not match money "
                                r"tool exercise_option")

    def test_fail_absent_tool_became_live(self):
        def mutate(d):
            d["absent_referenced"] = [a for a in d["absent_referenced"] if a["name"] != "get_quotes"]

        self.edit_json("connector/tool-classes.json", mutate)
        self.assertFailsWith(3, r"absent_referenced lists .*KNOWN_ABSENT is")


class Check4ParamFacts(DriftTestCase):
    def test_pass_forty_valid_facts(self):
        self.assertPasses(4)

    def test_fail_claim_missing_from_file(self):
        write(self.root, "skills/demo/SKILL.md", SKILL_MD.replace("nonzero: true", "open only"))
        self.assertFailsWith(4, r"skills/demo/SKILL\.md:1: fact fact-00: claim no longer found")

    def test_fail_schema_no_longer_backs_claim(self):
        def mutate(d):
            d["facts"][0]["must_contain"] = ["True to return only closed positions"]
            d["facts"][1]["schema_path"] = "inputSchema.properties.nonzero_flag.description"
            d["facts"][2]["tool"] = "get_ghost"

        self.edit_json("connector/param-facts.json", mutate)
        fails = self.assertFailsWith(4, r"fact fact-00: get_option_positions .* no longer contains")
        self.assertTrue(any("fact fact-01: schema_path" in f for f in fails))
        self.assertTrue(any("fact fact-02: tool get_ghost is not in the snapshot" in f for f in fails))

    def test_fail_too_few_or_malformed(self):
        write(self.root, "connector/param-facts.json", valid_facts(39))
        self.assertFailsWith(4, r"only 39 facts; at least 40")
        facts = valid_facts()
        facts["facts"][3]["id"] = "fact-02"
        facts["facts"][4]["claim_regex"] = "(unclosed"
        del facts["facts"][5]["must_contain"]
        facts["facts"][6]["file"] = "skills/demo/NOPE.md"
        write(self.root, "connector/param-facts.json", facts)
        fails = self.assertFailsWith(4, r"duplicate fact id fact-02")
        self.assertTrue(any("claim_regex does not compile" in f for f in fails))
        self.assertTrue(any("fact fact-05 is missing must_contain" in f for f in fails))
        self.assertTrue(any("claim file skills/demo/NOPE.md not found" in f for f in fails))

    def test_file_list_matches_any(self):
        facts = valid_facts()
        facts["facts"][0]["file"] = ["skills/demo/NOPE.md", "skills/demo/SKILL.md"]
        write(self.root, "connector/param-facts.json", facts)
        self.assertPasses(4)


class Check5Snapshot(DriftTestCase):
    def test_pass_and_fresh_enough(self):
        self.assertPasses(5)
        self.assertPasses(5, staleness=30, today=datetime.date(2026, 10, 21))

    def test_fail_stale(self):
        self.assertFailsWith(
            5, r"captured 2026-09-21 is 30 days old \(limit 29\)", staleness=29, today=datetime.date(2026, 10, 21)
        )

    def test_fail_hash_and_count(self):
        def mutate(d):
            d["tools"][0]["description"] += " edited"
            d["tool_count"] = 80

        self.edit_json("connector/tools.snapshot.json", mutate)
        fails = self.assertFailsWith(5, r"HASH_MISMATCH: add_option_to_watchlist: sha256_description")
        self.assertTrue(any("COUNT_MISMATCH" in f for f in fails))

    def test_fail_schemas_markdown_disagrees(self):
        path = os.path.join(self.root, "connector/schemas/research.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        write(
            self.root,
            "connector/schemas/research.md",
            text.replace("Get recent news articles for a stock", "Get recent news for a stock"),
        )
        self.assertFailsWith(5, r"get_equity_news differs between connector/schemas/\*\.md and the snapshot")

    def test_fail_missing_snapshot(self):
        os.remove(os.path.join(self.root, "connector/tools.snapshot.json"))
        self.assertFailsWith(5, r"connector/tools\.snapshot\.json:1: file not found")


class Check6HookAllow(DriftTestCase):
    def test_pass_and_tests_directory_is_exempt(self):
        write(self.root, "hooks/tests/test_guard.sh", 'grep -q \'"permissionDecision":"allow"\' out && exit 1\n')
        self.assertPasses(6)

    def test_fail_allow_in_hook_or_integration(self):
        write(self.root, "hooks/guard.sh", "#!/bin/sh\nprintf '%s' '{\"permissionDecision\": \"allow\"}'\n")
        write(self.root, "integrations/cursor/adapter.json", '{\n  "permission" : "allow"\n}\n')
        write(self.root, "optional/confirm-mode/gate.py", 'print(\'{"permissionDecision":"allow"}\')\n')
        fails = self.assertFailsWith(6, r"hooks/guard\.sh:2: emits a permission \"allow\"")
        self.assertTrue(any(f.startswith("integrations/cursor/adapter.json:2:") for f in fails))
        self.assertTrue(any(f.startswith("optional/confirm-mode/gate.py:1:") for f in fails))


class Check7SyncedCopies(DriftTestCase):
    def test_pass_identical_or_with_sync_header(self):
        self.assertPasses(7)
        write(self.root, "hooks/lib/canon.py", "# synced from shared/scripts/canon.py; do not edit\n" + SCRIPT)
        self.assertPasses(7)

    def test_fail_edited_copy(self):
        write(self.root, "hooks/lib/canon.py", SCRIPT.replace("return x", "return None"))
        self.assertFailsWith(7, r"hooks/lib/canon\.py:1: differs from shared/scripts/canon\.py")

    def test_fail_orphan_or_missing_copy(self):
        write(self.root, "hooks/lib/policy_check.py", SCRIPT)
        self.assertFailsWith(7, r"no source shared/scripts/policy_check\.py")
        os.remove(os.path.join(self.root, "hooks/lib/canon.py"))
        self.assertFailsWith(7, r"hooks/lib/canon\.py \(copy of shared/scripts/canon\.py\) is missing")


class RealRepoArtifacts(unittest.TestCase):
    """The committed WP-A files are internally consistent (independent of other packages' files)."""

    def test_snapshot_and_classes_checks_on_committed_files(self):
        root = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(root, "connector"))
            for fn in ("tools.snapshot.json", "tool-classes.json", "drift-allow.txt"):
                shutil.copy(os.path.join(CONNECTOR, fn), os.path.join(root, "connector", fn))
            shutil.copytree(os.path.join(CONNECTOR, "schemas"), os.path.join(root, "connector", "schemas"))
            write(root, "hooks/hooks.json", HOOKS_JSON)
            write(root, "gemini-extension.json", GEMINI)
            write(root, "integrations/codex/hooks.json", CODEX)
            results = cd.run_checks(root, [1, 3, 5])
            self.assertEqual({n: [str(f) for f in fs] for n, fs in results.items() if fs}, {})
        finally:
            shutil.rmtree(root)

    def test_classes_match_spec_c3(self):
        data = read_json(os.path.join(CONNECTOR, "tool-classes.json"))
        counts = {}
        for t in data["tools"]:
            counts[t["class"]] = counts.get(t["class"], 0) + 1
        self.assertEqual(
            counts, {"read": 48, "enroll_link": 3, "simulate": 5, "write_confirm": 14, "cancel": 6, "money": 5}
        )
        by = {t["name"]: t for t in data["tools"]}
        self.assertEqual(by["place_advanced_order"]["class"], "money")
        self.assertEqual(by["review_advanced_order"]["class"], "simulate")
        self.assertEqual(by["get_advanced_orders"]["class"], "read")
        self.assertEqual(by["cancel_advanced_order"]["class"], "cancel")
        self.assertEqual(by["delete_alert"]["class"], "cancel")
        self.assertEqual(by["preview_scan"]["class"], "simulate")
        self.assertEqual(
            {(t["name"], t["place_twin"]) for t in data["tools"] if "place_twin" in t},
            {
                ("review_equity_order", "place_equity_order"),
                ("review_advanced_order", "place_advanced_order"),
                ("review_option_order", "place_option_order"),
                ("preview_crypto_order", "place_crypto_order"),
            },
        )
        self.assertNotIn("review_twin", by["exercise_option"])
        self.assertEqual(by["get_pnl_trade_history"]["account_param"], "account_number=rhs_value")
        self.assertEqual(by["get_realized_pnl"]["account_param"], "account_number=rhs_value")
        self.assertEqual(by["get_crypto_quotes"]["account_param"], "rhs_account_number")
        self.assertIsNone(by["create_alert"]["account_param"])
        self.assertEqual(len(data["not_in_published_list"]["tools"]), 24)

    def test_param_facts_schema_side_and_fields_claims(self):
        data = read_json(os.path.join(CONNECTOR, "param-facts.json"))
        facts = data["facts"]
        self.assertGreaterEqual(len(facts), 40)
        snap = {t["name"]: t for t in read_json(os.path.join(CONNECTOR, "tools.snapshot.json"))["tools"]}
        for f in facts:
            value, ok = cd.resolve_path(snap[f["tool"]], f["schema_path"])
            self.assertTrue(ok, f["id"])
            hay = value if isinstance(value, str) else cd.snapshot_tools.canonical_json(value)
            for s in f["must_contain"]:
                self.assertIn(s, hay, f["id"])
            re.compile(f["claim_regex"])
            if f["file"] == "connector/FIELDS.md":
                with open(os.path.join(REPO, f["file"]), encoding="utf-8") as fh:
                    self.assertRegex(fh.read(), f["claim_regex"], f["id"])
        span = [f for f in facts if f["id"] == "fields-pnl-history-span"][0]
        self.assertEqual(span["enum"], ["week", "month", "3month", "ytd", "all"])


if __name__ == "__main__":
    unittest.main()
