"""Tests for tools/build_release.py and tools/build_variant.py.

Run: python3 -m unittest discover -s tests -v
"""

import calendar
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import build_release as br  # noqa: E402
import build_variant as bv  # noqa: E402

ALPHA_SKILL = """---
name: alpha
description: >-
  Use when the fixture needs a skill. Not for real use,
  and never for placing orders.
license: MIT (see LICENSE.txt)
metadata:
  version: "2.0.0"
  author: "yashpatel-py"
---

# Alpha

*Unofficial — not affiliated with Robinhood.*

Read `references/connector-rules.md` first.
"""

BETA_SKILL = """---
name: beta
description: Use for the second fixture skill. Not for anything else.
license: MIT (see LICENSE.txt)
metadata:
  version: "2.0.0"
---

# Beta
"""

LICENSE_TEXT = "MIT License\n\nCopyright (c) 2026 Fixture Owner\n"


def run(fn, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fn(argv)
    return code, out.getvalue(), err.getvalue()


def write(path, text, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(str(path), mode)


def make_repo(root):
    manifest = {
        "name": "robinhood-trading",
        "version": "2.0.0",
        "description": "Unofficial fixture plugin.",
        "userConfig": {"order_mode": {"type": "string", "default": "simulate_only"}},
    }
    write(root / ".claude-plugin" / "plugin.json", json.dumps(manifest, indent=2))
    write(root / "LICENSE", LICENSE_TEXT)
    write(root / "hooks" / "hooks.json", '{"hooks": {}}')
    write(root / "hooks" / "guard.sh", "#!/bin/sh\nexit 2\n", 0o755)
    alpha = root / "skills" / "alpha"
    write(alpha / "SKILL.md", ALPHA_SKILL)
    write(alpha / "LICENSE.txt", LICENSE_TEXT)
    write(alpha / "references" / "zeta.md", "# Zeta\nzeta body\n")
    write(alpha / "references" / "formulas.md", "# Formulas\nformulas body\n")
    write(alpha / "references" / "connector-rules.md", "# Connector rules\nrules body\n")
    write(alpha / "references" / "confirm-mode.md", "# Confirm mode\nplace_* instructions\n")
    write(alpha / "references" / "tables.json", '{"k": "v"}\n')
    write(alpha / "scripts" / "calc.py", "#!/usr/bin/env python3\nprint('ok')\n", 0o755)
    write(alpha / "scripts" / "__pycache__" / "calc.cpython-313.pyc", "junk")
    write(alpha / ".DS_Store", "junk")
    write(alpha / "assets" / "policy.example.toml", '[policy]\nmax = "UNSET"\n# ```` fence test\n')
    beta = root / "skills" / "beta"
    write(beta / "SKILL.md", BETA_SKILL)
    write(root / "evals" / "case-1" / "prompt.md", "---\nmax_turns: 4\n---\nHello\n")
    write(root / "evals" / "mocks" / "robinhood" / "get_accounts.md", "{}\n")
    write(root / "evals" / "results" / "old-run.json", "{}\n")


class ReleaseTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="build_release-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)
        make_repo(self.root)
        self.dist = self.root / "dist"

    def build(self, *extra):
        return run(br.main, ["--root", str(self.root)] + list(extra))

    def build_ok(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        return out


class BuildReleaseTests(ReleaseTestCase):
    def test_writes_zips_bundles_and_sums(self):
        out = self.build_ok()
        self.assertIn("version 2.0.0, 2 skills", out)
        for rel in ("zips/alpha.zip", "zips/beta.zip", "chatgpt/alpha.md", "chatgpt/beta.md", "SHA256SUMS"):
            self.assertTrue((self.dist / rel).is_file(), rel)

    def test_zip_has_folder_at_root_and_skips_junk_and_confirm_mode(self):
        self.build_ok()
        with zipfile.ZipFile(str(self.dist / "zips" / "alpha.zip")) as zf:
            names = zf.namelist()
        self.assertEqual(names, sorted(names))
        self.assertEqual(names, [
            "alpha/LICENSE.txt",
            "alpha/SKILL.md",
            "alpha/assets/policy.example.toml",
            "alpha/references/connector-rules.md",
            "alpha/references/formulas.md",
            "alpha/references/tables.json",
            "alpha/references/zeta.md",
            "alpha/scripts/calc.py",
        ])

    def test_zip_content_is_verbatim(self):
        self.build_ok()
        with zipfile.ZipFile(str(self.dist / "zips" / "alpha.zip")) as zf:
            self.assertEqual(zf.read("alpha/SKILL.md").decode("utf-8"), ALPHA_SKILL)

    def test_zip_permissions_and_timestamps_are_fixed(self):
        self.build_ok()
        with zipfile.ZipFile(str(self.dist / "zips" / "alpha.zip")) as zf:
            script = zf.getinfo("alpha/scripts/calc.py")
            doc = zf.getinfo("alpha/SKILL.md")
        self.assertEqual(stat.S_IMODE(script.external_attr >> 16), 0o755)
        self.assertEqual(stat.S_IMODE(doc.external_attr >> 16), 0o644)
        self.assertEqual(doc.date_time, (1980, 1, 1, 0, 0, 0))

    def test_builds_are_byte_reproducible(self):
        self.build_ok()
        first = (self.dist / "SHA256SUMS").read_bytes()
        os.utime(str(self.root / "skills" / "alpha" / "SKILL.md"), (1, 1))
        self.build_ok()
        self.assertEqual((self.dist / "SHA256SUMS").read_bytes(), first)

    def test_source_date_epoch_sets_timestamps(self):
        old = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = str(calendar.timegm((2026, 9, 21, 14, 13, 21)))
        try:
            self.build_ok()
        finally:
            if old is None:
                del os.environ["SOURCE_DATE_EPOCH"]
            else:
                os.environ["SOURCE_DATE_EPOCH"] = old
        with zipfile.ZipFile(str(self.dist / "zips" / "beta.zip")) as zf:
            self.assertEqual(zf.getinfo("beta/SKILL.md").date_time, (2026, 9, 21, 14, 13, 20))

    def test_sha256sums_match_files(self):
        self.build_ok()
        lines = (self.dist / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        paths = [line.split("  ", 1)[1] for line in lines]
        self.assertEqual(paths, ["chatgpt/alpha.md", "chatgpt/beta.md", "zips/alpha.zip", "zips/beta.zip"])
        for line in lines:
            digest, rel = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((self.dist / rel).read_bytes()).hexdigest(), digest)

    def test_missing_license_falls_back_to_repo_license(self):
        self.build_ok()
        with zipfile.ZipFile(str(self.dist / "zips" / "beta.zip")) as zf:
            self.assertEqual(zf.read("beta/LICENSE.txt").decode("utf-8"), LICENSE_TEXT)
        self.assertIn("Copyright (c) 2026 Fixture Owner", (self.dist / "chatgpt" / "beta.md").read_text("utf-8"))

    def test_dry_run_writes_nothing(self):
        code, out, err = self.build("--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("dry run, nothing written", out)
        self.assertIn("zips/alpha.zip", out)
        self.assertFalse(self.dist.exists())

    def test_custom_out_directory(self):
        target = self.root / "elsewhere"
        code, _, err = self.build("--out", str(target))
        self.assertEqual(code, 0, err)
        self.assertTrue((target / "zips" / "alpha.zip").is_file())
        self.assertFalse(self.dist.exists())

    def test_rebuild_removes_stale_outputs_and_keeps_variants(self):
        write(self.dist / "zips" / "removed-skill.zip", "old")
        write(self.dist / "chatgpt" / "removed-skill.md", "old")
        write(self.dist / "variants" / "skills-only" / "keep.txt", "keep")
        self.build_ok()
        self.assertFalse((self.dist / "zips" / "removed-skill.zip").exists())
        self.assertFalse((self.dist / "chatgpt" / "removed-skill.md").exists())
        self.assertTrue((self.dist / "variants" / "skills-only" / "keep.txt").exists())
        self.assertNotIn("removed-skill", (self.dist / "SHA256SUMS").read_text("utf-8"))

    def test_refuses_to_delete_foreign_files(self):
        write(self.dist / "zips" / "notes.txt", "mine")
        code, _, err = self.build()
        self.assertEqual(code, 1)
        self.assertIn("did not write (notes.txt)", err)
        self.assertTrue((self.dist / "zips" / "notes.txt").exists())


class ChatGptBundleTests(ReleaseTestCase):
    def setUp(self):
        super().setUp()
        self.build_ok()
        self.alpha = (self.dist / "chatgpt" / "alpha.md").read_text(encoding="utf-8")

    def test_first_line_is_advised_only(self):
        for name in ("alpha", "beta"):
            first = (self.dist / "chatgpt" / (name + ".md")).read_text("utf-8").splitlines()[0]
            self.assertTrue(first.startswith("**Advised only; your client does not enforce this.**"), first)

    def test_frontmatter_removed_and_description_kept(self):
        self.assertNotIn("\nname: alpha", self.alpha)
        self.assertNotIn("metadata:", self.alpha)
        self.assertIn("**When to use it:** Use when the fixture needs a skill. Not for real use, and never for "
                      "placing orders.", self.alpha)
        self.assertIn("# Alpha\n\n*Unofficial — not affiliated with Robinhood.*", self.alpha)
        self.assertIn("version 2.0.0", self.alpha)

    def test_reference_order_rules_first_formulas_last(self):
        positions = [self.alpha.index("## Bundled file: `references/{}`".format(n))
                     for n in ("connector-rules.md", "tables.json", "zeta.md", "formulas.md")]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(self.alpha.index("# Alpha"), positions[0])
        self.assertIn("rules body", self.alpha)
        self.assertIn("zeta body", self.alpha)

    def test_confirm_mode_and_scripts_are_not_bundled(self):
        self.assertNotIn("confirm-mode.md", self.alpha)
        self.assertNotIn("place_* instructions", self.alpha)
        self.assertNotIn("print('ok')", self.alpha)
        self.assertIn("simulate-only", self.alpha)

    def test_assets_and_non_markdown_references_are_fenced(self):
        self.assertIn("## Bundled file: `assets/policy.example.toml`\n\n`````toml\n[policy]", self.alpha)
        self.assertIn('## Bundled file: `references/tables.json`\n\n```json\n{"k": "v"}\n```', self.alpha)

    def test_license_is_appended(self):
        self.assertTrue(self.alpha.rstrip().endswith("```"))
        self.assertIn("## Bundled file: `LICENSE.txt`\n\n```text\nMIT License", self.alpha)


class BuildReleaseErrorTests(ReleaseTestCase):
    def assertBuildFails(self, fragment):
        code, _, err = self.build("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn(fragment, err)

    def test_symlink_is_refused(self):
        link = self.root / "skills" / "alpha" / "references" / "outside.md"
        try:
            os.symlink(str(self.root / "LICENSE"), str(link))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertBuildFails("symlink references/outside.md")

    def test_directory_without_skill_md(self):
        (self.root / "skills" / "stray").mkdir()
        self.assertBuildFails("skills/stray: no SKILL.md")

    def test_name_must_match_directory(self):
        write(self.root / "skills" / "beta" / "SKILL.md", BETA_SKILL.replace("name: beta", "name: gamma"))
        self.assertBuildFails("name 'gamma' does not match the directory")

    def test_skill_version_must_match_plugin(self):
        write(self.root / "skills" / "beta" / "SKILL.md", BETA_SKILL.replace('"2.0.0"', '"1.1.0"'))
        self.assertBuildFails("metadata.version 1.1.0 does not match plugin version 2.0.0")

    def test_no_skills(self):
        shutil.rmtree(str(self.root / "skills"))
        self.assertBuildFails("skills/: missing")

    def test_empty_skills_directory(self):
        shutil.rmtree(str(self.root / "skills"))
        (self.root / "skills").mkdir()
        self.assertBuildFails("no skills found")

    def test_missing_plugin_manifest(self):
        (self.root / ".claude-plugin" / "plugin.json").unlink()
        self.assertBuildFails(".claude-plugin/plugin.json: missing")


class BuildVariantTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="build_variant-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)
        make_repo(self.root)
        self.out = self.root / "dist" / "variants" / "skills-only"

    def build(self, *extra):
        return run(bv.main, ["skills-only", "--root", str(self.root)] + list(extra))

    def build_ok(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        return out

    def test_same_skills_no_hooks(self):
        out = self.build_ok()
        self.assertIn("2 skills", out)
        self.assertFalse((self.out / "hooks").exists())
        for rel in ("skills/alpha/SKILL.md", "skills/alpha/references/confirm-mode.md",
                    "skills/alpha/scripts/calc.py", "skills/beta/SKILL.md", "LICENSE"):
            self.assertTrue((self.out / rel).is_file(), rel)
        self.assertEqual((self.out / "skills" / "alpha" / "SKILL.md").read_text("utf-8"), ALPHA_SKILL)
        self.assertFalse((self.out / "skills" / "alpha" / ".DS_Store").exists())
        self.assertFalse((self.out / "skills" / "alpha" / "scripts" / "__pycache__").exists())

    def test_manifest_keeps_identity_and_drops_hook_config(self):
        self.build_ok()
        manifest = json.loads((self.out / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        self.assertEqual(manifest["name"], "robinhood-trading")
        self.assertEqual(manifest["version"], "2.0.0")
        self.assertNotIn("userConfig", manifest)
        self.assertNotIn("hooks", manifest)
        self.assertTrue(manifest["description"].startswith("EVAL ARM, not for installation"))
        self.assertIn("no order guard", manifest["description"])
        self.assertNotIn("hook blocks", manifest["description"])

    def test_evals_copied_without_results(self):
        out = self.build_ok()
        self.assertTrue((self.out / "evals" / "case-1" / "prompt.md").is_file())
        self.assertTrue((self.out / "evals" / "mocks" / "robinhood" / "get_accounts.md").is_file())
        self.assertFalse((self.out / "evals" / "results").exists())
        self.assertIn("2 eval files", out)

    def test_build_info(self):
        self.build_ok()
        info = json.loads((self.out / "BUILD-INFO.json").read_text("utf-8"))
        self.assertEqual(info["variant"], "skills-only")
        self.assertIs(info["hooks"], False)
        self.assertEqual(info["skills"], ["alpha", "beta"])

    def test_rebuild_replaces_previous_output(self):
        self.build_ok()
        write(self.out / "skills" / "leftover" / "SKILL.md", "old")
        shutil.rmtree(str(self.root / "skills" / "beta"))
        self.build_ok()
        self.assertFalse((self.out / "skills" / "leftover").exists())
        self.assertFalse((self.out / "skills" / "beta").exists())

    def test_refuses_foreign_output_directory(self):
        target = self.root / "precious"
        write(target / "thesis.md", "do not delete")
        code, _, err = run(bv.main, ["skills-only", "--root", str(self.root), "--out", str(target)])
        self.assertEqual(code, 1)
        self.assertIn("refusing to delete it", err)
        self.assertTrue((target / "thesis.md").exists())

    def test_symlink_in_skills_is_refused_and_output_cleaned(self):
        link = self.root / "skills" / "beta" / "linked.md"
        try:
            os.symlink(str(self.root / "LICENSE"), str(link))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        code, _, err = self.build()
        self.assertEqual(code, 1)
        self.assertIn("symlink beta/linked.md", err)
        self.assertFalse(self.out.exists())

    def test_no_skills(self):
        shutil.rmtree(str(self.root / "skills"))
        code, _, err = self.build()
        self.assertEqual(code, 1)
        self.assertIn("no skills found", err)

    def test_only_skills_only_variant_exists(self):
        # O2: one release, no staging/, so the old "next" variant is gone.
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                bv.main(["next", "--root", str(self.root)])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
