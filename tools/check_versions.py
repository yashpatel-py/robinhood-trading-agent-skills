#!/usr/bin/env python3
"""Check that every version string in a release agrees.

Usage:
    python3 tools/check_versions.py [TAG] [--root DIR] [--json]

Why this exists: the kit ships through five channels at once (the Claude Code
plugin and marketplace, the connector helper plugin, Codex, Gemini CLI and
per-skill zips). Each reads its own manifest, and the Gemini gallery indexes a
release only when the manifest version equals the tag. One stale number means
a user installs a different build than the release notes describe, so CI fails
on any disagreement instead of trusting a checklist.

Sources compared (all must be equal to `.claude-plugin/plugin.json` "version"):
  - `.claude-plugin/marketplace.json`: every plugin entry that carries a version
    (the `robinhood-trading` and `unofficial-rh-connector` entries must)
  - `plugins/unofficial-rh-connector/.claude-plugin/plugin.json`
  - `.codex-plugin/plugin.json`
  - `gemini-extension.json`
  - the latest version heading in `CHANGELOG.md` (`## [x.y.z] ...`;
    `## [Unreleased]` is skipped)
  - `metadata.version` in every `skills/<name>/SKILL.md`
  - TAG, when given (`v2.0.0`, `2.0.0` or `refs/tags/v2.0.0`). On a tag, the
    CHANGELOG section for that version must also carry a date, not
    "Unreleased", because the release notes are cut from it.

Exit 0 when everything agrees, 1 otherwise. Stdlib only; Python 3.9+.
The frontmatter helpers here are also used by tools/build_release.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CORE_PLUGIN = "robinhood-trading"
HELPER_PLUGIN = "unofficial-rh-connector"

PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
MARKETPLACE = ".claude-plugin/marketplace.json"
HELPER_MANIFEST = "plugins/unofficial-rh-connector/.claude-plugin/plugin.json"
CODEX_MANIFEST = ".codex-plugin/plugin.json"
GEMINI_MANIFEST = "gemini-extension.json"
CHANGELOG = "CHANGELOG.md"
SKILLS_DIR = "skills"

SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
CHANGELOG_HEADING = re.compile(r"^##\s+\[([^\]]+)\](.*)$")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


# --------------------------------------------------------------------------
# Minimal YAML-frontmatter helpers (no PyYAML: CI and users may not have it).
# They cover the restricted shape every SKILL.md uses (spec B.0.1): top-level
# scalar keys, `>-`/`|` block scalars, and a flat `metadata:` string map in
# block or flow form.
# --------------------------------------------------------------------------

def read_frontmatter(text: str) -> Optional[List[str]]:
    """Return the lines between the opening and closing `---`, or None."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i]
    return None


def strip_body(text: str) -> str:
    """Return the markdown body after the frontmatter (text unchanged if none)."""
    lines = text.lstrip("﻿").splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1:]).strip("\n") + "\n"
    return text


def strip_comment(value: str) -> str:
    """Drop a trailing YAML comment (` # ...`) that is outside quotes."""
    quote = ""
    for i, ch in enumerate(value):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or value[i - 1].isspace()):
            return value[:i].rstrip()
    return value.rstrip()


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _block_scalar(lines: List[str], start: int, style: str) -> str:
    body: List[str] = []
    for sub in lines[start:]:
        if sub.strip() == "":
            body.append("")
            continue
        if not sub[0].isspace():
            break
        body.append(sub)
    while body and body[-1] == "":
        body.pop()
    indents = [len(b) - len(b.lstrip()) for b in body if b]
    cut = min(indents) if indents else 0
    body = [b[cut:] if b else "" for b in body]
    if style == "|":
        return "\n".join(body)
    paragraphs: List[str] = []
    current: List[str] = []
    for b in body:
        if b == "":
            paragraphs.append(" ".join(current))
            current = []
        else:
            current.append(b.strip())
    paragraphs.append(" ".join(current))
    return "\n".join(paragraphs)


def frontmatter_scalar(lines: List[str], key: str) -> Optional[str]:
    """Value of a top-level scalar key (plain, quoted, or `>`/`|` block)."""
    pattern = re.compile(r"^" + re.escape(key) + r":(?:\s+(.*))?$")
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if not m:
            continue
        rest = strip_comment((m.group(1) or "").strip())
        if rest[:1] in (">", "|"):
            return _block_scalar(lines, i + 1, rest[0])
        return unquote(rest)
    return None


def metadata_value(lines: List[str], key: str) -> Optional[str]:
    """Value of `metadata.<key>` in block or flow form, or None."""
    for i, line in enumerate(lines):
        m = re.match(r"^metadata:(?:\s+(.*))?$", line)
        if not m:
            continue
        rest = strip_comment((m.group(1) or "").strip())
        if rest.startswith("{"):
            fm = re.search(
                r"(?:^|[{,\s])" + re.escape(key) + r"\s*:\s*(\"[^\"]*\"|'[^']*'|[^,}\s]+)",
                rest,
            )
            return unquote(fm.group(1)) if fm else None
        if rest:
            return None
        child_indent: Optional[int] = None
        for sub in lines[i + 1:]:
            if not sub.strip() or sub.lstrip().startswith("#"):
                continue
            if not sub[0].isspace():
                break
            indent = len(sub) - len(sub.lstrip())
            if child_indent is None:
                child_indent = indent
            if indent != child_indent:
                continue
            sm = re.match(r"^\s+" + re.escape(key) + r"\s*:(?:\s+(.*))?$", sub)
            if sm:
                return unquote(strip_comment((sm.group(1) or "").strip()))
        return None
    return None


# --------------------------------------------------------------------------
# Version collection
# --------------------------------------------------------------------------

def _load_json(path: Path) -> Tuple[Optional[dict], Optional[str]]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError) as exc:
        return None, "unreadable JSON ({})".format(exc)
    if not isinstance(data, dict):
        return None, "top level is not a JSON object"
    return data, None


def changelog_latest(text: str) -> Tuple[Optional[str], Optional[str]]:
    """(version, rest-of-heading) of the first non-Unreleased `## [..]` heading."""
    for line in text.splitlines():
        m = CHANGELOG_HEADING.match(line.strip())
        if not m:
            continue
        label = m.group(1).strip()
        if label.lower() == "unreleased":
            continue
        return label, m.group(2).strip()
    return None, None


def normalize_tag(tag: str) -> str:
    tag = tag.strip()
    if tag.startswith("refs/tags/"):
        tag = tag[len("refs/tags/"):]
    if tag[:1] in ("v", "V"):
        tag = tag[1:]
    return tag


def collect(root: Path) -> Tuple[List[Dict[str, str]], List[str], Optional[str]]:
    """Return (sources, errors, changelog_heading_rest).

    sources: [{"source": <label>, "version": <string>}] for every version found.
    errors: problems that make a version unreadable or a required entry absent.
    """
    sources: List[Dict[str, str]] = []
    errors: List[str] = []

    def add(label: str, value: object) -> None:
        if isinstance(value, str) and value.strip():
            sources.append({"source": label, "version": value.strip()})
        else:
            errors.append("{}: no version string".format(label))

    for rel in (PLUGIN_MANIFEST, HELPER_MANIFEST, CODEX_MANIFEST, GEMINI_MANIFEST):
        data, err = _load_json(root / rel)
        if err:
            errors.append("{}: {}".format(rel, err))
        else:
            add(rel, data.get("version"))

    data, err = _load_json(root / MARKETPLACE)
    if err:
        errors.append("{}: {}".format(MARKETPLACE, err))
    else:
        plugins = data.get("plugins")
        if not isinstance(plugins, list):
            errors.append("{}: no plugins list".format(MARKETPLACE))
            plugins = []
        seen = set()
        for entry in plugins:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            seen.add(name)
            label = "{} plugins[{}]".format(MARKETPLACE, name)
            if name in (CORE_PLUGIN, HELPER_PLUGIN) or "version" in entry:
                add(label, entry.get("version"))
        for required in (CORE_PLUGIN, HELPER_PLUGIN):
            if required not in seen:
                errors.append("{}: no plugin entry named {}".format(MARKETPLACE, required))

    heading_rest: Optional[str] = None
    try:
        text = (root / CHANGELOG).read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append("{}: missing".format(CHANGELOG))
    except OSError as exc:
        errors.append("{}: unreadable ({})".format(CHANGELOG, exc))
    else:
        version, heading_rest = changelog_latest(text)
        if version is None:
            errors.append("{}: no version heading like '## [2.0.0] - 2026-09-27'".format(CHANGELOG))
        else:
            sources.append({"source": CHANGELOG + " (latest heading)", "version": version})

    skills_root = root / SKILLS_DIR
    skill_files = sorted(skills_root.glob("*/SKILL.md")) if skills_root.is_dir() else []
    if not skill_files:
        errors.append("{}/: no skills found (expected skills/<name>/SKILL.md)".format(SKILLS_DIR))
    for path in skill_files:
        label = "{}/{}/SKILL.md metadata.version".format(SKILLS_DIR, path.parent.name)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append("{}: unreadable ({})".format(label, exc))
            continue
        fm = read_frontmatter(text)
        if fm is None:
            errors.append("{}: no YAML frontmatter".format(label))
            continue
        add(label, metadata_value(fm, "version"))

    return sources, errors, heading_rest


def check(root: Path, tag: Optional[str] = None) -> Dict[str, object]:
    sources, errors, heading_rest = collect(root)
    reference: Optional[str] = None
    for item in sources:
        if item["source"] == PLUGIN_MANIFEST:
            reference = item["version"]
    if reference is not None and not SEMVER.match(reference):
        errors.append("{}: '{}' is not a SemVer version (x.y.z)".format(PLUGIN_MANIFEST, reference))
    if reference is not None:
        for item in sources:
            if item["version"] != reference:
                errors.append(
                    "{}: {} (expected {}, from {})".format(
                        item["source"], item["version"], reference, PLUGIN_MANIFEST
                    )
                )
    if tag:
        tag_version = normalize_tag(tag)
        sources.append({"source": "tag " + tag, "version": tag_version})
        if not SEMVER.match(tag_version):
            errors.append("tag {}: not a version tag like v2.0.0".format(tag))
        elif reference is not None and tag_version != reference:
            errors.append("tag {}: {} (expected {}, from {})".format(tag, tag_version, reference, PLUGIN_MANIFEST))
        if heading_rest is not None and (
            "unreleased" in heading_rest.lower() or not ISO_DATE.search(heading_rest)
        ):
            errors.append(
                "{}: the latest version heading is not dated (found '{}'); set the release "
                "date (## [x.y.z] - YYYY-MM-DD) before tagging".format(CHANGELOG, heading_rest or "no date")
            )
    return {"ok": not errors, "version": reference, "sources": sources, "errors": errors}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check that all release version strings agree.")
    parser.add_argument("tag", nargs="?", default="", help="release tag, e.g. v2.0.0 (optional)")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                        help="repository root (default: the parent of tools/)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    result = check(Path(args.root), args.tag or None)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for item in result["sources"]:
            print("  {:<12} {}".format(item["version"], item["source"]))
        if result["ok"]:
            print("check_versions: OK, {} sources agree on {}".format(len(result["sources"]), result["version"]))
        else:
            for err in result["errors"]:
                print("check_versions: ERROR " + err, file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
