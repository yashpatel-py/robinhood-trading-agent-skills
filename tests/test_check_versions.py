"""Tests for tools/check_versions.py, plus guards on the real release manifests.

Run: python3 -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import check_versions as cv  # noqa: E402

MONEY_TOOLS = ["place_equity_order", "place_option_order", "place_crypto_order",
               "place_advanced_order", "exercise_option"]

SKILL_TEMPLATE = """---
name: {name}
description: >-
  Use when testing the version checker. Not for anything else.
license: MIT (see LICENSE.txt)
metadata:
  version: {version}                # equals plugin.json version
  author: "yashpatel-py"
  unofficial: "Not affiliated with Robinhood Markets, Inc."
---

# {name}
Body text.
"""

CHANGELOG_TEMPLATE = """# Changelog
All notable changes.

## [Unreleased]

## [{version}] - {date}
### Added
- Something.

## [1.0.0] - 2026-01-01
### Added
- The first release.
"""


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def make_repo(root, version="2.0.0", skills=("alpha", "beta"), date="Unreleased"):
    write_json(root / cv.PLUGIN_MANIFEST, {"name": "robinhood-trading", "version": version})
    write_json(root / cv.MARKETPLACE, {
        "name": "yashpatel-py",
        "plugins": [
            {"name": "robinhood-trading", "source": "./", "version": version},
            {"name": "unofficial-rh-connector", "source": "./plugins/unofficial-rh-connector", "version": version},
        ],
    })
    write_json(root / cv.HELPER_MANIFEST, {"name": "unofficial-rh-connector", "version": version})
    write_json(root / cv.CODEX_MANIFEST, {"name": "robinhood-trading", "version": version})
    write_json(root / cv.GEMINI_MANIFEST, {"name": "robinhood-trading", "version": version})
    (root / cv.CHANGELOG).write_text(CHANGELOG_TEMPLATE.format(version=version, date=date), encoding="utf-8")
    for name in skills:
        path = root / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SKILL_TEMPLATE.format(name=name, version='"{}"'.format(version)), encoding="utf-8")


class RepoTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="check_versions-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)
        make_repo(self.root)

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cv.main(["--root", str(self.root)] + list(args))
        return code, out.getvalue(), err.getvalue()

    def edit_json(self, rel, fn):
        path = self.root / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        fn(data)
        write_json(path, data)

    def assertFailsMentioning(self, fragment, tag=None):
        result = cv.check(self.root, tag)
        self.assertFalse(result["ok"], result)
        self.assertTrue(any(fragment in e for e in result["errors"]),
                        "no error mentions {!r}: {}".format(fragment, result["errors"]))


class AgreementTests(RepoTestCase):
    def test_all_equal_passes(self):
        result = cv.check(self.root)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["version"], "2.0.0")
        labels = {s["source"] for s in result["sources"]}
        for expected in (cv.PLUGIN_MANIFEST, cv.HELPER_MANIFEST, cv.CODEX_MANIFEST, cv.GEMINI_MANIFEST,
                         "skills/alpha/SKILL.md metadata.version", "skills/beta/SKILL.md metadata.version"):
            self.assertIn(expected, labels)
        self.assertEqual(len(result["sources"]), 9)

    def test_main_exit_zero_and_summary(self):
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("OK, 9 sources agree on 2.0.0", out)

    def test_json_output(self):
        code, out, _ = self.run_main("--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertEqual(data["errors"], [])

    def test_empty_tag_argument_is_ignored(self):
        # CI passes '' on branch pushes.
        code, _, err = self.run_main("")
        self.assertEqual(code, 0, err)

    def test_marketplace_core_entry_mismatch(self):
        self.edit_json(cv.MARKETPLACE, lambda d: d["plugins"][0].update(version="1.9.0"))
        self.assertFailsMentioning("plugins[robinhood-trading]: 1.9.0 (expected 2.0.0")

    def test_marketplace_helper_entry_mismatch(self):
        self.edit_json(cv.MARKETPLACE, lambda d: d["plugins"][1].update(version="1.0.0"))
        self.assertFailsMentioning("plugins[unofficial-rh-connector]: 1.0.0")

    def test_marketplace_entry_without_version(self):
        self.edit_json(cv.MARKETPLACE, lambda d: d["plugins"][0].pop("version"))
        self.assertFailsMentioning("plugins[robinhood-trading]: no version string")

    def test_marketplace_missing_required_entry(self):
        self.edit_json(cv.MARKETPLACE, lambda d: d["plugins"].pop(1))
        self.assertFailsMentioning("no plugin entry named unofficial-rh-connector")

    def test_extra_marketplace_entry_with_version_is_checked(self):
        self.edit_json(cv.MARKETPLACE, lambda d: d["plugins"].append({"name": "other", "version": "0.1.0"}))
        self.assertFailsMentioning("plugins[other]: 0.1.0")

    def test_helper_plugin_mismatch(self):
        self.edit_json(cv.HELPER_MANIFEST, lambda d: d.update(version="1.0.0"))
        self.assertFailsMentioning(cv.HELPER_MANIFEST + ": 1.0.0")

    def test_codex_mismatch(self):
        self.edit_json(cv.CODEX_MANIFEST, lambda d: d.update(version="2.0.1"))
        self.assertFailsMentioning(cv.CODEX_MANIFEST + ": 2.0.1")

    def test_gemini_mismatch(self):
        self.edit_json(cv.GEMINI_MANIFEST, lambda d: d.update(version="2.1.0"))
        self.assertFailsMentioning(cv.GEMINI_MANIFEST + ": 2.1.0")

    def test_missing_manifest(self):
        (self.root / cv.CODEX_MANIFEST).unlink()
        self.assertFailsMentioning(cv.CODEX_MANIFEST + ": missing")

    def test_unparseable_manifest(self):
        (self.root / cv.GEMINI_MANIFEST).write_text("{not json", encoding="utf-8")
        self.assertFailsMentioning(cv.GEMINI_MANIFEST + ": unreadable JSON")

    def test_plugin_version_must_be_semver(self):
        for rel in (cv.PLUGIN_MANIFEST, cv.HELPER_MANIFEST, cv.CODEX_MANIFEST, cv.GEMINI_MANIFEST):
            self.edit_json(rel, lambda d: d.update(version="2.0"))
        self.assertFailsMentioning("'2.0' is not a SemVer version")

    def test_main_exit_one_on_mismatch(self):
        self.edit_json(cv.CODEX_MANIFEST, lambda d: d.update(version="2.0.1"))
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("ERROR .codex-plugin/plugin.json: 2.0.1", err)


class SkillVersionTests(RepoTestCase):
    def write_skill(self, name, text):
        path = self.root / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_skill_mismatch_names_the_skill(self):
        self.write_skill("beta", SKILL_TEMPLATE.format(name="beta", version='"1.1.0"'))
        self.assertFailsMentioning("skills/beta/SKILL.md metadata.version: 1.1.0 (expected 2.0.0")

    def test_skill_without_metadata_version(self):
        self.write_skill("beta", "---\nname: beta\ndescription: x\nmetadata:\n  author: \"y\"\n---\nbody\n")
        self.assertFailsMentioning("skills/beta/SKILL.md metadata.version: no version string")

    def test_top_level_version_does_not_count(self):
        self.write_skill("beta", "---\nname: beta\nversion: \"2.0.0\"\nmetadata:\n  author: \"y\"\n---\n")
        self.assertFailsMentioning("skills/beta/SKILL.md metadata.version: no version string")

    def test_skill_without_frontmatter(self):
        self.write_skill("beta", "# no frontmatter\n")
        self.assertFailsMentioning("skills/beta/SKILL.md metadata.version: no YAML frontmatter")

    def test_no_skills_is_an_error(self):
        shutil.rmtree(str(self.root / "skills"))
        self.assertFailsMentioning("no skills found")

    def test_unquoted_single_quoted_and_flow_forms(self):
        self.write_skill("alpha", SKILL_TEMPLATE.format(name="alpha", version="2.0.0"))
        self.write_skill("beta", SKILL_TEMPLATE.format(name="beta", version="'2.0.0'"))
        self.write_skill("gamma", '---\nname: gamma\nmetadata: {author: "y", version: "2.0.0"}\n---\n')
        result = cv.check(self.root)
        self.assertTrue(result["ok"], result["errors"])

    def test_staging_directory_is_not_read(self):
        # O2: one release, no staging/. A stray staging tree must not affect the check.
        path = self.root / "staging" / "skills" / "old" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(SKILL_TEMPLATE.format(name="old", version='"9.9.9"'), encoding="utf-8")
        self.assertTrue(cv.check(self.root)["ok"])


class ChangelogAndTagTests(RepoTestCase):
    def test_latest_heading_skips_unreleased_and_ignores_older(self):
        version, rest = cv.changelog_latest(CHANGELOG_TEMPLATE.format(version="2.0.0", date="Unreleased"))
        self.assertEqual(version, "2.0.0")
        self.assertEqual(rest, "- Unreleased")

    def test_changelog_mismatch(self):
        (self.root / cv.CHANGELOG).write_text(
            CHANGELOG_TEMPLATE.format(version="1.9.0", date="2026-09-01"), encoding="utf-8")
        self.assertFailsMentioning("CHANGELOG.md (latest heading): 1.9.0")

    def test_changelog_missing(self):
        (self.root / cv.CHANGELOG).unlink()
        self.assertFailsMentioning("CHANGELOG.md: missing")

    def test_changelog_without_version_heading(self):
        (self.root / cv.CHANGELOG).write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")
        self.assertFailsMentioning("no version heading")

    def test_matching_tag_forms_pass_with_dated_changelog(self):
        make_repo(self.root, date="2026-09-27")
        for tag in ("v2.0.0", "2.0.0", "refs/tags/v2.0.0"):
            result = cv.check(self.root, tag)
            self.assertTrue(result["ok"], (tag, result["errors"]))

    def test_tag_mismatch(self):
        make_repo(self.root, date="2026-09-27")
        self.assertFailsMentioning("tag v2.0.1: 2.0.1 (expected 2.0.0", tag="v2.0.1")

    def test_non_version_tag(self):
        make_repo(self.root, date="2026-09-27")
        self.assertFailsMentioning("not a version tag", tag="release-candidate")

    def test_tag_requires_dated_changelog_heading(self):
        # O2 ships "## [2.0.0] - Unreleased"; the release date must be set before tagging.
        self.assertTrue(cv.check(self.root)["ok"])
        self.assertFailsMentioning("is not dated", tag="v2.0.0")

    def test_tag_exit_code(self):
        make_repo(self.root, date="2026-09-27")
        code, out, err = self.run_main("v2.0.0")
        self.assertEqual(code, 0, err)
        self.assertIn("tag v2.0.0", out)


class FrontmatterParserTests(unittest.TestCase):
    def test_block_scalar_description_folds(self):
        fm = cv.read_frontmatter("---\nname: x\ndescription: >-\n  line one\n  line two\nlicense: MIT\n---\nbody")
        self.assertEqual(cv.frontmatter_scalar(fm, "description"), "line one line two")
        self.assertEqual(cv.frontmatter_scalar(fm, "license"), "MIT")
        self.assertEqual(cv.frontmatter_scalar(fm, "name"), "x")
        self.assertIsNone(cv.frontmatter_scalar(fm, "missing"))

    def test_literal_block_keeps_relative_indent(self):
        fm = cv.read_frontmatter("---\nnote: |\n  a\n    b\n---\n")
        self.assertEqual(cv.frontmatter_scalar(fm, "note"), "a\n  b")

    def test_comment_inside_quotes_is_kept(self):
        self.assertEqual(cv.strip_comment('"a # b"  # real comment'), '"a # b"')
        self.assertEqual(cv.unquote(cv.strip_comment('"a # b"  # real comment')), "a # b")

    def test_nested_keys_under_metadata_are_not_children(self):
        fm = cv.read_frontmatter("---\nmetadata:\n  other:\n    version: \"9\"\n  version: \"2.0.0\"\n---\n")
        self.assertEqual(cv.metadata_value(fm, "version"), "2.0.0")

    def test_strip_body(self):
        self.assertEqual(cv.strip_body("---\na: b\n---\n\n# Title\ntext\n"), "# Title\ntext\n")
        self.assertEqual(cv.strip_body("# No frontmatter\n"), "# No frontmatter\n")

    def test_unterminated_frontmatter(self):
        self.assertIsNone(cv.read_frontmatter("---\nname: x\n"))
        self.assertIsNone(cv.read_frontmatter("name: x\n"))


class RealRepoManifestTests(unittest.TestCase):
    """Guards on the shipped manifests (WP-L). These encode safety decisions, so a
    change here should be deliberate: O2 (one 2.0.0 release), O3 (confirm mode wired
    but off: order_mode defaults to simulate_only) and spec C.3/D.7 (Gemini hides the
    exact money-class tools)."""

    def load(self, rel):
        return json.loads((REPO / rel).read_text(encoding="utf-8"))

    def test_manifest_versions_agree(self):
        versions = {rel: self.load(rel)["version"]
                    for rel in (cv.PLUGIN_MANIFEST, cv.HELPER_MANIFEST, cv.CODEX_MANIFEST, cv.GEMINI_MANIFEST)}
        for entry in self.load(cv.MARKETPLACE)["plugins"]:
            versions["marketplace:" + entry["name"]] = entry["version"]
        self.assertEqual(set(versions.values()), {"2.0.0"}, versions)

    def test_order_mode_defaults_to_simulate_only(self):
        user_config = self.load(cv.PLUGIN_MANIFEST)["userConfig"]
        self.assertEqual(set(user_config),
                         {"audit_log", "guard_exempt_servers", "order_mode", "max_order_notional_usd",
                          "review_ttl_seconds"})
        self.assertEqual(user_config["order_mode"]["default"], "simulate_only")
        self.assertEqual(user_config["order_mode"]["options"], ["simulate_only", "confirm"])
        self.assertEqual(user_config["max_order_notional_usd"]["default"], "")
        self.assertEqual(user_config["review_ttl_seconds"]["default"], "300")
        self.assertIs(user_config["audit_log"]["default"], True)
        self.assertEqual(user_config["guard_exempt_servers"]["default"], "")

    def test_no_hook_or_server_declared_in_core_manifest(self):
        manifest = self.load(cv.PLUGIN_MANIFEST)
        # hooks/hooks.json and skills/ load from their default locations; the core
        # plugin must not bundle the Robinhood server (the helper plugin does).
        for key in ("hooks", "mcpServers", "skills"):
            self.assertNotIn(key, manifest)

    def test_gemini_excludes_exactly_the_money_tools(self):
        servers = self.load(cv.GEMINI_MANIFEST)["mcpServers"]
        self.assertEqual(sorted(servers["robinhood"]["excludeTools"]), sorted(MONEY_TOOLS))
        self.assertEqual(servers["robinhood"]["httpUrl"], "https://agent.robinhood.com/mcp/trading")

    def test_marketplace_shape(self):
        market = self.load(cv.MARKETPLACE)
        self.assertEqual(market["name"], "yashpatel-py")
        self.assertFalse(market["name"].startswith("robinhood"))
        names = [(p["name"], p["source"]) for p in market["plugins"]]
        self.assertEqual(names, [("robinhood-trading", "./"),
                                 ("unofficial-rh-connector", "./plugins/unofficial-rh-connector")])

    def test_helper_plugin_server(self):
        mcp = self.load("plugins/unofficial-rh-connector/.mcp.json")
        self.assertEqual(mcp, {"mcpServers": {"robinhood": {"type": "http",
                                                             "url": "https://agent.robinhood.com/mcp/trading"}}})

    def test_every_description_says_unofficial(self):
        texts = [self.load(cv.PLUGIN_MANIFEST)["description"], self.load(cv.CODEX_MANIFEST)["description"],
                 self.load(cv.GEMINI_MANIFEST)["description"], self.load(cv.HELPER_MANIFEST)["description"],
                 self.load(cv.MARKETPLACE)["description"]]
        texts += [p["description"] for p in self.load(cv.MARKETPLACE)["plugins"]]
        for text in texts:
            self.assertIn("nofficial", text)
            for word in (" only ", " first "):
                self.assertNotIn(word, " {} ".format(text.lower()).replace(",", " "),
                                 "superlative {!r} in {!r}".format(word.strip(), text))

    def all_descriptions(self):
        texts = {rel: self.load(rel)["description"]
                 for rel in (cv.PLUGIN_MANIFEST, cv.CODEX_MANIFEST, cv.GEMINI_MANIFEST, cv.HELPER_MANIFEST,
                             cv.MARKETPLACE)}
        texts[cv.CODEX_MANIFEST + " interface.shortDescription"] = \
            self.load(cv.CODEX_MANIFEST)["interface"]["shortDescription"]
        for p in self.load(cv.MARKETPLACE)["plugins"]:
            texts["{} plugins[{}]".format(cv.MARKETPLACE, p["name"])] = p["description"]
        return texts

    def test_no_listing_promises_orders_are_never_placed(self):
        # "Never places" is false in Claude Code confirm mode and unenforced on Codex until its
        # guard is verified (docs/launch/github-metadata.md). A listing says what is enforced.
        for label, text in self.all_descriptions().items():
            self.assertNotRegex(text.lower(), r"\bnever\s+place", label)

    def test_codex_listing_labels_placement_advised(self):
        # The safety table labels Codex "Verify": advised until a first-run test passes.
        text = self.load(cv.CODEX_MANIFEST)["description"]
        self.assertIn("advised, not enforced", text)
        self.assertIn("/hooks", text)

    def test_helper_listing_does_not_claim_the_evals_use_it(self):
        # The eval kit serves its mock as a standalone `robinhood` server and declares no plugin
        # (evalkit/README.md); the helper points at the live endpoint, so the listing must not send
        # people to install it for the evals, nor alongside `claude mcp add`.
        entry = [p for p in self.load(cv.MARKETPLACE)["plugins"] if p["name"] == "unofficial-rh-connector"][0]
        for text in (entry["description"], self.load(cv.HELPER_MANIFEST)["description"]):
            lowered = text.lower()
            self.assertNotIn("used by", lowered)
            if "eval" in lowered:
                self.assertRegex(lowered, r"evals (don't|do not) use it")
        self.assertIn("instead of claude mcp add, never as well", entry["description"])

    def codex_hooks(self):
        rel = self.load(cv.CODEX_MANIFEST).get("hooks")
        self.assertIsInstance(rel, str, "the Codex manifest must set `hooks`, or Codex loads hooks/hooks.json")
        self.assertTrue(rel.startswith("./") and ".." not in rel, rel)
        path = (REPO / rel).resolve()
        self.assertTrue(path.is_file(), rel)
        self.assertNotEqual(path, (REPO / "hooks" / "hooks.json").resolve())
        return path, json.loads(path.read_text(encoding="utf-8"))

    def test_codex_plugin_loads_only_the_order_guard(self):
        # Without an explicit `hooks` value Codex discovers hooks/hooks.json, the Claude Code set:
        # a session line claiming Claude Code enforcement, the audit log, and cancel "ask" prompts
        # that Codex parses but does not implement. The Codex plugin gets the money guard only.
        path, data = self.codex_hooks()
        self.assertEqual(set(data["hooks"]), {"PreToolUse"})
        entries = data["hooks"]["PreToolUse"]
        self.assertTrue(entries)
        for entry in entries:
            for hook in entry["hooks"]:
                self.assertEqual(hook, {"type": "command",
                                        "command": 'sh "${PLUGIN_ROOT}/hooks/guard.sh" money --format codex'})
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("<REPO>", text)
        self.assertNotIn("CLAUDE_PLUGIN", text)

    def test_codex_plugin_matcher_routes_every_money_tool_and_nothing_else(self):
        _path, data = self.codex_hooks()
        matchers = [re.compile(e["matcher"]) for e in data["hooks"]["PreToolUse"]]

        def routed(name):
            return any(m.search(name) for m in matchers)  # Codex matchers need not match in full

        classes = self.load("hooks/tool-classes.json")["tools"]
        money = {t["name"] for t in classes if t["class"] == "money"} | set(MONEY_TOOLS)
        servers = ("robinhood", "robinhood-trading", "plugin_unofficial-rh-connector_robinhood", "rh-sandbox",
                   "abcdefab-cdef-4abc-8def-abcdefabcdef")
        for tool in sorted(money):
            for server in servers:
                self.assertTrue(routed("mcp__{}__{}".format(server, tool)), (server, tool))
        for tool in sorted(t["name"] for t in classes if t["class"] != "money"):
            self.assertFalse(routed("mcp__robinhood-trading__" + tool), tool)
        for name in ("mcp__robinhood__transfer_funds", "mcp__robinhood__sell-crypto", "mcp__x__place-order"):
            self.assertTrue(routed(name), name)
        for name in ("mcp__db__execute_sql", "mcp__pdf__convert_to_pdf", "mcp__x__placeholder_text", "Bash"):
            self.assertFalse(routed(name), name)

    def test_codex_plugin_matcher_equals_the_manual_snippet(self):
        # One Codex guard, two install routes (plugin, or merged by hand for skills-only installs).
        _path, data = self.codex_hooks()
        manual = self.load("integrations/codex/hooks.json")["hooks"]["PreToolUse"]
        self.assertEqual([e["matcher"] for e in data["hooks"]["PreToolUse"]], [e["matcher"] for e in manual])

    @unittest.skipUnless(shutil.which("sh"), "needs a POSIX sh")
    def test_codex_plugin_hook_command_blocks_placement(self):
        _path, data = self.codex_hooks()
        command = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "PLUGIN_ROOT": str(REPO),
                   "ROBINHOOD_SKILLS_STATE": os.path.join(tmp, "state")}

            def run(tool):
                payload = json.dumps({"tool_name": "mcp__robinhood__" + tool, "tool_input": {}})
                return subprocess.run(["sh", "-c", command], input=payload, capture_output=True, text=True,
                                      env=env, cwd=tmp, timeout=30)

            for tool in MONEY_TOOLS:
                res = run(tool)
                self.assertEqual(res.returncode, 2, (tool, res.stderr))
                self.assertIn("Nothing was placed", res.stderr)
            res = run("get_equity_quotes")
            self.assertEqual((res.returncode, res.stdout, res.stderr), (0, "", ""))

    def test_context_files_stay_short(self):
        self.assertLessEqual(len((REPO / "GEMINI.md").read_text(encoding="utf-8").splitlines()), 30)
        self.assertLessEqual(len((REPO / "AGENTS.md").read_text(encoding="utf-8").splitlines()), 40)


if __name__ == "__main__":
    unittest.main()
