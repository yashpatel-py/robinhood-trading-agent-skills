#!/usr/bin/env python3
"""Build plugin variants for the eval arms.

Usage:
    python3 tools/build_variant.py skills-only [--root DIR] [--out DIR]

`skills-only` writes <root>/dist/variants/skills-only/ (or --out): the same
plugin name, version and skills as the repository root, with no hooks. The
eval workflow runs every case against both the repository root (WITH: skills
plus the order guard and audit log) and this variant (SKILLS-ONLY), so the
scorecard can show what the skills do on their own and what only the hook
enforces. That split is the honest answer to "is the guard doing the work, or
the instructions?", and it is why this variant must differ from the root by the
hooks and nothing else:

  - `.claude-plugin/plugin.json`: the root manifest with `hooks`, `mcpServers`
    and `userConfig` removed (every userConfig key is read only by hooks) and
    the description replaced by an eval-arm notice (the root one advertises the
    hook). `name` and `version` are unchanged, so skill names and grader
    expectations match across arms.
  - `skills/`: a real copy, synced files included; symlinks are refused.
  - `evals/`: copied without `evals/results/`, because `claude plugin eval`
    reads cases and mocks from the target plugin's own `evals/` directory.
  - `LICENSE` and `BUILD-INFO.json` (what this directory is and where it came from).
  - never `hooks/`, `.mcp.json`, `commands/` or `agents/`.

The output directory is replaced on each run, but only if it is empty or holds
an earlier build of the same variant (checked through BUILD-INFO.json).

Stdlib only; Python 3.9+. No network access.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import List, Optional

VARIANTS = ("skills-only",)
INFO_FILE = "BUILD-INFO.json"
GENERATOR = "tools/build_variant.py"
JUNK_DIRS = frozenset({"__pycache__"})
JUNK_SUFFIXES = (".pyc", ".pyo", "~", ".orig", ".rej", ".swp")
# Root-level plugin components that carry behavior other than skills. The
# skills-only arm must contain none of them.
EXCLUDED_COMPONENTS = ("hooks", ".mcp.json", "commands", "agents", "output-styles")


class VariantError(Exception):
    pass


def _copy_tree(src: Path, dest: Path, skip_top: frozenset = frozenset()) -> int:
    """Copy regular files from src to dest, skipping junk and refusing symlinks."""
    count = 0
    problems: List[str] = []
    for dirpath, dirnames, filenames in os.walk(str(src), followlinks=False):
        base = Path(dirpath)
        rel_base = base.relative_to(src)
        for d in sorted(dirnames):
            full = base / d
            rel = (rel_base / d).as_posix()
            if full.is_symlink():
                problems.append("symlinked directory {}".format(rel))
                dirnames.remove(d)
            elif d.startswith(".") or d in JUNK_DIRS or (rel_base == Path(".") and d in skip_top):
                dirnames.remove(d)
        for name in sorted(filenames):
            full = base / name
            rel = rel_base / name
            if name.startswith(".") or name.endswith(JUNK_SUFFIXES):
                continue
            if full.is_symlink():
                problems.append("symlink {}".format(rel.as_posix()))
                continue
            if not stat.S_ISREG(full.lstat().st_mode):
                problems.append("not a regular file: {}".format(rel.as_posix()))
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(full), str(target))
            count += 1
    if problems:
        raise VariantError("{}: {}".format(src.name, "; ".join(sorted(problems))))
    return count


def _prepare_out(out: Path, variant: str) -> None:
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise VariantError("{} exists and is not a directory; refusing to replace it".format(out))
    if not out.exists():
        return
    if not any(out.iterdir()):
        out.rmdir()
        return
    info_path = out / INFO_FILE
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = None
    ours = isinstance(info, dict) and info.get("variant") == variant and info.get("generated_by") == GENERATOR
    if not ours:
        raise VariantError(
            "{} is not empty and is not an earlier '{}' build (no matching {}); refusing to delete it".format(
                out, variant, INFO_FILE))
    shutil.rmtree(str(out))


def build_skills_only(root: Path, out: Path) -> dict:
    manifest_path = root / ".claude-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VariantError("{}: {}".format(manifest_path, exc)) from exc
    if not isinstance(manifest, dict) or not manifest.get("name"):
        raise VariantError("{}: not a plugin manifest with a name".format(manifest_path))
    skills_src = root / "skills"
    skill_names = sorted(p.parent.name for p in skills_src.glob("*/SKILL.md")) if skills_src.is_dir() else []
    if not skill_names:
        raise VariantError("skills/: no skills found (expected skills/<name>/SKILL.md)")

    _prepare_out(out, "skills-only")
    try:
        variant_manifest = {k: v for k, v in manifest.items() if k not in ("hooks", "userConfig", "mcpServers")}
        # The root description advertises the hook; this arm has none, so it gets its own text.
        variant_manifest["description"] = (
            "EVAL ARM, not for installation: the {} skills with no hooks, so no order guard and no audit "
            "log. Generated by tools/build_variant.py for the SKILLS-ONLY eval arm.".format(manifest["name"])
        )
        (out / ".claude-plugin").mkdir(parents=True)
        (out / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(variant_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        skill_file_count = _copy_tree(skills_src, out / "skills")
        eval_file_count = 0
        if (root / "evals").is_dir():
            eval_file_count = _copy_tree(root / "evals", out / "evals", skip_top=frozenset({"results"}))
        if (root / "LICENSE").is_file():
            shutil.copy2(str(root / "LICENSE"), str(out / "LICENSE"))

        present = [c for c in EXCLUDED_COMPONENTS if (out / c).exists()]
        if present:
            raise VariantError("skills-only output contains {}".format(", ".join(present)))

        info = {
            "variant": "skills-only",
            "generated_by": GENERATOR,
            "plugin": manifest.get("name"),
            "version": manifest.get("version"),
            "hooks": False,
            "skills": skill_names,
            "skill_files": skill_file_count,
            "eval_files": eval_file_count,
            "note": "Generated eval arm. Same skills as the repository root, without hooks/. Rebuild with "
                    "python3 tools/build_variant.py skills-only; do not edit or install.",
        }
        (out / INFO_FILE).write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(str(out), ignore_errors=True)
        raise
    return info


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build plugin variants for the eval arms.")
    parser.add_argument("variant", choices=VARIANTS,
                        help="skills-only: the plugin's skills (and evals) without hooks")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                        help="repository root (default: the parent of tools/)")
    parser.add_argument("--out", default=None, help="output directory (default: <root>/dist/variants/<variant>)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    out = Path(args.out).resolve() if args.out else root / "dist" / "variants" / args.variant
    try:
        info = build_skills_only(root, out)
    except VariantError as exc:
        print("build_variant: ERROR {}".format(exc), file=sys.stderr)
        return 1
    print("build_variant: {} -> {} ({} skills, {} skill files, {} eval files, no hooks)".format(
        args.variant, out, len(info["skills"]), info["skill_files"], info["eval_files"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
