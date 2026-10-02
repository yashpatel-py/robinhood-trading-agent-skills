#!/usr/bin/env python3
"""Connector drift checks for the Preflight kit (build spec section G.2).

Unofficial; not affiliated with Robinhood Markets, Inc. Stdlib only, Python 3.9+, no network access.

The kit's safety rules are only as good as its picture of the connector. These checks keep the
skills, hooks and manifests honest against connector/tools.snapshot.json, the verbatim capture of
the live tool list:

  1 tool-names    Every tool-like name in the skills, shared rules, docs, eval templates, hooks and
                  integrations is a live tool, a known-absent tool written about as absent, or an
                  allowlisted non-tool identifier (connector/drift-allow.txt).
  2 parameters    Every `tool {param: ...}` / tool(param=...) usage and every row of the R4
                  account-number table names parameters the tool's inputSchema really has.
  3 classes       connector/tool-classes.json covers exactly the snapshot; the money class is exactly
                  the five order-placing tools; twins, account_param and paginates agree with the
                  schemas; every money tool is caught by the hook matcher, the Gemini excludeTools
                  list and the Codex matcher (and the Claude Code deny snippet, when present).
  4 param-facts   Every claim in connector/param-facts.json still appears in its file, and the
                  snapshot text still backs it.
  5 snapshot      Snapshot count, names and hashes verify; schemas/*.md parse to the same tools;
                  with --staleness N, fail when the capture is older than N days.
  6 hook-allow    No hook, optional or integration file emits a permission "allow".
  7 synced-copy   hooks/lib/*.py matches shared/scripts/*.py.

Usage: python3 tools/check_drift.py [--root DIR] [--checks 1,3,5] [--staleness DAYS] [--today YYYY-MM-DD]

Each failure prints on stdout as `file:line: message`; a per-check summary goes to stderr.
Exit 0 when every selected check passes, 1 when any fails, 2 on a usage error.
"""

import argparse
import bisect
import datetime as _dt
import json
import os
import re
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import snapshot_tools  # noqa: E402  (sibling module in tools/)

DEFAULT_ROOT = os.path.dirname(TOOLS_DIR)

SNAPSHOT_REL = "connector/tools.snapshot.json"
CLASSES_REL = "connector/tool-classes.json"
FACTS_REL = "connector/param-facts.json"
ALLOW_REL = "connector/drift-allow.txt"
SCHEMAS_REL = "connector/schemas"

# Tools referenced by descriptions or older docs that the connector does not expose (spec G.2 1b).
KNOWN_ABSENT = frozenset(["get_market_hours", "replace_option_order", "get_crypto_tax_lots", "get_quotes"])

# The money class is pinned here on purpose. If the connector adds an order-placing tool, the PR that
# classifies it must also update this set: a deliberate second key on the one change that matters most.
EXPECTED_MONEY = frozenset(
    [
        "place_equity_order",
        "place_advanced_order",
        "place_option_order",
        "place_crypto_order",
        "exercise_option",
    ]
)

CLASSES = ("read", "enroll_link", "simulate", "write_confirm", "cancel", "money")
ACCOUNT_PARAMS = ("account_number", "account_number=rhs_value", "rhs_account_number", None)
PAGINATES = ("next_url", "next", "next_cursor", "next_offset", None)
MIN_FACTS = 40
ABSENT_WINDOW = 160

TOOL_PATTERN = re.compile(
    r"\b(?:get|review|place|cancel|create|update|delete|add|remove|follow|unfollow|mark|run|preview|exercise|replace)"
    r"_[a-z0-9_]+\b"
)
SEARCH_PATTERN = re.compile(r"`search(?=[`\s{(])")
PREFIXED_PATTERN = re.compile(r"\bmcp__(?P<server>[A-Za-z0-9_.-]+)__(?P<tool>[a-z][a-z0-9_]*)\b")
# Phrases that mark a known-absent tool as being written about as absent. The spec's three phrases,
# plus the wordings its own normative rules use ("There is no `get_market_hours` tool", "never existed").
ABSENT_PHRASES = re.compile(
    r"not exposed|does not exist|doesn't exist|do not exist|absent|never existed|there is no|"
    r"is not a tool|isn't a tool|no such tool|not a connector tool|not in the tool list",
    re.I,
)
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
CALL_BRACE = re.compile(r"\b(?P<tool>[a-z][a-z0-9_]*)[ \t]*\{")
CALL_PAREN = re.compile(r"\b(?P<tool>[a-z][a-z0-9_]*)\(")
KEY_ALT = re.compile(r"^[\"']?([a-z][a-z0-9_]*)[\"']?\??(?:\s*(?:≤|≥|<=|>=|<|>|=)\s*\S.*)?$")
R4_HEADING = re.compile(r"^\s*(?:#{1,6}\s*|\*\*)R4\b")
NEXT_SECTION = re.compile(r"^\s*(?:#{1,6}\s|\*\*R\d+\b)")
ALLOW_GREP = re.compile(r"\"(permissionDecision|permission)\"\s*:\s*\"allow\"")
SYNC_HEADER = re.compile(r"^# synced from shared/\S+; do not edit\s*$")
GUARD_LAYER1 = re.compile(r"^(place|exercise|replace)_")
MATCHER_SAMPLE_SERVERS = (
    "robinhood-trading",
    "plugin_unofficial-rh-connector_robinhood",
    "rh-sandbox",
    "abcdefab-cdef-4abc-8def-abcdefabcdef",
)
SKIP_DIRS = frozenset([".git", "__pycache__", "node_modules", ".venv", "venv"])

CHECK_NAMES = {
    1: "tool-names",
    2: "parameters",
    3: "classes",
    4: "param-facts",
    5: "snapshot",
    6: "hook-allow",
    7: "synced-copy",
}


class Failure(object):
    __slots__ = ("check", "path", "line", "msg")

    def __init__(self, check, path, line, msg):
        self.check, self.path, self.line, self.msg = check, path, line, msg

    def __str__(self):
        return "%s:%d: %s" % (self.path, self.line, self.msg)

    def __repr__(self):
        return "Failure(%d, %r, %d, %r)" % (self.check, self.path, self.line, self.msg)


class Context(object):
    """Lazy access to the repo files the checks share."""

    def __init__(self, root, staleness=None, today=None):
        self.root = os.path.abspath(root)
        self.staleness = staleness
        self.today = today or _dt.datetime.now(_dt.timezone.utc).date()
        self._cache = {}

    def path(self, rel):
        return os.path.join(self.root, rel)

    def exists(self, rel):
        return os.path.exists(self.path(rel))

    def read_text(self, rel):
        key = ("text", rel)
        if key not in self._cache:
            try:
                with open(self.path(rel), encoding="utf-8", errors="replace") as fh:
                    self._cache[key] = fh.read().replace("\r\n", "\n")
            except OSError:
                self._cache[key] = None
        return self._cache[key]

    def load_json(self, rel):
        """Return (data, error_message)."""
        key = ("json", rel)
        if key not in self._cache:
            text = self.read_text(rel)
            if text is None:
                self._cache[key] = (None, "file not found")
            else:
                try:
                    self._cache[key] = (json.loads(text), None)
                except ValueError as exc:
                    self._cache[key] = (None, "not valid JSON: %s" % exc)
        return self._cache[key]

    def snapshot_tools(self):
        """{name: entry} from the snapshot, or None when it cannot be read."""
        data, err = self.load_json(SNAPSHOT_REL)
        if err or not isinstance(data, dict) or not isinstance(data.get("tools"), list):
            return None
        return {t["name"]: t for t in data["tools"] if isinstance(t, dict) and isinstance(t.get("name"), str)}

    def classes(self):
        """(list of tool entries, top-level dict or None, error) from tool-classes.json."""
        data, err = self.load_json(CLASSES_REL)
        if err:
            return None, None, err
        if isinstance(data, list):
            return data, None, None
        if isinstance(data, dict) and isinstance(data.get("tools"), list):
            return data["tools"], data, None
        return None, None, "expected a list of tools or an object with a 'tools' list"

    def line_of(self, rel, needle, default=1):
        text = self.read_text(rel)
        if not text:
            return default
        idx = text.find(needle)
        if idx < 0:
            return default
        return text.count("\n", 0, idx) + 1


# ---------------------------------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------------------------------


def _walk_files(root, rel_dir, suffix=None, recursive=True, skip_dirs=SKIP_DIRS):
    base = os.path.join(root, rel_dir)
    if not os.path.isdir(base):
        return []
    out = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
            for fn in sorted(filenames):
                if suffix is None or fn.endswith(suffix):
                    out.append(os.path.relpath(os.path.join(dirpath, fn), root))
    else:
        for fn in sorted(os.listdir(base)):
            full = os.path.join(base, fn)
            if os.path.isfile(full) and (suffix is None or fn.endswith(suffix)):
                out.append(os.path.relpath(full, root))
    return [p.replace(os.sep, "/") for p in out]


def scanned_files(root):
    """The files checks 1 and 2 read (spec G.2 check 1), in a stable order."""
    files = []
    files += _walk_files(root, "skills", ".md")
    files += _walk_files(root, "staging/skills", ".md")
    files += _walk_files(root, "shared", ".md")
    files += [f for f in ("README.md",) if os.path.isfile(os.path.join(root, f))]
    files += _walk_files(root, "docs", ".md")
    files += _walk_files(root, "evalkit/templates")
    files += _walk_files(root, "hooks", ".json", recursive=False)
    files += _walk_files(root, "integrations")
    files += [f for f in ("gemini-extension.json", "GEMINI.md", "AGENTS.md") if os.path.isfile(os.path.join(root, f))]
    seen, ordered = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            ordered.append(f)
    return ordered


class LineIndex(object):
    def __init__(self, text):
        self.starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                self.starts.append(i + 1)

    def line(self, offset):
        return bisect.bisect_right(self.starts, offset)


def load_allowlist(ctx, snapshot_names, failures):
    """Parse connector/drift-allow.txt: `identifier  # reason` per line. Returns the identifier set."""
    text = ctx.read_text(ALLOW_REL)
    allowed = set()
    if text is None:
        return allowed
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ident, _, reason = line.partition("#")
        ident, reason = ident.strip(), reason.strip()
        if not re.match(r"^[a-z][a-z0-9_]*$", ident):
            failures.append(Failure(1, ALLOW_REL, n, "allowlist entry %r is not an identifier" % ident))
            continue
        if not reason:
            failures.append(
                Failure(1, ALLOW_REL, n, "allowlist entry %s needs a reason comment (`%s  # why`)" % (ident, ident))
            )
            continue
        if snapshot_names is not None and ident in snapshot_names:
            failures.append(Failure(1, ALLOW_REL, n, "%s is a live connector tool; it must not be allowlisted" % ident))
            continue
        if ident in KNOWN_ABSENT:
            failures.append(
                Failure(
                    1,
                    ALLOW_REL,
                    n,
                    "%s is a known-absent tool; write about it as absent instead of allowlisting it" % ident,
                )
            )
            continue
        allowed.add(ident)
    return allowed


def _strip_md(s):
    return re.sub(r"\s+", " ", re.sub(r"[*`]", "", s))


def absent_context(text, start, end):
    """True when a known-absent tool name at text[start:end] is written about as absent."""
    window = text[max(0, start - ABSENT_WINDOW) : end + ABSENT_WINDOW]
    if ABSENT_PHRASES.search(_strip_md(window)):
        return True
    lines = text.split("\n")
    idx = text.count("\n", 0, start)
    here = lines[idx] if idx < len(lines) else ""
    candidates = []
    if LIST_ITEM.match(here):
        # Walk up to every enclosing list item (less indented) and the list's lead-in line, so a
        # heading item such as "- Absent though referenced:" covers the items nested under it.
        indent = len(here) - len(here.lstrip())
        j, steps = idx - 1, 0
        while j >= 0 and steps < 80:
            ln = lines[j]
            j -= 1
            steps += 1
            if not ln.strip():
                continue
            if LIST_ITEM.match(ln):
                ind = len(ln) - len(ln.lstrip())
                if ind < indent:
                    candidates.append(ln)
                    indent = ind
                continue
            if ln[:1] in (" ", "\t"):
                continue
            candidates.append(ln)
            break
    if here.lstrip().startswith("|"):
        j = idx
        while j - 1 >= 0 and lines[j - 1].lstrip().startswith("|"):
            j -= 1
        candidates.append(lines[j])
        k = j - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        if k >= 0:
            candidates.append(lines[k])
    return any(ABSENT_PHRASES.search(_strip_md(c)) for c in candidates)


# ---------------------------------------------------------------------------------------------------
# Check 1: tool names
# ---------------------------------------------------------------------------------------------------


def check_tool_names(ctx):
    failures = []
    names = ctx.snapshot_tools()
    if names is None:
        return [Failure(1, SNAPSHOT_REL, 1, "cannot run the tool-name check: the snapshot is missing or unreadable")]
    allowed = load_allowlist(ctx, names, failures)
    for rel in scanned_files(ctx.root):
        text = ctx.read_text(rel)
        if not text:
            continue
        hits = [(m.group(0), m.start(), m.end()) for m in TOOL_PATTERN.finditer(text)]
        for m in PREFIXED_PATTERN.finditer(text):
            server = m.group("server")
            if "robinhood" in server.lower() or server == "rh-sandbox":
                hits.append((m.group("tool"), m.start("tool"), m.end("tool")))
        index = LineIndex(text)
        reported = set()
        for name, start, end in hits:
            if name in names or name in allowed:
                continue
            line = index.line(start)
            if (line, name) in reported:
                continue
            if name in KNOWN_ABSENT:
                if absent_context(text, start, end):
                    continue
                msg = (
                    "`%s` is not exposed by the connector; mention it only as absent "
                    '("not exposed", "does not exist" or "absent" within %d characters)' % (name, ABSENT_WINDOW)
                )
            else:
                msg = "unknown tool name `%s`: not in %s. If it is not a tool, add it to %s with a reason" % (
                    name,
                    SNAPSHOT_REL,
                    ALLOW_REL,
                )
            reported.add((line, name))
            failures.append(Failure(1, rel, line, msg))
        if "search" not in names:
            for m in SEARCH_PATTERN.finditer(text):
                failures.append(Failure(1, rel, index.line(m.start()), "`search` is no longer in %s" % SNAPSHOT_REL))
    return failures


# ---------------------------------------------------------------------------------------------------
# Check 2: parameters
# ---------------------------------------------------------------------------------------------------

_PAIRS = {"{": "}", "[": "]", "(": ")"}


def extract_group(text, open_idx, inline=False, limit=4000):
    """Return the text inside the bracket at open_idx up to its match, or None if unbalanced."""
    stack = []
    quote = False
    i = open_idx
    end = min(len(text), open_idx + limit)
    while i < end:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                quote = False
        elif c == '"':
            quote = True
        elif c in _PAIRS:
            stack.append(_PAIRS[c])
        elif c in ")]}":
            if not stack or c != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return text[open_idx + 1 : i]
        elif c == "`" and inline:
            return None
        elif c == "\n" and text[i + 1 : i + 2] == "\n":
            return None
        i += 1
    return None


def split_top(s, sep):
    """Split on sep at bracket depth 0, outside double quotes and <placeholder> spans."""
    parts, depth, angle, quote, cur = [], 0, 0, False, []
    i = 0
    while i < len(s):
        c = s[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                quote = False
        elif c == '"':
            quote = True
            cur.append(c)
        elif c in "{[(":
            depth += 1
            cur.append(c)
        elif c in "}])":
            depth = max(0, depth - 1)
            cur.append(c)
        elif c == "<" and i + 1 < len(s) and s[i + 1].isalpha():
            angle += 1
            cur.append(c)
        elif c == ">" and angle:
            angle -= 1
            cur.append(c)
        elif c == sep and depth == 0 and angle == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    return parts


def brace_keys(content):
    """Yield (key, value_text) for each parameter named inside a `{...}` usage."""
    for seg in split_top(content, ","):
        seg = seg.strip()
        if not seg:
            continue
        pieces = split_top(seg, ":")
        key_part = pieces[0]
        value = ":".join(pieces[1:]).strip() if len(pieces) > 1 else ""
        key_part = re.sub(r"\([^()]*\)", " ", key_part)
        key_part = re.sub(r"<[^<>]*>", " ", key_part)
        for alt in re.split(r"\s*(?:\||\+|/|\band\b|\bor\b)\s*", key_part):
            m = KEY_ALT.match(alt.strip())
            if m:
                yield m.group(1), value


def paren_keys(content):
    for seg in split_top(content, ","):
        m = re.match(r"^\s*([a-z_][a-z0-9_]*)\s*=(?!=)", seg)
        if m:
            yield m.group(1)


def _props(schema):
    return (schema or {}).get("properties", {}) or {}


def _check_keys(failures, rel, line, tool, schema, content, where=None):
    props = _props(schema)
    label = where or tool
    for key, value in brace_keys(content):
        if key not in props:
            failures.append(
                Failure(
                    2,
                    rel,
                    line,
                    "`%s` has no parameter `%s` (it takes: %s)"
                    % (label, key, ", ".join(sorted(props)) or "no parameters"),
                )
            )
            continue
        sub = props[key] or {}
        v = value.lstrip()
        if v.startswith("{") and _props(sub):
            inner = extract_group(v, 0)
            if inner is not None:
                _check_keys(failures, rel, line, tool, sub, inner, "%s.%s" % (label, key))
        elif v.startswith("[") and _props(sub.get("items")):
            inner = extract_group(v, 0)
            if inner is not None and inner.lstrip().startswith("{"):
                body = extract_group(inner.lstrip(), 0)
                if body is not None:
                    _check_keys(failures, rel, line, tool, sub["items"], body, "%s.%s[]" % (label, key))


def expand_tool_token(token):
    """'review_/place_/cancel_equity_order' -> the three full names; plain names pass through."""
    if "/" not in token:
        return [token]
    parts = token.split("/")
    last = parts[-1]
    if "_" not in last:
        return [p for p in parts if p]
    suffix = last.split("_", 1)[1]
    out = []
    for p in parts[:-1]:
        out.append(p + suffix if p.endswith("_") else p)
    out.append(last)
    return out


def find_r4_table(text):
    """Return [(line_number, cells)] for the data rows of the R4 table, or None when there is none."""
    lines = text.split("\n")
    head = None
    for i, ln in enumerate(lines):
        if R4_HEADING.match(ln):
            head = i
            break
    if head is None:
        return None
    rows, in_table = [], False
    for i in range(head + 1, len(lines)):
        ln = lines[i]
        if ln.lstrip().startswith("|"):
            in_table = True
            cells = [c.strip() for c in ln.strip().replace("\\|", "\x00").strip("|").split("|")]
            cells = [c.replace("\x00", "|") for c in cells]
            rows.append((i + 1, cells))
            continue
        if in_table:
            break
        if NEXT_SECTION.match(ln):
            break
    if len(rows) < 2:
        return None
    header = " ".join(rows[0][1]).lower()
    if "key" not in header and "tool" not in header:
        return None  # some other table that happens to follow an R4 mention
    body = [r for r in rows[1:] if not re.match(r"^:?-{3,}:?$", r[1][0] if r[1] else "")]
    return body


def _r4_expected(first_cell):
    m = re.search(r"key\s*`([a-z_]+)`", first_cell)
    if m:
        key, rest = m.group(1), first_cell[m.end() :].lower()
        if key == "rhs_account_number":
            return True, "rhs_account_number"
        if key == "account_number":
            return True, "account_number=rhs_value" if "rhs" in rest else "account_number"
        return False, "unknown key `%s`" % key
    if "no account" in first_cell.lower():
        return True, None
    return False, "cannot tell which key this row describes"


def check_r4_table(ctx, rel, text, snap, class_by_name, failures):
    rows = find_r4_table(text)
    if rows is None:
        return False
    listed = set()
    for line, cells in rows:
        if len(cells) < 2:
            failures.append(Failure(2, rel, line, "R4 row has fewer than two cells"))
            continue
        ok, expected = _r4_expected(cells[0])
        if not ok:
            failures.append(Failure(2, rel, line, "R4 row: %s" % expected))
            continue
        for token in re.findall(r"`([a-z][a-z0-9_/]*)`", cells[1]):
            for name in expand_tool_token(token):
                if "_" not in name and name != "search":
                    continue
                if name not in snap:
                    failures.append(
                        Failure(2, rel, line, "R4 table lists `%s`, which is not in %s" % (name, SNAPSHOT_REL))
                    )
                    continue
                props = _props(snap[name].get("inputSchema"))
                if expected in ("account_number", "account_number=rhs_value") and "account_number" not in props:
                    failures.append(Failure(2, rel, line, "R4 table: `%s` has no `account_number` parameter" % name))
                elif expected == "rhs_account_number" and "rhs_account_number" not in props:
                    failures.append(
                        Failure(2, rel, line, "R4 table: `%s` has no `rhs_account_number` parameter" % name)
                    )
                elif expected is None and ("account_number" in props or "rhs_account_number" in props):
                    failures.append(
                        Failure(2, rel, line, "R4 table says `%s` takes no account, but its schema has one" % name)
                    )
                if name in class_by_name and class_by_name[name].get("account_param") != expected:
                    failures.append(
                        Failure(
                            2,
                            rel,
                            line,
                            "R4 table puts `%s` under %s but %s says %s"
                            % (name, expected, CLASSES_REL, class_by_name[name].get("account_param")),
                        )
                    )
                if expected is not None:
                    listed.add(name)
    for name, entry in sorted(class_by_name.items()):
        if entry.get("account_param") and name not in listed and name in snap:
            failures.append(
                Failure(
                    2,
                    rel,
                    rows[0][0] if rows else 1,
                    "R4 table omits `%s` (account_param %s)" % (name, entry.get("account_param")),
                )
            )
    return True


def check_parameters(ctx):
    failures = []
    snap = ctx.snapshot_tools()
    if snap is None:
        return [Failure(2, SNAPSHOT_REL, 1, "cannot run the parameter check: the snapshot is missing or unreadable")]
    tools, _meta, _err = ctx.classes()
    class_by_name = {t.get("name"): t for t in (tools or []) if isinstance(t, dict)}
    for rel in scanned_files(ctx.root):
        text = ctx.read_text(rel)
        if not text:
            continue
        index = LineIndex(text)
        for m in CALL_BRACE.finditer(text):
            tool = m.group("tool")
            if tool not in snap:
                continue
            backticked = m.start("tool") > 0 and text[m.start("tool") - 1] == "`"
            if tool == "search" and not backticked:
                continue
            content = extract_group(text, m.end() - 1, inline=backticked)
            if content is None:
                continue
            _check_keys(failures, rel, index.line(m.start("tool")), tool, snap[tool].get("inputSchema"), content)
        for m in CALL_PAREN.finditer(text):
            tool = m.group("tool")
            if tool not in snap:
                continue
            backticked = m.start("tool") > 0 and text[m.start("tool") - 1] == "`"
            if tool == "search" and not backticked:
                continue
            content = extract_group(text, m.end() - 1, inline=backticked)
            if content is None:
                continue
            props = _props(snap[tool].get("inputSchema"))
            for key in paren_keys(content):
                if key not in props:
                    failures.append(
                        Failure(
                            2,
                            rel,
                            index.line(m.start("tool")),
                            "`%s` has no parameter `%s` (it takes: %s)"
                            % (tool, key, ", ".join(sorted(props)) or "no parameters"),
                        )
                    )
        if rel.endswith(".md"):
            found = check_r4_table(ctx, rel, text, snap, class_by_name, failures)
            if rel == "shared/connector-rules.md" and not found:
                failures.append(
                    Failure(2, rel, 1, "no R4 account-number table found (an R4 heading followed by a table)")
                )
    return failures


# ---------------------------------------------------------------------------------------------------
# Check 3: classes and guard coverage
# ---------------------------------------------------------------------------------------------------


def _expected_paginates(schema):
    return snapshot_tools.suggest_paginates(schema)


def _money_matcher(data, rel, failures):
    """Find the PreToolUse matcher whose command runs guard.sh in money mode."""
    hooks = (data or {}).get("hooks") if isinstance(data, dict) else None
    entries = (hooks or {}).get("PreToolUse") if isinstance(hooks, dict) else None
    if not isinstance(entries, list) or not entries:
        failures.append(Failure(3, rel, 1, "no hooks.PreToolUse entries"))
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cmds = [h.get("command", "") for h in entry.get("hooks", []) if isinstance(h, dict)]
        if any("guard.sh" in c and re.search(r"\bmoney\b", c) for c in cmds):
            matcher = entry.get("matcher")
            if not isinstance(matcher, str):
                failures.append(Failure(3, rel, 1, "the guard.sh money entry has no matcher string"))
                return None
            return matcher
    failures.append(Failure(3, rel, 1, "no PreToolUse entry runs `guard.sh ... money`"))
    return None


def _test_matcher(ctx, rel, matcher, money, failures, label):
    try:
        rx = re.compile(matcher)
    except re.error as exc:
        failures.append(Failure(3, rel, ctx.line_of(rel, "matcher"), "%s matcher does not compile: %s" % (label, exc)))
        return
    line = ctx.line_of(rel, json.dumps(matcher))
    for tool in sorted(money):
        missed = [s for s in MATCHER_SAMPLE_SERVERS if not rx.search("mcp__%s__%s" % (s, tool))]
        if missed:
            failures.append(
                Failure(
                    3,
                    rel,
                    line,
                    "%s matcher %r does not match money tool %s for server(s) %s"
                    % (label, matcher, tool, ", ".join(missed)),
                )
            )
    for probe in ("get_accounts", "review_equity_order"):
        if rx.search("mcp__robinhood-trading__%s" % probe):
            failures.append(
                Failure(3, rel, line, "%s matcher %r also matches non-money tool %s" % (label, matcher, probe))
            )


def check_classes(ctx):
    failures = []
    snap = ctx.snapshot_tools()
    if snap is None:
        return [Failure(3, SNAPSHOT_REL, 1, "cannot run the class check: the snapshot is missing or unreadable")]
    tools, meta, err = ctx.classes()
    if err:
        return [Failure(3, CLASSES_REL, 1, err)]
    by_name = {}
    for i, t in enumerate(tools):
        if not isinstance(t, dict) or not isinstance(t.get("name"), str):
            failures.append(Failure(3, CLASSES_REL, 1, "tools[%d] is not an object with a name" % i))
            continue
        name = t["name"]
        line = ctx.line_of(CLASSES_REL, '"name": "%s"' % name)
        if name in by_name:
            failures.append(Failure(3, CLASSES_REL, line, "%s is listed twice" % name))
        by_name[name] = t
        for key in ("family", "class", "account_param", "paginates"):
            if key not in t:
                failures.append(Failure(3, CLASSES_REL, line, "%s has no %s" % (name, key)))
        if t.get("class") not in CLASSES:
            failures.append(Failure(3, CLASSES_REL, line, "%s has unknown class %r" % (name, t.get("class"))))
        if t.get("account_param") not in ACCOUNT_PARAMS:
            failures.append(
                Failure(3, CLASSES_REL, line, "%s has unknown account_param %r" % (name, t.get("account_param")))
            )
        if t.get("paginates") not in PAGINATES:
            failures.append(Failure(3, CLASSES_REL, line, "%s has unknown paginates %r" % (name, t.get("paginates"))))
    for name in sorted(set(snap) - set(by_name)):
        failures.append(
            Failure(3, CLASSES_REL, 1, "%s is in the snapshot but has no class (classify it by hand)" % name)
        )
    for name in sorted(set(by_name) - set(snap)):
        failures.append(
            Failure(
                3,
                CLASSES_REL,
                ctx.line_of(CLASSES_REL, '"name": "%s"' % name),
                "%s is classified but not in the snapshot (remove it)" % name,
            )
        )

    money = set(n for n, t in by_name.items() if t.get("class") == "money")
    if money != EXPECTED_MONEY:
        failures.append(
            Failure(
                3,
                CLASSES_REL,
                1,
                "money class is %s; expected exactly %s. A new order-placing tool "
                "needs EXPECTED_MONEY in tools/check_drift.py updated in the same change"
                % (sorted(money), sorted(EXPECTED_MONEY)),
            )
        )
    for name in sorted(money):
        if not GUARD_LAYER1.match(name):
            failures.append(
                Failure(
                    3,
                    CLASSES_REL,
                    ctx.line_of(CLASSES_REL, '"name": "%s"' % name),
                    "money tool %s does not match the hook's always-on pattern ^(place|exercise|replace)_" % name,
                )
            )
    for name, entry in sorted(snap.items()):
        desc = entry.get("description", "") or ""
        if (GUARD_LAYER1.match(name) or "real money" in desc.lower()) and by_name.get(name, {}).get("class") != "money":
            failures.append(
                Failure(
                    3,
                    CLASSES_REL,
                    ctx.line_of(CLASSES_REL, '"name": "%s"' % name),
                    "%s places, exercises or replaces orders (by name or a 'real money' description) "
                    "but is not class money" % name,
                )
            )

    for name, t in sorted(by_name.items()):
        if name not in snap:
            continue
        line = ctx.line_of(CLASSES_REL, '"name": "%s"' % name)
        schema = snap[name].get("inputSchema") or {}
        props = _props(schema)
        ap = t.get("account_param")
        if ap in ("account_number", "account_number=rhs_value"):
            if "account_number" not in props:
                failures.append(
                    Failure(
                        3, CLASSES_REL, line, "%s: account_param %s but the schema has no account_number" % (name, ap)
                    )
                )
            else:
                mentions_rhs = "rhs_account_number" in ((props["account_number"] or {}).get("description", "") or "")
                if mentions_rhs and ap != "account_number=rhs_value":
                    failures.append(
                        Failure(
                            3,
                            CLASSES_REL,
                            line,
                            "%s: the schema says account_number takes the rhs_account_number "
                            "value; account_param must be account_number=rhs_value" % name,
                        )
                    )
                if not mentions_rhs and ap == "account_number=rhs_value":
                    failures.append(
                        Failure(
                            3,
                            CLASSES_REL,
                            line,
                            "%s: account_param account_number=rhs_value but the schema "
                            "no longer says the value is rhs_account_number" % name,
                        )
                    )
        elif ap == "rhs_account_number":
            if "rhs_account_number" not in props:
                failures.append(
                    Failure(3, CLASSES_REL, line, "%s: account_param rhs_account_number but the schema has none" % name)
                )
        elif ap is None and ("account_number" in props or "rhs_account_number" in props):
            failures.append(
                Failure(3, CLASSES_REL, line, "%s: the schema takes an account number but account_param is null" % name)
            )
        want = _expected_paginates(schema)
        if t.get("paginates") != want:
            failures.append(
                Failure(
                    3,
                    CLASSES_REL,
                    line,
                    "%s: paginates is %r but the schema's cursor/offset says %r" % (name, t.get("paginates"), want),
                )
            )
        pt, rt = t.get("place_twin"), t.get("review_twin")
        if not isinstance(pt, (str, type(None))) or not isinstance(rt, (str, type(None))):
            failures.append(Failure(3, CLASSES_REL, line, "%s: place_twin and review_twin must be tool names" % name))
            continue
        if pt is not None:
            if t.get("class") != "simulate":
                failures.append(Failure(3, CLASSES_REL, line, "%s has place_twin but is not class simulate" % name))
            if by_name.get(pt, {}).get("class") != "money" or by_name.get(pt, {}).get("review_twin") != name:
                failures.append(
                    Failure(
                        3,
                        CLASSES_REL,
                        line,
                        "%s.place_twin %s is not a money tool pointing back with review_twin" % (name, pt),
                    )
                )
        if rt is not None:
            if t.get("class") != "money":
                failures.append(Failure(3, CLASSES_REL, line, "%s has review_twin but is not class money" % name))
            if by_name.get(rt, {}).get("class") != "simulate" or by_name.get(rt, {}).get("place_twin") != name:
                failures.append(
                    Failure(
                        3,
                        CLASSES_REL,
                        line,
                        "%s.review_twin %s is not a simulate tool pointing back with place_twin" % (name, rt),
                    )
                )
        if t.get("class") == "money" and name.startswith("place_") and rt is None:
            failures.append(Failure(3, CLASSES_REL, line, "money tool %s has no review_twin" % name))
        if t.get("class") == "money" and name.startswith("exercise_") and rt is not None:
            failures.append(
                Failure(3, CLASSES_REL, line, "%s must not have a review_twin (it is denied in every mode)" % name)
            )

    if isinstance(meta, dict):
        absent = meta.get("absent_referenced")
        if absent is not None:
            absent_names = set(a.get("name") for a in absent if isinstance(a, dict))
            if absent_names != set(KNOWN_ABSENT):
                failures.append(
                    Failure(
                        3,
                        CLASSES_REL,
                        ctx.line_of(CLASSES_REL, '"absent_referenced"'),
                        "absent_referenced lists %s; tools/check_drift.py KNOWN_ABSENT is %s"
                        % (sorted(absent_names), sorted(KNOWN_ABSENT)),
                    )
                )
            for n in sorted(absent_names & set(snap)):
                failures.append(
                    Failure(
                        3,
                        CLASSES_REL,
                        ctx.line_of(CLASSES_REL, '"name": "%s"' % n),
                        "%s is listed as absent but is now in the snapshot" % n,
                    )
                )
        groups = []
        nip = meta.get("not_in_published_list")
        if isinstance(nip, dict):
            groups.append(("not_in_published_list", nip.get("tools", [])))
        odd = meta.get("older_docs_disagree")
        if isinstance(odd, dict):
            groups.append(("older_docs_disagree", odd.get("tools", [])))
        for note in meta.get("availability_notes", []) or []:
            if isinstance(note, dict):
                groups.append(("availability_notes", note.get("tools", [])))
        for label, names in groups:
            for n in names:
                if n not in snap:
                    failures.append(
                        Failure(
                            3,
                            CLASSES_REL,
                            ctx.line_of(CLASSES_REL, '"%s"' % label),
                            "%s names %s, which is not in the snapshot" % (label, n),
                        )
                    )

    # Guard coverage for the pinned money set (spec G.2 check 3 and D.7).
    money_now = money | set(EXPECTED_MONEY)
    rel = "hooks/hooks.json"
    data, err = ctx.load_json(rel)
    if err:
        failures.append(Failure(3, rel, 1, "cannot verify guard coverage: %s" % err))
    else:
        matcher = _money_matcher(data, rel, failures)
        if matcher is not None:
            _test_matcher(ctx, rel, matcher, money_now, failures, "hooks.json money")
    rel = "gemini-extension.json"
    data, err = ctx.load_json(rel)
    if err:
        failures.append(Failure(3, rel, 1, "cannot verify excludeTools: %s" % err))
    else:
        servers = (data or {}).get("mcpServers") if isinstance(data, dict) else None
        entry = None
        if isinstance(servers, dict):
            entry = servers.get("robinhood")
            if entry is None:
                for v in servers.values():
                    if isinstance(v, dict) and "robinhood" in json.dumps(v).lower():
                        entry = v
                        break
        if not isinstance(entry, dict):
            failures.append(Failure(3, rel, 1, "no Robinhood entry under mcpServers"))
        else:
            excl = entry.get("excludeTools")
            excl_set = set(excl) if isinstance(excl, list) else set()
            line = ctx.line_of(rel, "excludeTools")
            for n in sorted(money_now - excl_set):
                failures.append(Failure(3, rel, line, "excludeTools is missing money tool %s" % n))
            for n in sorted(excl_set - money_now):
                failures.append(
                    Failure(
                        3,
                        rel,
                        line,
                        "excludeTools lists %s, which is not a money tool (the list must equal the money class)" % n,
                    )
                )
    rel = "integrations/codex/hooks.json"
    data, err = ctx.load_json(rel)
    if err:
        failures.append(Failure(3, rel, 1, "cannot verify the Codex guard matcher: %s" % err))
    else:
        matcher = _money_matcher(data, rel, failures)
        if matcher is not None:
            _test_matcher(ctx, rel, matcher, money_now, failures, "Codex")
    # The Codex plugin bundles the same guard (.codex-plugin/plugin.json "hooks"); it must cover the same class.
    rel = ".codex-plugin/hooks.json"
    if ctx.exists(rel):
        data, err = ctx.load_json(rel)
        if err:
            failures.append(Failure(3, rel, 1, "cannot verify the bundled Codex guard matcher: %s" % err))
        else:
            matcher = _money_matcher(data, rel, failures)
            if matcher is not None:
                _test_matcher(ctx, rel, matcher, money_now, failures, "Codex plugin")
    rel = "integrations/claude-code/settings.deny.json"
    if ctx.exists(rel):
        data, err = ctx.load_json(rel)
        deny = ((data or {}).get("permissions") or {}).get("deny") if isinstance(data, dict) else None
        if err or not isinstance(deny, list):
            failures.append(Failure(3, rel, 1, "cannot read permissions.deny: %s" % (err or "missing list")))
        else:
            by_server = {}
            for item in deny:
                m = re.match(r"^mcp__(.+)__([a-z][a-z0-9_]*)$", item) if isinstance(item, str) else None
                if m:
                    by_server.setdefault(m.group(1), set()).add(m.group(2))
            for server, denied in sorted(by_server.items()):
                if denied & money_now:
                    for n in sorted(money_now - denied):
                        failures.append(
                            Failure(
                                3, rel, ctx.line_of(rel, server), "deny list for server %s is missing %s" % (server, n)
                            )
                        )
    return failures


# ---------------------------------------------------------------------------------------------------
# Check 4: param-facts
# ---------------------------------------------------------------------------------------------------


def resolve_path(entry, path):
    """Resolve a dotted schema_path (e.g. inputSchema.properties.span.description) inside a snapshot entry."""
    cur = entry
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None, False
    return cur, True


def check_param_facts(ctx):
    failures = []
    snap = ctx.snapshot_tools()
    if snap is None:
        return [Failure(4, SNAPSHOT_REL, 1, "cannot run the param-facts check: the snapshot is missing or unreadable")]
    data, err = ctx.load_json(FACTS_REL)
    if err:
        return [Failure(4, FACTS_REL, 1, err)]
    facts = data.get("facts") if isinstance(data, dict) else data
    if not isinstance(facts, list):
        return [Failure(4, FACTS_REL, 1, "expected a list of facts or an object with a 'facts' list")]
    if len(facts) < MIN_FACTS:
        failures.append(Failure(4, FACTS_REL, 1, "only %d facts; at least %d are required" % (len(facts), MIN_FACTS)))
    ids = set()
    for i, f in enumerate(facts):
        if not isinstance(f, dict):
            failures.append(Failure(4, FACTS_REL, 1, "facts[%d] is not an object" % i))
            continue
        fid = f.get("id")
        line = ctx.line_of(FACTS_REL, '"id": "%s"' % fid) if isinstance(fid, str) else 1
        missing = [k for k in ("id", "file", "claim_regex", "tool", "schema_path", "must_contain") if k not in f]
        if missing:
            failures.append(
                Failure(4, FACTS_REL, line, "fact %s is missing %s" % (fid or "#%d" % i, ", ".join(missing)))
            )
            continue
        files = f["file"] if isinstance(f["file"], list) else [f["file"]]
        bad_types = [k for k in ("id", "claim_regex", "tool", "schema_path") if not isinstance(f[k], str)]
        if not files or not all(
            isinstance(p, str) and p and not os.path.isabs(p) and ".." not in p.split("/") for p in files
        ):
            bad_types.append("file (a repo-relative path or a list of them)")
        if bad_types:
            failures.append(
                Failure(4, FACTS_REL, line, "fact %s has the wrong type for %s" % (fid, ", ".join(bad_types)))
            )
            continue
        if fid in ids:
            failures.append(Failure(4, FACTS_REL, line, "duplicate fact id %s" % fid))
        ids.add(fid)
        try:
            rx = re.compile(f["claim_regex"])
        except (re.error, TypeError) as exc:
            failures.append(Failure(4, FACTS_REL, line, "fact %s: claim_regex does not compile: %s" % (fid, exc)))
            rx = None
        if rx is not None:
            present = [p for p in files if isinstance(p, str) and ctx.read_text(p) is not None]
            if not present:
                failures.append(
                    Failure(
                        4, FACTS_REL, line, "fact %s: claim file %s not found" % (fid, " or ".join(map(str, files)))
                    )
                )
            elif not any(rx.search(ctx.read_text(p)) for p in present):
                failures.append(
                    Failure(
                        4,
                        present[0],
                        1,
                        "fact %s: claim no longer found (regex %r); update the claim or %s"
                        % (fid, f["claim_regex"], FACTS_REL),
                    )
                )
        tool = f["tool"]
        if tool not in snap:
            failures.append(Failure(4, FACTS_REL, line, "fact %s: tool %s is not in the snapshot" % (fid, tool)))
            continue
        value, ok = resolve_path(snap[tool], f["schema_path"])
        if not ok:
            failures.append(
                Failure(
                    4, FACTS_REL, line, "fact %s: schema_path %s does not exist in %s" % (fid, f["schema_path"], tool)
                )
            )
            continue
        hay = value if isinstance(value, str) else snapshot_tools.canonical_json(value)
        must = f["must_contain"]
        if not isinstance(must, list) or not must:
            failures.append(Failure(4, FACTS_REL, line, "fact %s: must_contain must be a non-empty list" % fid))
            continue
        for s in must:
            if not isinstance(s, str) or s not in hay:
                failures.append(
                    Failure(
                        4, FACTS_REL, line, "fact %s: %s %s no longer contains %r" % (fid, tool, f["schema_path"], s)
                    )
                )
    return failures


# ---------------------------------------------------------------------------------------------------
# Check 5: snapshot integrity and staleness
# ---------------------------------------------------------------------------------------------------


def check_snapshot(ctx):
    failures = []
    data, err = ctx.load_json(SNAPSHOT_REL)
    if err:
        return [Failure(5, SNAPSHOT_REL, 1, err)]
    for code, msg in snapshot_tools.verify_snapshot(data):
        name = msg.split(":", 1)[0]
        failures.append(
            Failure(5, SNAPSHOT_REL, ctx.line_of(SNAPSHOT_REL, '"name": "%s"' % name), "%s: %s" % (code, msg))
        )
    if ctx.staleness is not None and isinstance(data, dict):
        captured = snapshot_tools.parse_date(data.get("captured_at"))
        if captured is not None:
            age = (ctx.today - captured).days
            if age > ctx.staleness:
                failures.append(
                    Failure(
                        5,
                        SNAPSHOT_REL,
                        ctx.line_of(SNAPSHOT_REL, '"captured_at"'),
                        "snapshot captured %s is %d days old (limit %d); refresh it with "
                        "tools/snapshot_tools.py ingest" % (captured.isoformat(), age, ctx.staleness),
                    )
                )
    schemas_dir = ctx.path(SCHEMAS_REL)
    if os.path.isdir(schemas_dir) and isinstance(data, dict) and isinstance(data.get("tools"), list):
        try:
            parsed, _fam, errors = snapshot_tools.parse_schema_dir(schemas_dir)
        except snapshot_tools.SnapshotError as exc:
            parsed, errors = [], [{"msg": exc.msg}]
        for e in errors:
            failures.append(Failure(5, SCHEMAS_REL, 1, e.get("msg", "schema parse error")))
        md = {n: snapshot_tools.make_entry(n, d, s) for (n, d, s) in parsed}
        snap = {t.get("name"): t for t in data["tools"] if isinstance(t, dict)}
        for n in sorted(set(snap) - set(md)):
            failures.append(Failure(5, SCHEMAS_REL, 1, "%s is in the snapshot but not in %s/*.md" % (n, SCHEMAS_REL)))
        for n in sorted(set(md) - set(snap)):
            failures.append(Failure(5, SCHEMAS_REL, 1, "%s is in %s/*.md but not in the snapshot" % (n, SCHEMAS_REL)))
        for n in sorted(set(md) & set(snap)):
            if (md[n]["sha256_description"], md[n]["sha256_schema"]) != (
                snap[n].get("sha256_description"),
                snap[n].get("sha256_schema"),
            ):
                failures.append(
                    Failure(
                        5,
                        SCHEMAS_REL,
                        1,
                        "%s differs between %s/*.md and the snapshot; rebuild with "
                        "tools/snapshot_tools.py from-schemas" % (n, SCHEMAS_REL),
                    )
                )
    return failures


# ---------------------------------------------------------------------------------------------------
# Check 6: no hook emits "allow"
# ---------------------------------------------------------------------------------------------------


def check_hook_allow(ctx):
    """Scan hooks/, optional/ and integrations/. Test directories are skipped: tests legitimately
    contain the forbidden string to assert that it never appears in any hook's output."""
    failures = []
    skip = SKIP_DIRS | frozenset(["tests"])
    for base in ("hooks", "optional", "integrations"):
        for rel in _walk_files(ctx.root, base, skip_dirs=skip):
            text = ctx.read_text(rel)
            if not text:
                continue
            for m in ALLOW_GREP.finditer(text):
                failures.append(
                    Failure(
                        6,
                        rel,
                        text.count("\n", 0, m.start()) + 1,
                        'emits a permission "allow"; no hook in this kit may ever allow a call',
                    )
                )
    return failures


# ---------------------------------------------------------------------------------------------------
# Check 7: synced-copy parity
# ---------------------------------------------------------------------------------------------------


def _without_sync_header(text):
    return "\n".join(ln for ln in text.split("\n") if not SYNC_HEADER.match(ln))


def check_synced_copies(ctx):
    failures = []
    targets = [p for p in _walk_files(ctx.root, "hooks/lib", ".py", recursive=False) if not p.endswith("/__init__.py")]
    data, err = ctx.load_json("shared/manifest.json")
    if not err and isinstance(data, dict):
        for item in data.get("files", []) or []:
            if not isinstance(item, dict):
                continue
            src = item.get("source", "")
            for tgt in item.get("targets", []) or []:
                if isinstance(tgt, str) and tgt.startswith("hooks/lib/") and not ctx.exists(tgt):
                    failures.append(
                        Failure(
                            7,
                            "shared/manifest.json",
                            ctx.line_of("shared/manifest.json", tgt),
                            "%s (copy of %s) is missing; run tools/sync_shared.py" % (tgt, src),
                        )
                    )
    for rel in targets:
        src = "shared/scripts/" + rel.rsplit("/", 1)[1]
        s_text, t_text = ctx.read_text(src), ctx.read_text(rel)
        if s_text is None:
            failures.append(Failure(7, rel, 1, "no source %s for this synced copy" % src))
            continue
        if t_text is None:
            continue
        if s_text == t_text or _without_sync_header(t_text) == _without_sync_header(s_text):
            continue
        failures.append(
            Failure(
                7,
                rel,
                1,
                "differs from %s (sha256 %s vs %s); run tools/sync_shared.py"
                % (src, snapshot_tools.sha256_text(t_text)[:12], snapshot_tools.sha256_text(s_text)[:12]),
            )
        )
    return failures


# ---------------------------------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------------------------------

CHECK_FUNCS = {
    1: check_tool_names,
    2: check_parameters,
    3: check_classes,
    4: check_param_facts,
    5: check_snapshot,
    6: check_hook_allow,
    7: check_synced_copies,
}


def run_checks(root, checks=None, staleness=None, today=None):
    """Run the selected checks; return {check_number: [Failure, ...]}."""
    ctx = Context(root, staleness=staleness, today=today)
    results = {}
    for n in sorted(checks or CHECK_FUNCS):
        results[n] = CHECK_FUNCS[n](ctx)
    return results


def _parse_checks(value):
    out = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or int(part) not in CHECK_FUNCS:
            raise argparse.ArgumentTypeError("checks are numbers 1-7, e.g. --checks 1,3,5")
        out.add(int(part))
    return sorted(out)


def _parse_today(value):
    d = snapshot_tools.parse_date(value)
    if d is None:
        raise argparse.ArgumentTypeError("--today takes YYYY-MM-DD")
    return d


def main(argv=None):
    p = argparse.ArgumentParser(prog="check_drift.py", description="Connector drift checks (spec G.2).")
    p.add_argument("--root", default=DEFAULT_ROOT, help="repository root (default: the parent of tools/)")
    p.add_argument("--checks", type=_parse_checks, default=None, help="comma-separated check numbers (default: all)")
    p.add_argument("--staleness", type=int, default=None, help="fail if the snapshot is older than N days")
    p.add_argument("--today", type=_parse_today, default=None, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    results = run_checks(args.root, args.checks, args.staleness, args.today)
    total = 0
    for n, fails in sorted(results.items()):
        for f in fails:
            sys.stdout.write(str(f) + "\n")
        total += len(fails)
        sys.stderr.write(
            "check %d %-12s %s\n" % (n, CHECK_NAMES[n], "ok" if not fails else "%d failure(s)" % len(fails))
        )
    sys.stderr.write("check_drift: %s\n" % ("all selected checks passed" if not total else "%d failure(s)" % total))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
