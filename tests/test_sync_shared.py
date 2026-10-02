"""Tests for tools/sync_shared.py and tools/gen_rules_tables.py (WP-B), and for the shared rules they sync.

The sync tests build a temp repository from the REAL shared/manifest.json, connector/tool-classes.json and
shared/invariants.md, with stub sources for every other manifest entry and six stub skills, then run the
tool in write and --check modes and break one thing at a time. The rules tests check the committed
shared/connector-rules.md: its generated tables are current, every rule has a Why line, and WP-A's drift
checks (tool names, parameters, param-facts) pass on it.
Stdlib unittest; run with python3 -m unittest discover -s tests.
"""

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "tools")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sync = _load("sync_shared", os.path.join(TOOLS, "sync_shared.py"))
gen = _load("gen_rules_tables", os.path.join(TOOLS, "gen_rules_tables.py"))

SKILLS = (
    "robinhood-trading",
    "robinhood-tax-loss-harvesting",
    "robinhood-exit-guardian",
    "robinhood-options-monitor",
    "robinhood-agent-report-card",
    "robinhood-options-screener",
)
SKILL_TEMPLATE = (
    "---\nname: %s\n---\n# %s\n*Unofficial — not affiliated with Robinhood.*\n\n"
    "<!-- BEGIN shared:invariants -->\n<!-- END shared:invariants -->\n\n## Before you start\nOwned text.\n"
)


def _read(path, mode="r"):
    if "b" in mode:
        with open(path, mode) as fh:
            return fh.read()
    with open(path, mode, encoding="utf-8") as fh:
        return fh.read()


def _write(path, data):
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    if isinstance(data, bytes):
        with open(path, "wb") as fh:
            fh.write(data)
        return
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(data)


def build_repo():
    """A temp repo with the real manifest and stub sources for everything it names."""
    root = tempfile.mkdtemp(prefix="sync-shared-")
    manifest = json.loads(_read(os.path.join(REPO, "shared", "manifest.json")))
    _write(os.path.join(root, "shared", "manifest.json"), _read(os.path.join(REPO, "shared", "manifest.json")))
    _write(
        os.path.join(root, "connector", "tool-classes.json"),
        _read(os.path.join(REPO, "connector", "tool-classes.json")),
    )
    _write(os.path.join(root, "shared", "invariants.md"), _read(os.path.join(REPO, "shared", "invariants.md")))
    _write(os.path.join(root, "LICENSE"), "MIT License\n\nCopyright (c) 2026 test\n")
    for entry in manifest["files"]:
        src = entry["source"]
        if src.endswith(".py"):
            stub = '#!/usr/bin/env python3\n"""%s stub."""\nimport sys\nsys.exit(0)\n' % os.path.basename(src)
        else:
            stub = "# %s\n\nStub content for %s.\n" % (os.path.basename(src), src)
        _write(os.path.join(root, *src.split("/")), stub)
    for name in SKILLS:
        _write(os.path.join(root, "skills", name, "SKILL.md"), SKILL_TEMPLATE % (name, name))
    return root, manifest


def run_sync(root, check=False):
    out, err = io.StringIO(), io.StringIO()
    code = sync.run(root, check=check, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class SyncSharedTest(unittest.TestCase):
    def setUp(self):
        self.root, self.manifest = build_repo()
        self.addCleanup(shutil.rmtree, self.root, True)

    def path(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def test_write_then_check_is_clean(self):
        code, out, err = run_sync(self.root)
        self.assertEqual(code, 0, err)
        self.assertIn("created skills/robinhood-trading/references/connector-rules.md", out)
        code, out, err = run_sync(self.root, check=True)
        self.assertEqual((code, out), (0, ""), err)
        # A second write run changes nothing.
        code, out, _ = run_sync(self.root)
        self.assertEqual((code, out), (0, ""))

    def test_every_manifest_target_is_written(self):
        run_sync(self.root)
        for name in SKILLS:
            for rel in ("LICENSE.txt", "references/connector-rules.md", "scripts/rh_time.py", "scripts/kitconfig.py"):
                self.assertTrue(os.path.isfile(self.path("skills/%s/%s" % (name, rel))), (name, rel))
        for rel in (
            "skills/robinhood-trading/references/wash-sweep.md",
            "skills/robinhood-tax-loss-harvesting/references/wash-sweep.md",
            "skills/robinhood-trading/references/confirm-mode.md",
            "skills/robinhood-trading/scripts/exposure.py",
            "skills/robinhood-agent-report-card/scripts/policy_check.py",
            "skills/robinhood-options-screener/scripts/options_math.py",
            "hooks/lib/canon.py",
            "hooks/lib/order_lint.py",
            "hooks/lib/policy_check.py",
            "hooks/tool-classes.json",
            "hooks/known-tools.txt",
            "skills/robinhood-trading/scripts/tool_inventory.json",
        ):
            self.assertTrue(os.path.isfile(self.path(rel)), rel)
        # Scripts go only where the manifest says.
        self.assertFalse(os.path.exists(self.path("skills/robinhood-agent-report-card/scripts/canon.py")))
        self.assertFalse(os.path.exists(self.path("skills/robinhood-exit-guardian/references/confirm-mode.md")))

    def test_markdown_header_and_body(self):
        run_sync(self.root)
        src = _read(self.path("shared/connector-rules.md"))
        dst = _read(self.path("skills/robinhood-exit-guardian/references/connector-rules.md"))
        self.assertEqual(dst, "<!-- synced from shared/connector-rules.md; do not edit -->\n" + src)

    def test_python_header_follows_shebang(self):
        run_sync(self.root)
        src = _read(self.path("shared/scripts/rh_time.py")).splitlines(True)
        dst = _read(self.path("skills/robinhood-options-monitor/scripts/rh_time.py")).splitlines(True)
        self.assertEqual(dst[0], src[0])
        self.assertEqual(dst[1], "# synced from shared/scripts/rh_time.py; do not edit\n")
        self.assertEqual(dst[2:], src[1:])
        # The copy still runs.
        rc = subprocess.run(
            [sys.executable, self.path("skills/robinhood-options-monitor/scripts/rh_time.py")]
        ).returncode
        self.assertEqual(rc, 0)

    def test_python_header_keeps_coding_line_in_place(self):
        data = b"#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\nx = 1\n"
        out = sync.render_synced(data, "shared/scripts/x.py", "hooks/lib/x.py").decode()
        self.assertEqual(
            out.splitlines()[:3],
            ["#!/usr/bin/env python3", "# -*- coding: utf-8 -*-", "# synced from shared/scripts/x.py; do not edit"],
        )
        plain = sync.render_synced(b"x = 1\n", "shared/scripts/x.py", "hooks/lib/x.py").decode()
        self.assertEqual(plain, "# synced from shared/scripts/x.py; do not edit\nx = 1\n")
        self.assertEqual(sync.render_synced(b"MIT\n", "LICENSE", "skills/a/LICENSE.txt"), b"MIT\n")

    def test_license_is_a_byte_copy(self):
        run_sync(self.root)
        self.assertEqual(
            _read(self.path("LICENSE"), "rb"), _read(self.path("skills/robinhood-trading/LICENSE.txt"), "rb")
        )

    def test_generated_files(self):
        run_sync(self.root)
        classes = json.loads(_read(self.path("connector/tool-classes.json")))
        names = [t["name"] for t in classes["tools"]]
        known = _read(self.path("hooks/known-tools.txt")).splitlines()
        self.assertEqual(known, sorted(names))
        self.assertEqual(len(known), 81)
        self.assertEqual(
            _read(self.path("hooks/tool-classes.json"), "rb"), _read(self.path("connector/tool-classes.json"), "rb")
        )
        inv = json.loads(_read(self.path("skills/robinhood-trading/scripts/tool_inventory.json")))
        self.assertEqual(inv["count"], 81)
        self.assertEqual(sum(inv["class_counts"].values()), 81)
        self.assertEqual(inv["class_counts"]["money"], 5)
        self.assertEqual(inv["tools"]["place_advanced_order"], "money")
        self.assertEqual(list(inv["tools"]), names)
        self.assertIn("get_market_hours", inv["absent_referenced"])

    def test_invariants_region_replaced_and_rest_untouched(self):
        run_sync(self.root)
        text = _read(self.path("skills/robinhood-exit-guardian/SKILL.md"))
        block = _read(self.path("shared/invariants.md"))
        self.assertIn(block, text)
        self.assertTrue(text.startswith("---\nname: robinhood-exit-guardian\n---\n# robinhood-exit-guardian\n"))
        self.assertTrue(text.endswith("\n## Before you start\nOwned text.\n"))
        self.assertEqual(text.count("<!-- BEGIN shared:invariants"), 1)

    def test_check_reports_stale_copy_with_diff_and_writes_nothing(self):
        run_sync(self.root)
        target = self.path("skills/robinhood-options-screener/references/connector-rules.md")
        _write(target, _read(target) + "local edit\n")
        before = _read(target)
        code, out, err = run_sync(self.root, check=True)
        self.assertEqual(code, 1)
        self.assertIn("a/skills/robinhood-options-screener/references/connector-rules.md", out)
        self.assertIn("-local edit", out)
        self.assertIn("1 of", err)
        self.assertEqual(_read(target), before)
        # Write mode repairs it.
        self.assertEqual(run_sync(self.root)[0], 0)
        self.assertEqual(run_sync(self.root, check=True)[0], 0)

    def test_check_reports_missing_target(self):
        run_sync(self.root)
        os.remove(self.path("hooks/lib/canon.py"))
        code, out, _ = run_sync(self.root, check=True)
        self.assertEqual(code, 1)
        self.assertIn("/dev/null", out)
        self.assertIn("b/hooks/lib/canon.py", out)

    def test_check_reports_edited_invariants_region(self):
        run_sync(self.root)
        skill = self.path("skills/robinhood-trading/SKILL.md")
        _write(skill, _read(skill).replace("Never invent a quantity", "Invent a quantity"))
        code, out, _ = run_sync(self.root, check=True)
        self.assertEqual(code, 1)
        self.assertIn("+3. Never invent a quantity", out)

    def test_source_change_propagates(self):
        run_sync(self.root)
        _write(self.path("shared/connector-rules.md"), "# changed rules\n")
        code, out, _ = run_sync(self.root, check=True)
        self.assertEqual(code, 1)
        self.assertEqual(out.count("+++ b/skills/"), len(SKILLS))
        run_sync(self.root)
        self.assertEqual(
            _read(self.path("skills/robinhood-agent-report-card/references/connector-rules.md")),
            "<!-- synced from shared/connector-rules.md; do not edit -->\n# changed rules\n",
        )

    def test_missing_named_skill_is_an_error(self):
        shutil.rmtree(self.path("skills/robinhood-options-monitor"))
        code, _, err = run_sync(self.root, check=True)
        self.assertEqual(code, 1)
        self.assertIn("target skill directory skills/robinhood-options-monitor does not exist", err)

    def test_missing_source_is_an_error(self):
        os.remove(self.path("shared/confirm-mode.md"))
        code, _, err = run_sync(self.root)
        self.assertEqual(code, 1)
        self.assertIn("shared/confirm-mode.md: source is missing", err)
        # Everything else was still written.
        self.assertTrue(os.path.isfile(self.path("skills/robinhood-trading/references/connector-rules.md")))

    def test_missing_markers_is_an_error(self):
        _write(self.path("skills/robinhood-exit-guardian/SKILL.md"), "---\nname: x\n---\n# x\n")
        code, _, err = run_sync(self.root)
        self.assertEqual(code, 1)
        self.assertIn("skills/robinhood-exit-guardian/SKILL.md: invariants markers missing", err)

    def test_bad_invariants_source_is_an_error(self):
        _write(self.path("shared/invariants.md"), "no markers here\n")
        code, _, err = run_sync(self.root)
        self.assertEqual(code, 1)
        self.assertIn("shared/invariants.md: must start with a line beginning", err)

    def test_orphan_synced_file_is_reported(self):
        run_sync(self.root)
        _write(
            self.path("skills/robinhood-trading/scripts/old_helper.py"),
            "#!/usr/bin/env python3\n# synced from shared/scripts/old_helper.py; do not edit\n",
        )
        _write(self.path("hooks/lib/gone.py"), "# synced from shared/scripts/gone.py; do not edit\n")
        _write(self.path("skills/robinhood-trading/references/owned.md"), "# skill-owned file, no header\n")
        code, _, err = run_sync(self.root, check=True)
        self.assertEqual(code, 1)
        self.assertIn("skills/robinhood-trading/scripts/old_helper.py: carries a sync header", err)
        self.assertIn("hooks/lib/gone.py: carries a sync header", err)
        self.assertNotIn("owned.md", err)

    def test_duplicate_target_is_an_error(self):
        manifest = json.loads(_read(self.path("shared/manifest.json")))
        manifest["files"].append(
            {"source": "shared/wash-sweep.md", "targets": ["skills/robinhood-trading/references/connector-rules.md"]}
        )
        _write(self.path("shared/manifest.json"), json.dumps(manifest))
        code, _, err = run_sync(self.root, check=True)
        self.assertEqual(code, 1)
        self.assertIn("listed twice in the manifest", err)

    def test_malformed_manifest_exits_2(self):
        _write(self.path("shared/manifest.json"), "{not json")
        self.assertEqual(run_sync(self.root)[0], 2)
        _write(self.path("shared/manifest.json"), json.dumps({"files": [{"source": "x"}]}))
        self.assertEqual(run_sync(self.root)[0], 2)
        _write(self.path("shared/manifest.json"), json.dumps({"staging": []}))
        self.assertEqual(run_sync(self.root)[0], 2)

    def test_target_expansion(self):
        self.assertEqual(
            sync.expand_target(self.root, "skills/*/scripts/", "shared/scripts/rh_time.py"),
            ["skills/%s/scripts/rh_time.py" % n for n in sorted(SKILLS)],
        )
        self.assertEqual(
            sync.expand_target(self.root, "hooks/lib/canon.py", "shared/scripts/canon.py"), ["hooks/lib/canon.py"]
        )
        for bad in ("/abs/path", "skills/../x", "skills/*/a/*/b", "hooks/*.py"):
            with self.assertRaises(ValueError):
                sync.expand_target(self.root, bad, "shared/x.md")

    def test_cli(self):
        tool = os.path.join(TOOLS, "sync_shared.py")
        rc = subprocess.run(
            [sys.executable, tool, "--root", self.root, "--check", "--quiet"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).returncode
        self.assertEqual(rc, 1)
        rc = subprocess.run(
            [sys.executable, tool, "--root", self.root, "--quiet"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ).returncode
        self.assertEqual(rc, 0)
        rc = subprocess.run(
            [sys.executable, tool, "--root", self.root, "--check"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ).returncode
        self.assertEqual(rc, 0)

    def test_synced_hook_libs_pass_drift_check_7(self):
        run_sync(self.root)
        cd = _load("check_drift_for_sync", os.path.join(TOOLS, "check_drift.py"))
        self.assertEqual(cd.run_checks(self.root, checks=[7])[7], [])


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.manifest = sync.load_manifest(os.path.join(REPO, "shared", "manifest.json"))

    def test_structure_matches_spec(self):
        self.assertEqual(sorted(self.manifest), ["files", "generated", "invariants", "license"])
        self.assertEqual(self.manifest["license"], {"source": "LICENSE", "targets": ["skills/*/LICENSE.txt"]})
        self.assertEqual(
            self.manifest["invariants"],
            {
                "source": "shared/invariants.md",
                "targets": ["skills/*/SKILL.md"],
                "begin": "<!-- BEGIN shared:invariants",
                "end": "<!-- END shared:invariants -->",
            },
        )
        kinds = sorted((g["to"], g["kind"]) for g in self.manifest["generated"])
        self.assertEqual(
            kinds,
            [
                ("hooks/known-tools.txt", "names"),
                ("hooks/tool-classes.json", "copy"),
                ("skills/robinhood-trading/scripts/tool_inventory.json", "names_classes"),
            ],
        )

    def test_hook_libs_and_confirm_mode(self):
        by_source = dict((e["source"], e["targets"]) for e in self.manifest["files"])
        for name in ("canon", "order_lint", "policy_check"):
            self.assertIn("hooks/lib/%s.py" % name, by_source["shared/scripts/%s.py" % name])
        # Overrides O3: confirm mode ships wired but off; its agent flow is synced into the core skill only.
        self.assertEqual(by_source["shared/confirm-mode.md"], ["skills/robinhood-trading/references/confirm-mode.md"])

    def test_no_staging_paths(self):
        # Overrides O2: one release, no staging/ directory.
        self.assertNotIn("staging", json.dumps(self.manifest))

    def test_named_skills_are_the_six(self):
        named = set()
        for entry in self.manifest["files"]:
            for t in entry["targets"]:
                parts = t.split("/")
                if parts[0] == "skills" and parts[1] != "*":
                    named.add(parts[1])
        self.assertTrue(named <= set(SKILLS), named - set(SKILLS))


class GenRulesTablesTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gen-rules-")
        self.addCleanup(shutil.rmtree, self.root, True)
        classes = {
            "snapshot_captured_at": "2026-09-21",
            "classes": {"read": "Reads.", "simulate": "Simulates | never places.", "money": "Real money."},
            "absent_referenced": [{"name": "get_market_hours", "note": "never existed"}],
            "older_docs_disagree": {"tools": ["get_scans"]},
            "not_in_published_list": {"tools": ["get_scans", "get_alerts"]},
            "availability_notes": [{"tools": ["review_advanced_order"], "observed": "2026-09-22"}],
            "tools": [
                {"name": "get_accounts", "family": "accounts", "class": "read", "account_param": None},
                {"name": "get_portfolio", "family": "accounts", "class": "read", "account_param": "account_number"},
                {
                    "name": "get_realized_pnl",
                    "family": "accounts",
                    "class": "read",
                    "account_param": "account_number=rhs_value",
                },
                {
                    "name": "review_equity_order",
                    "family": "orders",
                    "class": "simulate",
                    "account_param": "account_number",
                },
                {"name": "place_equity_order", "family": "orders", "class": "money", "account_param": "account_number"},
                {
                    "name": "review_advanced_order",
                    "family": "orders",
                    "class": "simulate",
                    "account_param": "account_number",
                },
                {
                    "name": "get_crypto_quotes",
                    "family": "marketdata",
                    "class": "read",
                    "account_param": "rhs_account_number",
                },
                {"name": "get_equity_quotes", "family": "marketdata", "class": "read", "account_param": None},
                {"name": "get_scans", "family": "scanner", "class": "read", "account_param": None},
                {"name": "get_alerts", "family": "watchlists-alerts", "class": "read", "account_param": None},
            ],
        }
        snapshot = {
            "tools": [
                {
                    "name": t["name"],
                    "inputSchema": {
                        "required": ["account_number"]
                        if t["account_param"] in ("account_number", "account_number=rhs_value")
                        else []
                    },
                }
                for t in classes["tools"]
            ]
        }
        _write(os.path.join(self.root, "connector", "tool-classes.json"), json.dumps(classes))
        _write(os.path.join(self.root, "connector", "tools.snapshot.json"), json.dumps(snapshot))
        self.rules = os.path.join(self.root, "shared", "connector-rules.md")
        _write(
            self.rules,
            "# Rules\n\n## R4: keys\n<!-- BEGIN generated:r4 (generated) -->\nstale\n"
            "<!-- END generated:r4 -->\n- Why: x.\n\n"
            "## R15: classes\n<!-- BEGIN generated:r15 -->\n<!-- END generated:r15 -->\n- Why: y.\n",
        )

    def run_gen(self, check=False):
        out, err = io.StringIO(), io.StringIO()
        return gen.run(self.root, check=check, out=out, err=err), out.getvalue(), err.getvalue()

    def test_render_and_idempotence(self):
        code, out, err = self.run_gen(check=True)
        self.assertEqual(code, 1, err)
        self.assertIn("-stale", out)
        self.assertEqual(self.run_gen()[0], 0)
        self.assertEqual(self.run_gen(check=True)[0], 0)
        text = _read(self.rules)
        self.assertIn(
            "| key `account_number` ← the alphanumeric `account_number` | "
            "`get_portfolio`, `review_/place_equity_order`, `review_advanced_order` |",
            text,
        )
        self.assertIn("| key `account_number` ← **the `rhs_account_number` VALUE** | `get_realized_pnl` |", text)
        self.assertIn("| key `rhs_account_number` ← `rhs_account_number` | `get_crypto_quotes` (optional) |", text)
        self.assertIn(
            "| no account parameter (profile-wide or market data) | "
            "`get_accounts`, `get_equity_quotes`; every scanner, watchlist and alert tool |",
            text,
        )
        self.assertIn(
            "| `simulate` | 2 | Simulates \\| never places. | `review_equity_order`, `review_advanced_order` |", text
        )
        self.assertIn("All 10 tools in the connector snapshot (captured 2026-09-21), by class:", text)
        self.assertIn("- Absent though referenced, not exposed (never call them): `get_market_hours`.", text)
        self.assertIn("- 2 live tools are missing from Robinhood's published tool list", text)
        self.assertIn("(observed 2026-09-22; R26): `review_advanced_order`.", text)
        self.assertTrue(text.startswith("# Rules\n\n## R4: keys\n<!-- BEGIN generated:r4 (generated) -->\n| Key"))
        self.assertTrue(text.endswith("<!-- END generated:r15 -->\n- Why: y.\n"))

    def test_large_family_is_summarized(self):
        tools = [{"name": "get_x%d" % i, "family": "marketdata", "account_param": None} for i in range(5)]
        tools.append({"name": "get_equity_tradability", "family": "marketdata", "account_param": "account_number"})
        self.assertEqual(gen.null_row_text(tools), "the other market-data tools")

    def test_missing_markers_exit_2(self):
        _write(self.rules, "# Rules\n<!-- BEGIN generated:r4 -->\n<!-- END generated:r4 -->\n")
        code, _, err = self.run_gen()
        self.assertEqual(code, 2)
        self.assertIn("generated:r15", err)

    def test_regeneration_that_breaks_a_fact_is_refused(self):
        facts = {"facts": [{"id": "stale-claim", "file": "shared/connector-rules.md", "claim_regex": "(?m)^stale$"}]}
        _write(os.path.join(self.root, "connector", "param-facts.json"), json.dumps(facts))
        before = _read(self.rules)
        code, _, err = self.run_gen()
        self.assertEqual(code, 1)
        self.assertIn("would break param-fact stale-claim", err)
        self.assertEqual(_read(self.rules), before)

    def test_cli_on_repo_is_current(self):
        rc = subprocess.run(
            [sys.executable, os.path.join(TOOLS, "gen_rules_tables.py"), "--check"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(rc.returncode, 0, rc.stdout.decode() + rc.stderr.decode())


class SharedRulesTest(unittest.TestCase):
    """The committed shared/connector-rules.md and shared/invariants.md."""

    @classmethod
    def setUpClass(cls):
        cls.rules = _read(os.path.join(REPO, "shared", "connector-rules.md"))
        cls.invariants = _read(os.path.join(REPO, "shared", "invariants.md"))

    def test_rules_r0_to_r26_in_order_each_with_why(self):
        headings = re.findall(r"^## R(\d+):", self.rules, re.M)
        self.assertEqual([int(h) for h in headings], list(range(27)))
        sections = re.split(r"^## R\d+:", self.rules, flags=re.M)[1:]
        for n, sec in enumerate(sections):
            self.assertRegex(sec, r"(?m)^- Why: .+\[[^\]]+\]\.?$", "R%d has no Why line with a source" % n)

    def test_rules_header_and_length(self):
        self.assertIn("Verified against 81 live tools on 2026-09-22", self.rules.splitlines()[2])
        self.assertIn("not affiliated with Robinhood Markets, Inc.", self.rules.splitlines()[2])
        self.assertLessEqual(len(self.rules.splitlines()), 260)

    def test_override_rules_present(self):
        self.assertIn("the tool you requested cannot be found or does not exist", self.rules)
        self.assertIn('Never report "no OCO orders"', self.rules)
        self.assertIn("`market_data_disclosure`. Show it verbatim and unmodified", self.rules)
        self.assertIn("`order_checks` from `review_equity_order` is an object, not a list", self.rules)
        self.assertRegex(self.rules, r"`state` came back empty.{0,40}never use it as a session signal")

    def test_invariants_block_shape(self):
        lines = self.invariants.rstrip("\n").split("\n")
        self.assertTrue(lines[0].startswith("<!-- BEGIN shared:invariants"))
        self.assertEqual(lines[-1], "<!-- END shared:invariants -->")
        self.assertEqual(
            [ln.split(".", 1)[0] for ln in lines if re.match(r"^\d\.", ln)], [str(i) for i in range(1, 10)]
        )
        self.assertIn("`place_advanced_order`", self.invariants)

    def test_drift_checks_pass_on_shared_rules(self):
        cd = _load("check_drift_for_rules", os.path.join(TOOLS, "check_drift.py"))
        results = cd.run_checks(REPO, checks=[1, 2, 4])
        mine = ("shared/connector-rules.md", "shared/invariants.md")
        bad = [str(f) for n in results for f in results[n] if f.path in mine]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
