#!/usr/bin/env python3
"""sync_shared.py - copy the kit's shared sources into every skill and hook that needs them.

Unofficial; not affiliated with Robinhood Markets, Inc. Stdlib only, Python 3.9+, no network access.

Every SKILL.md must work when copied alone, so each skill carries its own copy of the shared connector
rules and scripts. Copies drift unless one tool owns them; this is that tool. shared/manifest.json says
what goes where:

  license     LICENSE -> skills/*/LICENSE.txt (byte copy)
  files       shared sources -> skill and hook targets. Markdown copies get a first line
              `<!-- synced from <source>; do not edit -->`; Python copies get
              `# synced from <source>; do not edit` (after the shebang and any coding line, so the
              script still runs). Other file types are byte copies.
  generated   connector/tool-classes.json -> hooks/tool-classes.json (copy), hooks/known-tools.txt
              (bare names, one per line, sorted) and skills/robinhood-trading/scripts/tool_inventory.json
              (names and classes)
  invariants  shared/invariants.md replaces the region from the `<!-- BEGIN shared:invariants` line to
              the `<!-- END shared:invariants -->` line inside every skills/*/SKILL.md. Skill authors
              write the two marker lines; this tool owns what goes between them.

Target patterns: `skills/*/…` expands to every directory under skills/; a target ending in `/` means
"that directory, same filename as the source"; a target naming a skill that does not exist is an error
(a manifest that points at a missing skill is drift, not something to paper over).

A file under skills/ or hooks/lib/ that carries a sync header but is no longer a manifest target is an
orphan: the tool reports it (and exits 1) instead of deleting it.

Usage:
  python3 tools/sync_shared.py            write every target that differs
  python3 tools/sync_shared.py --check    write nothing; print a unified diff per stale target, exit 1
  options: --root DIR (repository root), --manifest PATH (default shared/manifest.json), --quiet

Exit 0 when everything is (or now is) in sync, 1 when a target is stale (--check) or any error was
found, 2 when the manifest itself is missing or malformed.
"""

import argparse
import difflib
import json
import os
import re
import stat
import sys

DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_REL = "shared/manifest.json"
SKILLS_DIR = "skills"
GENERATED_KINDS = ("copy", "names", "names_classes")
MD_HEADER = "<!-- synced from %s; do not edit -->\n"
PY_HEADER = "# synced from %s; do not edit\n"
HEADER_RE = re.compile(r"^(?:<!-- synced from \S+; do not edit -->|# synced from \S+; do not edit)\s*$")
CODING_RE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
ORPHAN_SCAN = ((SKILLS_DIR, True), ("hooks/lib", False))  # (directory, recursive)


class ManifestError(Exception):
    """The manifest is missing or malformed (exit 2)."""


class Plan(object):
    """Everything the manifest asks for: file targets, invariant regions, and the errors found."""

    def __init__(self):
        self.targets = []  # [(target_rel, expected_bytes, source_rel, mode or None)]
        self.regions = []  # [(target_rel, expected_text, current_text)]
        self.errors = []  # ["path: message"]
        self._owner = {}  # target_rel -> source_rel, to catch two sources writing one file

    def add_target(self, target, data, source, mode=None):
        prev = self._owner.get(target)
        if prev is not None:
            self.errors.append("%s: listed twice in the manifest (from %s and %s)" % (target, prev, source))
            return
        self._owner[target] = source
        self.targets.append((target, data, source, mode))

    def target_names(self):
        return set(self._owner)


# ---------------------------------------------------------------------------------------------------
# Paths and rendering
# ---------------------------------------------------------------------------------------------------


def _abs(root, rel):
    return os.path.join(root, *rel.split("/"))


def skill_dirs(root):
    """Names of the skill directories under skills/ (hidden and underscore-prefixed entries skipped)."""
    base = _abs(root, SKILLS_DIR)
    if not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base) if not d.startswith((".", "_")) and os.path.isdir(os.path.join(base, d)))


def expand_target(root, pattern, source_rel):
    """Expand one manifest target into repo-relative paths. Raises ValueError with a message."""
    if not isinstance(pattern, str) or not pattern or pattern.startswith("/") or ".." in pattern.split("/"):
        raise ValueError("invalid target %r" % (pattern,))
    if pattern.startswith(SKILLS_DIR + "/*/"):
        rest = pattern[len(SKILLS_DIR) + 3 :]
        if "*" in rest:
            raise ValueError("only one '*' (the skill directory) is supported: %s" % pattern)
        paths = ["%s/%s/%s" % (SKILLS_DIR, d, rest) for d in skill_dirs(root)]
    else:
        if "*" in pattern:
            raise ValueError("'*' is only supported as skills/*/…: %s" % pattern)
        parts = pattern.split("/")
        if parts[0] == SKILLS_DIR:
            if len(parts) < 3 or not parts[1]:
                raise ValueError("a skills/ target must name a file or directory inside a skill: %s" % pattern)
            if not os.path.isdir(_abs(root, SKILLS_DIR + "/" + parts[1])):
                raise ValueError("target skill directory %s/%s does not exist" % (SKILLS_DIR, parts[1]))
        paths = [pattern]
    base = source_rel.rsplit("/", 1)[-1]
    return [p + base if p.endswith("/") else p for p in paths]


def header_for(source_rel, target_rel):
    ext = os.path.splitext(target_rel)[1].lower()
    if ext == ".md":
        return MD_HEADER % source_rel
    if ext == ".py":
        return PY_HEADER % source_rel
    return None


def render_synced(data, source_rel, target_rel):
    """The bytes a synced target must hold: the source plus its sync header, if the type takes one."""
    header = header_for(source_rel, target_rel)
    if header is None:
        return data
    text = data.decode("utf-8")
    if not target_rel.endswith(".py"):
        return (header + text).encode("utf-8")
    lines = text.splitlines(True)
    keep = 0
    if lines and lines[0].startswith("#!"):
        keep = 1
        if len(lines) > 1 and CODING_RE.match(lines[1]):
            keep = 2
    elif lines and CODING_RE.match(lines[0]):
        keep = 1
    head = "".join(lines[:keep])
    if head and not head.endswith("\n"):
        head += "\n"
    return (head + header + "".join(lines[keep:])).encode("utf-8")


def render_generated(kind, data, source_rel):
    """Bytes for a generated target built from connector/tool-classes.json."""
    if kind == "copy":
        return data
    try:
        classes = json.loads(data.decode("utf-8"))
    except ValueError as exc:
        raise ValueError("%s is not valid JSON (%s)" % (source_rel, exc)) from None
    tools = classes.get("tools") if isinstance(classes, dict) else None
    if not isinstance(tools, list) or not all(isinstance(t, dict) and isinstance(t.get("name"), str) for t in tools):
        raise ValueError("%s has no usable 'tools' list" % source_rel)
    if kind == "names":
        return ("\n".join(sorted(t["name"] for t in tools)) + "\n").encode("utf-8")
    counts = {}
    for t in tools:
        counts[t.get("class")] = counts.get(t.get("class"), 0) + 1
    inventory = {
        "$comment": "GENERATED by tools/sync_shared.py from %s; do not edit. Unofficial; not affiliated with "
        "Robinhood Markets, Inc." % source_rel,
        "source": source_rel,
        "snapshot_captured_at": classes.get("snapshot_captured_at"),
        "count": len(tools),
        "class_counts": counts,
        "tools": dict((t["name"], t.get("class")) for t in tools),
        "absent_referenced": [
            a.get("name") for a in classes.get("absent_referenced") or [] if isinstance(a, dict) and a.get("name")
        ],
    }
    return (json.dumps(inventory, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def splice_region(text, block, begin, end):
    """Replace the begin..end marker region of text with block. Raises ValueError on bad markers."""
    lines = text.split("\n")
    starts = [i for i, ln in enumerate(lines) if ln.lstrip().startswith(begin)]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == end]
    if not starts or not ends:
        raise ValueError("invariants markers missing (need a line starting %r and a line %r)" % (begin, end))
    if len(starts) > 1 or len(ends) > 1:
        raise ValueError("invariants markers appear more than once")
    b, e = starts[0], ends[0]
    if e < b:
        raise ValueError("the invariants END marker comes before the BEGIN marker")
    return "\n".join(lines[:b] + block.rstrip("\n").split("\n") + lines[e + 1 :])


# ---------------------------------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------------------------------


def _read_bytes(root, rel):
    with open(_abs(root, rel), "rb") as fh:
        return fh.read()


def _source_mode(root, rel):
    return stat.S_IMODE(os.stat(_abs(root, rel)).st_mode)


def load_manifest(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise ManifestError("%s: cannot read (%s)" % (path, exc.strerror or exc)) from None
    except ValueError as exc:
        raise ManifestError("%s: not valid JSON (%s)" % (path, exc)) from None
    if not isinstance(data, dict):
        raise ManifestError("%s: expected a JSON object" % path)
    unknown = sorted(k for k in data if k not in ("license", "files", "generated", "invariants"))
    if unknown:
        raise ManifestError("%s: unknown keys %s" % (path, ", ".join(unknown)))
    entries = ([data["license"]] if "license" in data else []) + list(data.get("files") or [])
    for entry in entries:
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("source"), str)
            and isinstance(entry.get("targets"), list)
            and entry["targets"]
        ):
            raise ManifestError("%s: every license/files entry needs a 'source' string and a 'targets' list" % path)
    for entry in data.get("generated") or []:
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("from"), str)
            and isinstance(entry.get("to"), str)
            and entry.get("kind") in GENERATED_KINDS
        ):
            raise ManifestError(
                "%s: every generated entry needs 'from', 'to' and a 'kind' in %s" % (path, ", ".join(GENERATED_KINDS))
            )
    inv = data.get("invariants")
    if inv is not None and not (
        isinstance(inv, dict)
        and all(isinstance(inv.get(k), str) and inv.get(k) for k in ("source", "begin", "end"))
        and isinstance(inv.get("targets"), list)
    ):
        raise ManifestError("%s: 'invariants' needs 'source', 'targets', 'begin' and 'end'" % path)
    return data


def build_plan(root, manifest):
    plan = Plan()
    copy_entries = ([manifest["license"]] if "license" in manifest else []) + list(manifest.get("files") or [])
    for entry in copy_entries:
        src = entry["source"]
        try:
            data = _read_bytes(root, src)
            mode = _source_mode(root, src)
        except OSError:
            plan.errors.append("%s: source is missing (named in %s)" % (src, MANIFEST_REL))
            continue
        for pattern in entry["targets"]:
            try:
                targets = expand_target(root, pattern, src)
            except ValueError as exc:
                plan.errors.append("%s: %s" % (src, exc))
                continue
            for tgt in targets:
                try:
                    plan.add_target(tgt, render_synced(data, src, tgt), src, mode)
                except UnicodeDecodeError:
                    plan.errors.append("%s: not UTF-8 text, so it cannot carry a sync header" % src)
    for entry in manifest.get("generated") or []:
        src, tgt = entry["from"], entry["to"]
        try:
            data = _read_bytes(root, src)
        except OSError:
            plan.errors.append("%s: source is missing (named in %s)" % (src, MANIFEST_REL))
            continue
        try:
            paths = expand_target(root, tgt, src)
            if len(paths) != 1:
                raise ValueError("a generated target must name exactly one file (it expands to %d)" % len(paths))
            plan.add_target(paths[0], render_generated(entry["kind"], data, src), src)
        except ValueError as exc:
            plan.errors.append("%s: %s" % (tgt, exc))
    inv = manifest.get("invariants")
    if inv:
        try:
            block = _read_bytes(root, inv["source"]).decode("utf-8")
        except OSError:
            plan.errors.append("%s: source is missing (named in %s)" % (inv["source"], MANIFEST_REL))
            block = None
        if block is not None:
            first = block.lstrip("\n").split("\n", 1)[0]
            last = block.rstrip("\n").rsplit("\n", 1)[-1]
            if not first.startswith(inv["begin"]) or last.strip() != inv["end"]:
                plan.errors.append(
                    "%s: must start with a line beginning %r and end with the line %r"
                    % (inv["source"], inv["begin"], inv["end"])
                )
                block = None
        for pattern in inv["targets"]:
            try:
                targets = expand_target(root, pattern, inv["source"])
            except ValueError as exc:
                plan.errors.append("%s: %s" % (inv["source"], exc))
                continue
            for tgt in targets:
                try:
                    with open(_abs(root, tgt), "r", encoding="utf-8", newline="") as fh:
                        current = fh.read()
                except OSError:
                    plan.errors.append("%s: missing (it needs the invariants markers)" % tgt)
                    continue
                if block is None:
                    continue
                try:
                    plan.regions.append((tgt, splice_region(current, block, inv["begin"], inv["end"]), current))
                except ValueError as exc:
                    plan.errors.append("%s: %s" % (tgt, exc))
    return plan


def find_orphans(root, planned):
    """Files carrying a sync header that no manifest entry produces."""
    orphans = []
    for rel_dir, recursive in ORPHAN_SCAN:
        base = _abs(root, rel_dir)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", ".git") and recursive)
            for name in sorted(filenames):
                if not name.endswith((".md", ".py")):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                if rel in planned:
                    continue
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        head = [fh.readline(), fh.readline(), fh.readline()]
                except OSError:
                    continue
                if any(HEADER_RE.match(ln.rstrip("\n")) for ln in head):
                    orphans.append(rel)
    return orphans


# ---------------------------------------------------------------------------------------------------
# Check and write
# ---------------------------------------------------------------------------------------------------


def _current_bytes(root, rel):
    try:
        return _read_bytes(root, rel)
    except OSError:
        return None


def _diff(rel, current, expected):
    cur = current.decode("utf-8", "replace").splitlines(True) if current is not None else []
    exp = expected.decode("utf-8", "replace").splitlines(True)
    return difflib.unified_diff(cur, exp, "a/" + rel if current is not None else "/dev/null", "b/" + rel)


def _write(root, rel, data, mode):
    path = _abs(root, rel)
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    tmp = path + ".sync-tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, mode if mode is not None else 0o644)
    os.replace(tmp, path)


def run(root, manifest_path=None, check=False, quiet=False, out=sys.stdout, err=sys.stderr):
    manifest_path = manifest_path or _abs(root, MANIFEST_REL)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        err.write("sync_shared: %s\n" % exc)
        return 2
    plan = build_plan(root, manifest)
    stale = []
    for rel, expected, source, mode in plan.targets:
        current = _current_bytes(root, rel)
        if current == expected:
            continue
        stale.append(rel)
        if check:
            out.writelines(_diff(rel, current, expected))
        else:
            _write(root, rel, expected, mode)
            if not quiet:
                out.write("sync_shared: %s %s (from %s)\n" % ("created" if current is None else "updated", rel, source))
    for rel, expected, current in plan.regions:
        if expected == current:
            continue
        stale.append(rel)
        if check:
            out.writelines(_diff(rel, current.encode("utf-8"), expected.encode("utf-8")))
        else:
            with open(_abs(root, rel), "w", encoding="utf-8", newline="") as fh:
                fh.write(expected)
            if not quiet:
                out.write("sync_shared: updated the invariants region of %s\n" % rel)
    errors = list(plan.errors)
    planned = plan.target_names()
    for rel in find_orphans(root, planned):
        errors.append(
            "%s: carries a sync header but no manifest entry produces it; delete it or add it to %s"
            % (rel, MANIFEST_REL)
        )
    for msg in errors:
        err.write("sync_shared: error: %s\n" % msg)
    total = len(plan.targets) + len(plan.regions)
    if check:
        if stale:
            err.write(
                "sync_shared --check: %d of %d targets out of date; run python3 tools/sync_shared.py\n"
                % (len(stale), total)
            )
        elif not quiet:
            err.write("sync_shared --check: %d targets in sync\n" % total)
        return 1 if stale or errors else 0
    if not quiet:
        err.write("sync_shared: %d targets, %d written, %d errors\n" % (total, len(stale), len(errors)))
    return 1 if errors else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Sync shared rules, scripts and generated files into the skills and hooks."
    )
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 with a diff when anything is stale")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="repository root (default: %(default)s)")
    parser.add_argument("--manifest", default=None, help="manifest path (default: <root>/%s)" % MANIFEST_REL)
    parser.add_argument("--quiet", action="store_true", help="print only diffs and errors")
    args = parser.parse_args(argv)
    return run(os.path.abspath(args.root), args.manifest, check=args.check, quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
