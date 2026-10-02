#!/usr/bin/env python3
"""validate_skills.py - check every skill's frontmatter, layout and scripts before anything ships.

Unofficial; not affiliated with Robinhood Markets, Inc. Stdlib only, Python 3.9+, no network access.

A skill that loads with a malformed frontmatter, points at a file it does not ship, or carries a script
whose self-test fails will misbehave on some agent surface long before anyone notices. These checks run
in CI on every skill directory under skills/:

  frontmatter   keys are exactly name, description, license, metadata; the description is a folded
                block scalar (>-) of at most 1024 characters with no < or > that contains "Not for";
                name equals the directory name, is at most 64 characters and is kebab-case; metadata
                holds version, author, requires, connector-tools-verified, unofficial and homepage,
                every value a string (quote anything YAML would read as a number, date or boolean)
  layout        SKILL.md is at most 500 lines with LF line endings; the unofficial label follows the
                title; the invariants markers are present once each, BEGIN before END; LICENSE.txt
                exists; every references/, scripts/ and assets/ file named in SKILL.md exists
  scripts       every scripts/*.py passes `--selftest` (skip with --no-selftest)

The frontmatter is read with a strict parser for the small YAML subset skills use (block and flow maps,
plain, quoted and block scalars); anything outside it is reported rather than guessed at. PyYAML is not
required.

Usage: python3 tools/validate_skills.py [--root DIR] [--skill NAME ...] [--no-selftest] [--timeout SECS]

Each failure prints on stdout as `path:line: message`; a summary goes to stderr. Exit 0 when every check
passes, 1 when any fails, 2 on a usage error.
"""

import argparse
import os
import re
import subprocess
import sys

DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = "skills"

FRONTMATTER_KEYS = ("name", "description", "license", "metadata")
METADATA_KEYS = ("version", "author", "requires", "connector-tools-verified", "unofficial", "homepage")
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
SKILL_MAX_LINES = 500
INV_BEGIN = "<!-- BEGIN shared:invariants"
INV_END = "<!-- END shared:invariants -->"
UNOFFICIAL_RE = re.compile(r"unofficial\b.*not affiliated with robinhood", re.I)
UNOFFICIAL_WINDOW = 12  # body lines searched for the unofficial label
# A skill-relative file path: references/…, scripts/… or assets/…, with an extension, not part of a
# longer path such as shared/scripts/x.py or a URL.
REF_RE = re.compile(
    r"(?<![\w./-])((?:references|scripts|assets)/(?:[A-Za-z0-9_\-]+/)*[A-Za-z0-9_\-][A-Za-z0-9_.\-]*\.[A-Za-z0-9]+)"
)
DEFAULT_TIMEOUT = 120


class YamlError(Exception):
    def __init__(self, line, msg):
        Exception.__init__(self, msg)
        self.line = line
        self.msg = msg


class Scalar(object):
    """A parsed scalar: its text, the YAML type a real parser would give it, and where it came from."""

    __slots__ = ("value", "kind", "style", "line")

    def __init__(self, value, kind, style, line):
        self.value, self.kind, self.style, self.line = value, kind, style, line


class Mapping(object):
    __slots__ = ("items", "style", "line")

    def __init__(self, items, style, line):
        self.items, self.style, self.line = items, style, line


# ---------------------------------------------------------------------------------------------------
# Strict mini-YAML for frontmatter
# ---------------------------------------------------------------------------------------------------

_KEY_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)[ \t]*:(?:[ \t]+(.*?))?[ \t]*$")
_BLOCK_RE = re.compile(r"^([>|])([+-]?)([1-9]?)(?:[ \t]+#.*)?$")
_INT_RE = re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$")
_FLOAT_RE = re.compile(
    r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
)
_BOOL_WORDS = frozenset("true false yes no on off y n".split())
_NULL_WORDS = frozenset(["", "~", "null"])
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt ].*)?$")
_ESCAPES = {"\\": "\\", '"': '"', "/": "/", "n": "\n", "t": "\t", "r": "\r", "0": "\0", "b": "\b", "f": "\f", " ": " "}


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _plain_kind(text):
    low = text.lower()
    if low in _NULL_WORDS:
        return "null"
    if low in _BOOL_WORDS:
        return "bool"
    if _INT_RE.match(text):
        return "int"
    if _FLOAT_RE.match(text):
        return "float"
    if _DATE_RE.match(text):
        return "timestamp"
    return "str"


def _strip_comment(text):
    m = re.search(r"[ \t]#", text)
    return text[: m.start()].rstrip() if m else text


def _parse_double(text, line):
    """Parse a double-quoted scalar starting at text[0]; return (value, rest)."""
    out, i = [], 1
    while i < len(text):
        c = text[i]
        if c == '"':
            return "".join(out), text[i + 1 :]
        if c == "\\":
            nxt = text[i + 1 : i + 2]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
                i += 2
                continue
            if nxt in ("x", "u", "U"):
                width = {"x": 2, "u": 4, "U": 8}[nxt]
                digits = text[i + 2 : i + 2 + width]
                if len(digits) == width and re.match(r"^[0-9a-fA-F]+$", digits):
                    out.append(chr(int(digits, 16)))
                    i += 2 + width
                    continue
            raise YamlError(line, "unsupported escape in a double-quoted value")
        out.append(c)
        i += 1
    raise YamlError(line, "double-quoted value is not closed on the same line (use a block scalar for long text)")


def _parse_single(text, line):
    out, i = [], 1
    while i < len(text):
        c = text[i]
        if c == "'":
            if text[i + 1 : i + 2] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), text[i + 1 :]
        out.append(c)
        i += 1
    raise YamlError(line, "single-quoted value is not closed on the same line (use a block scalar for long text)")


def _inline_scalar(text, line):
    """Parse a scalar written on the key's line (not a block scalar or flow map)."""
    if text.startswith('"') or text.startswith("'"):
        value, rest = (_parse_double if text[0] == '"' else _parse_single)(text, line)
        rest = rest.strip()
        if rest and not rest.startswith("#"):
            raise YamlError(line, "unexpected text after a quoted value")
        return Scalar(value, "str", "double" if text[0] == '"' else "single", line)
    text = _strip_comment(text)
    if text[:1] in ("[", "]", "}", "&", "*", "!", "%", "@", "`", "|", ">", "-") and (
        text[:1] != "-" or text[:2] in ("- ", "-")
    ):
        raise YamlError(line, "value starts with a YAML indicator (%r); quote it" % text[:1])
    if ": " in text or text.endswith(":"):
        raise YamlError(line, "value contains ': '; quote it")
    return Scalar(text, _plain_kind(text), "plain", line)


def _split_flow(text, line):
    parts, cur, quote, i = [], [], None, 0
    while i < len(text):
        c = text[i]
        if quote:
            cur.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
            cur.append(c)
        elif c in "{}[]":
            raise YamlError(line, "nested collections are not supported in a flow map")
        elif c == ",":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    if quote:
        raise YamlError(line, "unclosed quote in a flow map")
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _flow_map(text, line):
    text = text.strip()
    if not text.endswith("}"):
        text = _strip_comment(text)
    if not (text.startswith("{") and text.endswith("}")):
        raise YamlError(line, "flow map must open and close on one line")
    items = {}
    for part in _split_flow(text[1:-1], line):
        m = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)[ \t]*:[ \t]*(.*)$", part)
        if not m:
            raise YamlError(line, "cannot read flow-map entry %r" % part)
        key = m.group(1)
        if key in items:
            raise YamlError(line, "duplicate key %r" % key)
        items[key] = _inline_scalar(m.group(2), line) if m.group(2) else Scalar("", "null", "plain", line)
    return Mapping(items, "flow", line)


def _fold(lines, style, chomp):
    """Apply YAML folded (>) or literal (|) semantics plus chomping to de-indented content lines."""
    last = len(lines)
    while last > 0 and not lines[last - 1].strip():
        last -= 1
    body, trailing = lines[:last], len(lines) - last
    if style == "|":
        text = "\n".join(body)
    else:
        text, pending, prev_more, started = "", 0, False, False
        for ln in body:
            if not ln.strip():
                pending += 1
                continue
            more = ln[:1] in (" ", "\t")
            if not started:
                text = "\n" * pending + ln
                started = True
            elif pending == 0:
                text += ("\n" if (more or prev_more) else " ") + ln
            else:
                text += "\n" * (pending + (1 if (more or prev_more) else 0)) + ln
            pending, prev_more = 0, more
    if not body:
        return "\n" * trailing if chomp == "+" else ""
    if chomp == "-":
        return text
    if chomp == "+":
        return text + "\n" * (trailing + 1)
    return text + "\n"


def _parse_block(lines, start, parent_indent, first_line):
    """Parse a mapping whose entries sit at one indent deeper than parent_indent. Return (Mapping, next)."""
    items, i, indent = {}, start, None
    while i < len(lines):
        raw = lines[i]
        lineno = first_line + i
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if "\t" in raw[: _indent(raw) + 1]:
            raise YamlError(lineno, "tabs are not allowed for indentation")
        ind = _indent(raw)
        if ind <= parent_indent:
            break
        if indent is None:
            indent = ind
        elif ind != indent:
            raise YamlError(lineno, "inconsistent indentation (expected %d spaces)" % indent)
        body = raw[ind:]
        if body.startswith("- ") or body == "-":
            raise YamlError(lineno, "lists are not supported in skill frontmatter")
        m = _KEY_RE.match(body)
        if not m:
            raise YamlError(lineno, "expected 'key: value'")
        key, rest = m.group(1), (m.group(2) or "")
        if rest.startswith("#"):
            rest = ""  # "key: # comment" has no value
        if key in items:
            raise YamlError(lineno, "duplicate key %r" % key)
        i += 1
        stripped = _strip_comment(rest) if not rest.startswith(("'", '"')) else rest
        bm = _BLOCK_RE.match(stripped)
        if bm:
            block, i = _collect_block(lines, i, ind, first_line)
            chomp = bm.group(2)
            items[key] = Scalar(_fold(block, bm.group(1), chomp), "str", "block" + bm.group(1) + chomp, lineno)
        elif stripped.startswith("{"):
            items[key] = _flow_map(rest, lineno)
        elif stripped == "":
            nxt = i
            while nxt < len(lines) and (not lines[nxt].strip() or lines[nxt].lstrip().startswith("#")):
                nxt += 1
            if nxt < len(lines) and _indent(lines[nxt]) > ind:
                child, i = _parse_block(lines, i, ind, first_line)
                items[key] = child
            else:
                items[key] = Scalar("", "null", "plain", lineno)
        else:
            items[key] = _inline_scalar(rest, lineno)
    return Mapping(items, "block", first_line + start), i


def _collect_block(lines, i, parent_indent, first_line):
    """Collect the content lines of a block scalar and strip their common indentation."""
    raw, base = [], None
    while i < len(lines):
        ln = lines[i]
        if ln.strip():
            ind = _indent(ln)
            if ind <= parent_indent:
                break
            if base is None:
                base = ind
            elif ind < base:
                raise YamlError(first_line + i, "block scalar line is indented less than its first line")
        raw.append(ln)
        i += 1
    base = base or 0
    return [ln[base:] if len(ln) >= base else "" for ln in raw], i


def parse_frontmatter(text):
    """Split SKILL.md into (Mapping, body_lines, body_first_line). Raises YamlError."""
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        raise YamlError(1, "SKILL.md must start with a '---' frontmatter line")
    for end in range(1, len(lines)):
        if lines[end].rstrip() == "---":
            break
    else:
        raise YamlError(1, "the frontmatter is not closed by a '---' line")
    mapping, stop = _parse_block(lines[1:end], 0, -1, 2)
    if stop != end - 1:
        raise YamlError(2 + stop, "cannot read this frontmatter line")
    return mapping, lines[end + 1 :], end + 2


# ---------------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------------


class Report(object):
    def __init__(self, root):
        self.root = root
        self.failures = []

    def fail(self, path, line, msg):
        rel = os.path.relpath(path, self.root).replace(os.sep, "/")
        self.failures.append("%s:%d: %s" % (rel, line, msg))


def check_frontmatter(report, skill_dir, path, mapping):
    name_dir = os.path.basename(skill_dir)
    keys = list(mapping.items)
    extra = [k for k in keys if k not in FRONTMATTER_KEYS]
    missing = [k for k in FRONTMATTER_KEYS if k not in mapping.items]
    if extra:
        report.fail(
            path,
            mapping.items[extra[0]].line,
            "frontmatter keys must be exactly %s; remove %s" % (", ".join(FRONTMATTER_KEYS), ", ".join(extra)),
        )
    if missing:
        report.fail(path, 1, "frontmatter is missing %s" % ", ".join(missing))

    name = mapping.items.get("name")
    if isinstance(name, Scalar):
        if name.kind != "str":
            report.fail(path, name.line, "name must be a string")
        elif name.value != name_dir:
            report.fail(path, name.line, "name %r must equal the directory name %r" % (name.value, name_dir))
        if len(name.value) > NAME_MAX:
            report.fail(path, name.line, "name is %d characters (max %d)" % (len(name.value), NAME_MAX))
        if not NAME_RE.match(name.value):
            report.fail(
                path, name.line, "name %r must be lowercase letters and digits joined by single hyphens" % name.value
            )
    elif name is not None:
        report.fail(path, name.line, "name must be a string, not a map")

    desc = mapping.items.get("description")
    if isinstance(desc, Scalar):
        if desc.style != "block>-":
            report.fail(path, desc.line, "description must be a folded block scalar ('description: >-')")
        if desc.kind != "str" or not desc.value.strip():
            report.fail(path, desc.line, "description must be a non-empty string")
        if len(desc.value) > DESCRIPTION_MAX:
            report.fail(path, desc.line, "description is %d characters (max %d)" % (len(desc.value), DESCRIPTION_MAX))
        if "<" in desc.value or ">" in desc.value:
            report.fail(path, desc.line, "description must not contain '<' or '>'")
        if "Not for" not in desc.value:
            report.fail(path, desc.line, "description must say what the skill is 'Not for'")
    elif desc is not None:
        report.fail(path, desc.line, "description must be a string, not a map")

    lic = mapping.items.get("license")
    if lic is not None and (not isinstance(lic, Scalar) or lic.kind != "str" or not lic.value.strip()):
        report.fail(path, lic.line, "license must be a non-empty string")

    meta = mapping.items.get("metadata")
    if meta is None:
        return
    if not isinstance(meta, Mapping):
        report.fail(path, meta.line, "metadata must be a map of string keys to string values")
        return
    for key in METADATA_KEYS:
        if key not in meta.items:
            report.fail(path, meta.line, "metadata is missing %s" % key)
    for key, value in meta.items.items():
        if not isinstance(value, Scalar):
            report.fail(path, value.line, "metadata.%s must be a string, not a map" % key)
        elif value.kind != "str":
            report.fail(
                path, value.line, "metadata.%s is read as a %s; quote it so it stays a string" % (key, value.kind)
            )
        elif not value.value.strip():
            report.fail(path, value.line, "metadata.%s is empty" % key)


def check_body(report, skill_dir, path, text, body, body_first):
    lines = text.split("\n")
    count = len(text.splitlines())
    if count > SKILL_MAX_LINES:
        report.fail(
            path,
            SKILL_MAX_LINES + 1,
            "SKILL.md is %d lines (max %d); move detail into references/" % (count, SKILL_MAX_LINES),
        )
    if "\r" in text:
        report.fail(path, 1, "use LF line endings, not CRLF")

    head = [ln for ln in body if ln.strip()][:UNOFFICIAL_WINDOW]
    if not any(UNOFFICIAL_RE.search(ln) for ln in head):
        report.fail(path, body_first, "the title must be followed by '*Unofficial — not affiliated with Robinhood.*'")

    begins = [i for i, ln in enumerate(lines) if ln.lstrip().startswith(INV_BEGIN)]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == INV_END]
    if not begins or not ends:
        report.fail(
            path, body_first, "invariants markers missing: add a line starting %r and a line %r" % (INV_BEGIN, INV_END)
        )
    elif len(begins) > 1 or len(ends) > 1:
        report.fail(path, (begins + ends)[1] + 1, "invariants markers must appear exactly once")
    elif ends[0] < begins[0]:
        report.fail(path, ends[0] + 1, "the invariants END marker comes before the BEGIN marker")
    elif begins[0] < body_first - 1:
        report.fail(path, begins[0] + 1, "the invariants markers belong in the body, not the frontmatter")

    seen = set()
    for offset, ln in enumerate(body):
        for m in REF_RE.finditer(ln):
            ref = m.group(1)
            if ref in seen:
                continue
            seen.add(ref)
            if not os.path.isfile(os.path.join(skill_dir, *ref.split("/"))):
                report.fail(
                    path, body_first + offset, "SKILL.md references %s, which does not exist in this skill" % ref
                )

    if not os.path.isfile(os.path.join(skill_dir, "LICENSE.txt")):
        report.fail(path, 1, "LICENSE.txt is missing (run python3 tools/sync_shared.py)")


def check_scripts(report, skill_dir, timeout):
    scripts = os.path.join(skill_dir, "scripts")
    if not os.path.isdir(scripts):
        return
    for name in sorted(os.listdir(scripts)):
        if not name.endswith(".py") or name.startswith("_"):
            continue
        path = os.path.join(scripts, name)
        try:
            proc = subprocess.run(
                [sys.executable, path, "--selftest"],
                cwd=skill_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            report.fail(path, 1, "--selftest did not finish within %d s" % timeout)
            continue
        except OSError as exc:
            report.fail(path, 1, "--selftest could not start (%s)" % exc)
            continue
        if proc.returncode != 0:
            tail = proc.stdout.decode("utf-8", "replace").strip().splitlines()[-3:]
            report.fail(
                path, 1, "--selftest exited %d%s" % (proc.returncode, (": " + " | ".join(tail)) if tail else "")
            )


def validate_skill(report, skill_dir, selftest=True, timeout=DEFAULT_TIMEOUT):
    path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(path):
        report.fail(skill_dir, 1, "no SKILL.md in this skill directory")
        return
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()
    try:
        mapping, body, body_first = parse_frontmatter(text.replace("\r\n", "\n"))
    except YamlError as exc:
        report.fail(path, exc.line, "frontmatter: %s" % exc.msg)
        mapping, body, body_first = None, text.split("\n"), 1
    if mapping is not None:
        check_frontmatter(report, skill_dir, path, mapping)
    check_body(report, skill_dir, path, text, body, body_first)
    if selftest:
        check_scripts(report, skill_dir, timeout)


def list_skills(root):
    base = os.path.join(root, SKILLS_DIR)
    if not os.path.isdir(base):
        return None
    return sorted(d for d in os.listdir(base) if not d.startswith((".", "_")) and os.path.isdir(os.path.join(base, d)))


def run(root, only=None, selftest=True, timeout=DEFAULT_TIMEOUT, out=sys.stdout, err=sys.stderr):
    report = Report(root)
    skills = list_skills(root)
    if skills is None:
        err.write("validate_skills: no %s/ directory under %s\n" % (SKILLS_DIR, root))
        return 1
    if only:
        unknown = [s for s in only if s not in skills]
        if unknown:
            err.write("validate_skills: no such skill: %s\n" % ", ".join(unknown))
            return 2
        skills = [s for s in skills if s in only]
    if not skills:
        err.write("validate_skills: no skills found in %s/\n" % SKILLS_DIR)
        return 1
    for name in skills:
        validate_skill(report, os.path.join(root, SKILLS_DIR, name), selftest=selftest, timeout=timeout)
    for line in report.failures:
        out.write(line + "\n")
    err.write("validate_skills: %d skill(s), %d failure(s)\n" % (len(skills), len(report.failures)))
    return 1 if report.failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate every skill under skills/.")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="repository root (default: %(default)s)")
    parser.add_argument("--skill", action="append", default=None, help="validate only this skill (repeatable)")
    parser.add_argument("--no-selftest", action="store_true", help="skip running scripts/*.py --selftest")
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds per --selftest (default %(default)s)"
    )
    args = parser.parse_args(argv)
    return run(os.path.abspath(args.root), only=args.skill, selftest=not args.no_selftest, timeout=args.timeout)


if __name__ == "__main__":
    sys.exit(main())
