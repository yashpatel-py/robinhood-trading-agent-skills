"""Tests for tools/validate_skills.py (WP-B): every check in build spec G.3 has a passing and a failing case.

Each test builds a small skills/ tree in a temp directory: one spec-shaped skill (frontmatter per B.0.1,
body per B.0.3, a LICENSE.txt, a synced-rules stub and a script with a --selftest), then breaks one thing.
Stdlib unittest; run with python3 -m unittest discover -s tests.
"""

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "validate_skills.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vs = _load("validate_skills", TOOL)

NAME = "robinhood-demo"
DESCRIPTION = (
    "description: >-\n"
    "  Use when the user asks for the demo skill's job, even if they never say Robinhood.\n"
    "  Simulates only; never places orders. Not for other brokerages or finance questions\n"
    "  with no Robinhood connector.\n"
)
METADATA = (
    "metadata:\n"
    '  version: "2.0.0"\n'
    '  author: "yashpatel-py"\n'
    '  requires: "Robinhood Trading MCP connector (https://agent.robinhood.com/mcp/trading); '
    'US Robinhood Agentic account"\n'
    '  connector-tools-verified: "2026-09-22 (81 tools)"\n'
    '  unofficial: "Not affiliated with Robinhood Markets, Inc."\n'
    '  homepage: "https://github.com/yashpatel-py/robinhood-trading-agent-skills"\n'
)
BODY = (
    "# Demo skill\n"
    "*Unofficial — not affiliated with Robinhood.*\n"
    "\n"
    "<!-- BEGIN shared:invariants -->\n"
    "<!-- END shared:invariants -->\n"
    "\n"
    "## Before you start\n"
    "Read `references/connector-rules.md` before the first Robinhood call of a session.\n"
    "Run `python3 scripts/demo.py run < input.json`. The shared source lives in shared/scripts/rh_time.py\n"
    "and https://example.com/scripts/remote.py is a URL, so neither is a skill file.\n"
    "Fill in `assets/<section>.example.toml` placeholders (a template, not a path).\n"
)
SELFTEST_OK = "import sys\nif __name__ == '__main__':\n    sys.exit(0 if '--selftest' in sys.argv else 3)\n"
SELFTEST_FAIL = "import sys\nprint('selftest: case 2 failed')\nsys.exit(1)\n"


def skill_md(
    name=NAME,
    description=DESCRIPTION,
    metadata=METADATA,
    body=BODY,
    extra="",
    license_line="license: MIT (see LICENSE.txt)\n",
):
    return "---\nname: %s\n%s%s%s%s---\n%s" % (name, description, license_line, metadata, extra, body)


class SkillTree(object):
    """A temp repository root holding skills/<name>/ trees."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="validate-skills-")
        os.makedirs(os.path.join(self.root, "skills"))

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def add(self, name=NAME, text=None, license_txt=True, rules=True, script=SELFTEST_OK):
        d = os.path.join(self.root, "skills", name)
        os.makedirs(os.path.join(d, "references"))
        os.makedirs(os.path.join(d, "scripts"))
        self.write(name, "SKILL.md", text if text is not None else skill_md(name=name))
        if license_txt:
            self.write(name, "LICENSE.txt", "MIT License\n")
        if rules:
            self.write(
                name,
                "references/connector-rules.md",
                "<!-- synced from shared/connector-rules.md; do not edit -->\n# rules\n",
            )
        if script is not None:
            self.write(name, "scripts/demo.py", script)
        return d

    def write(self, name, rel, text, newline="\n"):
        path = os.path.join(self.root, "skills", name, *rel.split("/"))
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "w", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
        return path

    def run(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        code = vs.run(self.root, out=out, err=err, **kw)
        return code, out.getvalue().splitlines(), err.getvalue()


class ValidateSkillsTest(unittest.TestCase):
    def setUp(self):
        self.tree = SkillTree()
        self.addCleanup(self.tree.cleanup)

    def assertFails(self, needle, **kw):
        code, lines, _ = self.tree.run(**kw)
        self.assertEqual(code, 1, lines)
        self.assertTrue(any(needle in ln for ln in lines), "no failure mentioning %r in %r" % (needle, lines))
        return lines

    # -- passing baseline ---------------------------------------------------------------------------

    def test_valid_skill_passes(self):
        self.tree.add()
        code, lines, err = self.tree.run()
        self.assertEqual((code, lines), (0, []), err)
        self.assertIn("1 skill(s), 0 failure(s)", err)

    def test_failure_lines_are_path_line_message(self):
        self.tree.add(text=skill_md(name="wrong-name"))
        lines = self.assertFails("must equal the directory name")
        path, line, msg = lines[0].split(":", 2)
        self.assertEqual(path, "skills/%s/SKILL.md" % NAME)
        self.assertEqual(int(line), 2)
        self.assertTrue(msg.strip())

    # -- frontmatter keys ---------------------------------------------------------------------------

    def test_extra_frontmatter_key_fails(self):
        self.tree.add(text=skill_md(extra="compatibility: claude-code\n"))
        self.assertFails("remove compatibility")

    def test_missing_frontmatter_key_fails(self):
        self.tree.add(text=skill_md(license_line=""))
        self.assertFails("frontmatter is missing license")

    def test_empty_license_fails(self):
        self.tree.add(text=skill_md(license_line="license:\n"))
        self.assertFails("license must be a non-empty string")

    # -- name ---------------------------------------------------------------------------------------

    def test_name_must_equal_directory(self):
        self.tree.add(text=skill_md(name="robinhood-other"))
        self.assertFails("must equal the directory name")

    def test_name_pattern(self):
        self.tree.add(name="Bad_Name", text=skill_md(name="Bad_Name"))
        self.assertFails("lowercase letters and digits joined by single hyphens")

    def test_name_length(self):
        long_name = "a" + "-b" * 32  # 65 characters
        self.tree.add(name=long_name, text=skill_md(name=long_name))
        self.assertFails("65 characters (max 64)")

    # -- description --------------------------------------------------------------------------------

    def test_description_over_1024_fails(self):
        words = " ".join(["word"] * 210)  # folded into one long line
        desc = "description: >-\n  %s\n  Not for anything else.\n" % words
        self.tree.add(text=skill_md(description=desc))
        self.assertFails("(max 1024)")

    def test_description_at_limit_passes(self):
        filler = "x" * (1024 - len(" Not for tests."))
        desc = "description: >-\n  %s\n  Not for tests.\n" % filler
        self.tree.add(text=skill_md(description=desc))
        code, lines, _ = self.tree.run()
        self.assertEqual((code, lines), (0, []))

    def test_description_angle_brackets_fail(self):
        desc = "description: >-\n  Use for <ticker> questions. Not for other brokers.\n"
        self.tree.add(text=skill_md(description=desc))
        self.assertFails("must not contain '<' or '>'")

    def test_description_needs_not_for(self):
        desc = "description: >-\n  Use for Robinhood questions.\n"
        self.tree.add(text=skill_md(description=desc))
        self.assertFails("'Not for'")

    def test_description_must_be_folded_block(self):
        desc = "description: Use for Robinhood questions. Not for other brokers.\n"
        self.tree.add(text=skill_md(description=desc))
        self.assertFails("folded block scalar")

    # -- metadata -----------------------------------------------------------------------------------

    def test_metadata_missing_key_fails(self):
        meta = "\n".join(ln for ln in METADATA.splitlines() if "homepage" not in ln) + "\n"
        self.tree.add(text=skill_md(metadata=meta))
        self.assertFails("metadata is missing homepage")

    def test_metadata_unquoted_number_fails(self):
        self.tree.add(text=skill_md(metadata=METADATA.replace('version: "2.0.0"', "version: 2.0")))
        self.assertFails("metadata.version is read as a float")

    def test_metadata_unquoted_date_fails(self):
        meta = METADATA.replace(
            'connector-tools-verified: "2026-09-22 (81 tools)"', "connector-tools-verified: 2026-09-22"
        )
        self.tree.add(text=skill_md(metadata=meta))
        self.assertFails("read as a timestamp")

    def test_metadata_unquoted_three_part_version_is_a_string(self):
        self.tree.add(text=skill_md(metadata=METADATA.replace('version: "2.0.0"', "version: 2.0.0")))
        code, lines, _ = self.tree.run()
        self.assertEqual((code, lines), (0, []))

    def test_metadata_flow_map_is_accepted(self):
        meta = (
            'metadata: {version: "2.0.0", author: "yashpatel-py", requires: "connector", '
            'connector-tools-verified: "2026-09-22 (81 tools)", '
            'unofficial: "Not affiliated with Robinhood Markets, Inc.", '
            'homepage: "https://github.com/yashpatel-py/robinhood-trading-agent-skills"}\n'
        )
        self.tree.add(text=skill_md(metadata=meta))
        code, lines, _ = self.tree.run()
        self.assertEqual((code, lines), (0, []))

    def test_metadata_must_be_a_map(self):
        self.tree.add(text=skill_md(metadata='metadata: "2.0.0"\n'))
        self.assertFails("metadata must be a map")

    # -- layout -------------------------------------------------------------------------------------

    def test_skill_md_over_500_lines_fails(self):
        body = BODY + "".join("line %d\n" % i for i in range(500))
        self.tree.add(text=skill_md(body=body))
        self.assertFails("(max 500)")

    def test_invariants_markers_missing(self):
        body = BODY.replace("<!-- END shared:invariants -->\n", "")
        self.tree.add(text=skill_md(body=body))
        self.assertFails("invariants markers missing")

    def test_invariants_markers_twice(self):
        body = BODY + "<!-- BEGIN shared:invariants -->\n<!-- END shared:invariants -->\n"
        self.tree.add(text=skill_md(body=body))
        self.assertFails("exactly once")

    def test_invariants_markers_out_of_order(self):
        body = BODY.replace(
            "<!-- BEGIN shared:invariants -->\n<!-- END shared:invariants -->\n",
            "<!-- END shared:invariants -->\n<!-- BEGIN shared:invariants -->\n",
        )
        self.tree.add(text=skill_md(body=body))
        self.assertFails("END marker comes before")

    def test_synced_invariants_region_passes(self):
        with open(os.path.join(REPO, "shared", "invariants.md"), encoding="utf-8") as fh:
            block = fh.read()
        body = BODY.replace("<!-- BEGIN shared:invariants -->\n<!-- END shared:invariants -->\n", block)
        self.tree.add(text=skill_md(body=body))
        code, lines, _ = self.tree.run()
        self.assertEqual((code, lines), (0, []))

    def test_license_txt_missing(self):
        self.tree.add(license_txt=False)
        self.assertFails("LICENSE.txt is missing")

    def test_referenced_file_missing(self):
        self.tree.add(text=skill_md(body=BODY + "Then read `references/orders.md`.\n"))
        self.assertFails("references references/orders.md, which does not exist")

    def test_referenced_files_present_pass(self):
        d = self.tree.add(text=skill_md(body=BODY + "See references/orders.md, then assets/policy.example.toml.\n"))
        self.tree.write(NAME, "references/orders.md", "# orders\n")
        self.tree.write(NAME, "assets/policy.example.toml", "[policy]\n")
        self.assertTrue(os.path.isdir(d))
        code, lines, _ = self.tree.run()
        self.assertEqual((code, lines), (0, []))

    def test_missing_rules_reference_fails(self):
        self.tree.add(rules=False)
        self.assertFails("references/connector-rules.md, which does not exist")

    def test_unofficial_label_required(self):
        self.tree.add(text=skill_md(body=BODY.replace("*Unofficial — not affiliated with Robinhood.*\n", "")))
        self.assertFails("*Unofficial")

    def test_crlf_line_endings_fail(self):
        self.tree.add()
        self.tree.write(NAME, "SKILL.md", skill_md(), newline="\r\n")
        self.assertFails("LF line endings")

    def test_directory_without_skill_md(self):
        self.tree.add()
        os.makedirs(os.path.join(self.tree.root, "skills", "robinhood-empty"))
        self.assertFails("no SKILL.md in this skill directory")

    def test_hidden_and_underscore_dirs_are_ignored(self):
        self.tree.add()
        os.makedirs(os.path.join(self.tree.root, "skills", ".cache"))
        os.makedirs(os.path.join(self.tree.root, "skills", "_drafts"))
        code, lines, _ = self.tree.run()
        self.assertEqual((code, lines), (0, []))

    # -- scripts ------------------------------------------------------------------------------------

    def test_failing_selftest_fails(self):
        self.tree.add(script=SELFTEST_FAIL)
        lines = self.assertFails("--selftest exited 1")
        self.assertTrue(any("case 2 failed" in ln for ln in lines))

    def test_no_selftest_flag_skips_scripts(self):
        self.tree.add(script=SELFTEST_FAIL)
        code, lines, _ = self.tree.run(selftest=False)
        self.assertEqual((code, lines), (0, []))

    def test_selftest_timeout(self):
        self.tree.add(script="import time\ntime.sleep(5)\n")
        self.assertFails("did not finish within 1 s", timeout=1)

    # -- whole-run behavior -------------------------------------------------------------------------

    def test_no_skills_directory(self):
        shutil.rmtree(os.path.join(self.tree.root, "skills"))
        code, _, err = self.tree.run()
        self.assertEqual(code, 1)
        self.assertIn("no skills/ directory", err)

    def test_empty_skills_directory(self):
        code, _, err = self.tree.run()
        self.assertEqual(code, 1)
        self.assertIn("no skills found", err)

    def test_only_filter_and_unknown_skill(self):
        self.tree.add()
        self.tree.add(name="robinhood-broken", text=skill_md(name="robinhood-wrong"))
        code, lines, _ = self.tree.run(only=[NAME])
        self.assertEqual((code, lines), (0, []))
        code, _, err = self.tree.run(only=["robinhood-nope"])
        self.assertEqual(code, 2)
        self.assertIn("no such skill", err)

    def test_cli_exit_codes(self):
        self.tree.add()
        ok = subprocess.run(
            [sys.executable, TOOL, "--root", self.tree.root], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.tree.write(NAME, "SKILL.md", skill_md(name="robinhood-x"))
        bad = subprocess.run(
            [sys.executable, TOOL, "--root", self.tree.root], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.assertEqual(bad.returncode, 1)
        self.assertIn(b"skills/robinhood-demo/SKILL.md:2:", bad.stdout)


class FrontmatterParserTest(unittest.TestCase):
    def parse(self, fm, body="# t\n"):
        mapping, _, _ = vs.parse_frontmatter("---\n%s---\n%s" % (fm, body))
        return mapping.items

    def test_folded_block_joins_lines_and_keeps_paragraphs(self):
        items = self.parse("description: >-\n  one\n  two\n\n  three\n")
        self.assertEqual(items["description"].value, "one two\nthree")
        self.assertEqual(items["description"].style, "block>-")

    def test_folded_clip_and_keep(self):
        self.assertEqual(self.parse("d: >\n  a\n  b\n\n")["d"].value, "a b\n")
        self.assertEqual(self.parse("d: >+\n  a\n\n")["d"].value, "a\n\n")

    def test_literal_block(self):
        self.assertEqual(self.parse("d: |-\n  a\n  b\n")["d"].value, "a\nb")

    def test_more_indented_lines_keep_breaks(self):
        self.assertEqual(self.parse("d: >-\n  a\n    code\n  b\n")["d"].value, "a\n  code\nb")

    def test_quoted_scalars(self):
        items = self.parse("a: \"x \\\"y\\\" \\u00e9\"\nb: 'it''s'\nc: plain # comment\n")
        self.assertEqual(items["a"].value, 'x "y" \u00e9')
        self.assertEqual(items["b"].value, "it's")
        self.assertEqual(items["c"].value, "plain")
        self.assertEqual(items["c"].kind, "str")

    def test_scalar_kinds(self):
        items = self.parse("a: true\nb: 12\nc: 1.5\nd: ~\ne: 2026-09-22\nf: 2.0.0\ng: yes\n")
        kinds = dict((k, v.kind) for k, v in items.items())
        self.assertEqual(
            kinds, {"a": "bool", "b": "int", "c": "float", "d": "null", "e": "timestamp", "f": "str", "g": "bool"}
        )

    def test_nested_block_map(self):
        items = self.parse('metadata:\n  version: "2.0.0"\n  # a comment\n  author: me\nname: x\n')
        self.assertEqual(sorted(items["metadata"].items), ["author", "version"])
        self.assertEqual(items["name"].value, "x")

    def test_errors(self):
        cases = [
            ("a: 1\na: 2\n", "duplicate key"),
            ("a:\n  - x\n", "lists are not supported"),
            ('a: "open\n', "not closed"),
            ("\ta: 1\n", "tabs"),
            ("a: b: c\n", "quote it"),
            ("just text\n", "expected 'key: value'"),
            ("a:\n  b: 1\n c: 2\n", "inconsistent indentation"),
        ]
        for fm, needle in cases:
            with self.assertRaises(vs.YamlError) as ctx:
                self.parse(fm)
            self.assertIn(needle, ctx.exception.msg, fm)

    def test_frontmatter_must_open_and_close(self):
        with self.assertRaises(vs.YamlError):
            vs.parse_frontmatter("# no frontmatter\n")
        with self.assertRaises(vs.YamlError):
            vs.parse_frontmatter("---\nname: x\n")

    def test_body_position(self):
        _, body, first = vs.parse_frontmatter("---\nname: x\n---\n# Title\nline\n")
        self.assertEqual(first, 4)
        self.assertEqual(body[0], "# Title")


if __name__ == "__main__":
    unittest.main()
