#!/usr/bin/env python3
"""Build, verify and refresh the Robinhood Trading MCP tool snapshot.

Unofficial; not affiliated with Robinhood Markets, Inc. Stdlib only, Python 3.9+, no network access.

The snapshot (connector/tools.snapshot.json) is the kit's single record of what the live connector
exposes: every tool's name, verbatim description and verbatim input schema, plus a sha256 of each so
tools/check_drift.py can tell when a hand edit or a partial copy changed them.

Operations
  from-schemas [--schemas-dir DIR] [--raw FILE ...] [--captured-at DATE] [--out FILE] [--check]
      Parse the verbatim captures in connector/schemas/*.md into the snapshot. --raw cross-checks the
      markdown against raw tool dumps (JSON list, JSONL, or an MCP tools/list result) and fails on any
      difference. --check writes nothing and fails if the snapshot on disk differs from the parse.
  ingest TOOLS_LIST [--date DATE] [--captured-at DATE] [--connector-dir DIR] [--dry-run]
      Refresh from a new tools-list dump (for example after the HOOD Summit). Rewrites the snapshot and
      connector/schemas/*.md, appends a dated diff (added / removed / description changed / schema
      changed) to connector/CHANGES.md, and prints the tools whose class must be assigned by hand in
      connector/tool-classes.json. Classes are never assigned automatically: a human decides whether a
      new tool can move money.
  diff TOOLS_LIST [--connector-dir DIR]
      The same comparison as ingest, without writing anything.
  verify [--snapshot FILE]
      Check the tool count, name uniqueness and every hash.

Output is one JSON object on stdout: {"ok": true, ...} or {"ok": false, "errors": [...]}.
Exit codes: 0 when ok, 1 when not ok, 2 on a usage error.

Tools-list input formats accepted by ingest/diff: an MCP tools/list result ({"tools": [...]} or a
JSON-RPC envelope {"result": {"tools": [...]}}), a bare JSON list, or JSON Lines. Each tool needs a
name and a description, plus "inputSchema" (MCP), "parameters" or "input_schema". Client prefixes such
as "mcp__<server>__get_accounts" are stripped to the bare name.
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONNECTOR_DIR = os.path.join(REPO_ROOT, "connector")

SNAPSHOT_FORMAT = 1
SNAPSHOT_SOURCE = (
    "Robinhood Trading MCP connector (https://agent.robinhood.com/mcp/trading): tool list as loaded by "
    "an MCP client. Descriptions and input schemas are verbatim; no tool was called to produce it."
)
HASH_METHOD = (
    "sha256_description = sha256 of the UTF-8 description; sha256_schema = sha256 of the UTF-8 "
    "canonical JSON of inputSchema (json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=False))"
)
TRUNCATION_MARKER = "… [truncated]"
UNCLASSIFIED_FAMILY = "unclassified"

TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
HEADING_TOOL_RE = re.compile(r"^##[ \t]+`?([a-z][a-z0-9_]*)`?[ \t]*$")
HEADING_ANY_RE = re.compile(r"^#{1,2}[ \t]")
FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})[ \t]*([A-Za-z0-9_+-]*)[ \t]*$")
DESC_MARKER_RE = re.compile(r"^(#{2,6}[ \t]*)?Description\b")
INPUT_MARKER_RE = re.compile(r"^(#{2,6}[ \t]*)?Input schema\b")

# Names a new tool is flagged "money-like" by (mirrors the hook layers in the spec, section D.4).
GUARD_LAYER1_RE = re.compile(r"^(place|exercise|replace)_")
MONEY_LIKE_RE = re.compile(
    r"^(place|exercise|replace|submit|execute|transfer|withdraw|deposit|stake|unstake|convert|send|buy|"
    r"sell|trade|liquidate|lend|borrow)_"
)


class SnapshotError(Exception):
    """A problem with an input file that stops the operation."""

    def __init__(self, code, msg):
        Exception.__init__(self, msg)
        self.code = code
        self.msg = msg


# ---------------------------------------------------------------------------------------------------
# Hashing and snapshot construction
# ---------------------------------------------------------------------------------------------------


def canonical_json(obj):
    """Order-insensitive, whitespace-free JSON used for schema hashing and comparison."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_entry(name, description, input_schema):
    entry = {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
        "sha256_description": sha256_text(description),
        "sha256_schema": sha256_text(canonical_json(input_schema)),
    }
    if description.endswith(TRUNCATION_MARKER):
        entry["description_truncated"] = True
    return entry


def build_snapshot(tools, captured_at):
    """tools: iterable of (name, description, inputSchema). Returns the snapshot dict (sorted by name)."""
    entries = [make_entry(n, d, s) for (n, d, s) in tools]
    entries.sort(key=lambda e: e["name"])
    return {
        "format": SNAPSHOT_FORMAT,
        "source": SNAPSHOT_SOURCE,
        "captured_at": captured_at,
        "tool_count": len(entries),
        "hash_method": HASH_METHOD,
        "tools": entries,
    }


def snapshot_json_text(snapshot):
    return json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"


def verify_snapshot(snapshot):
    """Return a list of (code, message) problems; empty when the snapshot is internally consistent."""
    problems = []
    if not isinstance(snapshot, dict):
        return [("BAD_SNAPSHOT", "snapshot is not a JSON object")]
    tools = snapshot.get("tools")
    if not isinstance(tools, list):
        return [("BAD_SNAPSHOT", "snapshot has no 'tools' list")]
    captured = snapshot.get("captured_at")
    if not isinstance(captured, str) or not captured:
        problems.append(("NO_CAPTURED_AT", "snapshot has no captured_at"))
    elif parse_date(captured) is None:
        problems.append(("BAD_CAPTURED_AT", "captured_at %r is not YYYY-MM-DD or ISO 8601" % captured))
    count = snapshot.get("tool_count")
    if not isinstance(count, int) or count != len(tools):
        problems.append(("COUNT_MISMATCH", "tool_count is %r but the snapshot lists %d tools" % (count, len(tools))))
    seen = set()
    for i, t in enumerate(tools):
        if not isinstance(t, dict):
            problems.append(("BAD_ENTRY", "tools[%d] is not an object" % i))
            continue
        name = t.get("name")
        if not isinstance(name, str) or not TOOL_NAME_RE.match(name):
            problems.append(("BAD_NAME", "tools[%d] has an invalid name %r" % (i, name)))
            continue
        if name in seen:
            problems.append(("DUPLICATE", "tool %s appears more than once" % name))
        seen.add(name)
        desc = t.get("description")
        schema = t.get("inputSchema")
        if not isinstance(desc, str):
            problems.append(("BAD_ENTRY", "%s: description is not a string" % name))
            continue
        if not isinstance(schema, dict):
            problems.append(("BAD_ENTRY", "%s: inputSchema is not an object" % name))
            continue
        if t.get("sha256_description") != sha256_text(desc):
            problems.append(("HASH_MISMATCH", "%s: sha256_description does not match the description" % name))
        if t.get("sha256_schema") != sha256_text(canonical_json(schema)):
            problems.append(("HASH_MISMATCH", "%s: sha256_schema does not match inputSchema" % name))
    return problems


def parse_date(value):
    """Parse YYYY-MM-DD or an ISO 8601 timestamp; return a datetime.date or None."""
    if not isinstance(value, str):
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value)
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------------
# Markdown capture parser (connector/schemas/*.md)
# ---------------------------------------------------------------------------------------------------


def _scan_fences(lines):
    """Return (fences, fenced_line_flags). fences: list of dicts {lang, start, end, content}."""
    fences = []
    flags = [False] * len(lines)
    i = 0
    while i < len(lines):
        m = FENCE_OPEN_RE.match(lines[i])
        if not m:
            i += 1
            continue
        marker = m.group(1)
        char, width = marker[0], len(marker)
        j = i + 1
        while j < len(lines):
            s = lines[j].strip()
            if s and len(s) >= width and set(s) == {char}:
                break
            j += 1
        end = min(j, len(lines) - 1)
        for k in range(i, end + 1):
            flags[k] = True
        fences.append({"lang": m.group(2).lower(), "start": i, "end": j, "content": lines[i + 1 : j]})
        i = j + 1
    return fences, flags


def _strip_blank_edges(block):
    start, end = 0, len(block)
    while start < end and not block[start].strip():
        start += 1
    while end > start and not block[end - 1].strip():
        end -= 1
    return block[start:end]


def parse_schema_markdown(text, source="<markdown>"):
    """Parse one capture file. Returns (tools, errors); tools = [(name, description, inputSchema)].

    Recognizes every layout used by the 2026-09-21 captures and by `ingest`: a `## <tool>` heading,
    a description (a ```text fence, a 'Description' marker followed by prose, or prose directly under
    the heading) and the first ```json fence (after an 'Input schema' marker when there is one).
    """
    lines = text.splitlines()
    fences, fenced = _scan_fences(lines)
    headings = []
    for idx, line in enumerate(lines):
        if fenced[idx]:
            continue
        if HEADING_ANY_RE.match(line):
            m = HEADING_TOOL_RE.match(line)
            headings.append((idx, m.group(1) if m else None))
    tools, errors = [], []
    for pos, (start, name) in enumerate(headings):
        if name is None:
            continue
        end = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
        sec_fences = [f for f in fences if start < f["start"] < end]
        input_marker = None
        for k in range(start + 1, end):
            if not fenced[k] and INPUT_MARKER_RE.match(lines[k]):
                input_marker = k
                break
        json_fences = [f for f in sec_fences if f["lang"] == "json"]
        if input_marker is not None:
            after = [f for f in json_fences if f["start"] > input_marker]
            json_fences = after or json_fences
        if not json_fences:
            errors.append({"code": "NO_SCHEMA", "msg": "%s: section %s has no ```json input schema" % (source, name)})
            continue
        jf = json_fences[0]
        try:
            schema = json.loads("\n".join(jf["content"]))
        except ValueError as exc:
            errors.append(
                {"code": "BAD_SCHEMA_JSON", "msg": "%s: %s input schema is not valid JSON: %s" % (source, name, exc)}
            )
            continue
        if not isinstance(schema, dict):
            errors.append({"code": "BAD_SCHEMA_JSON", "msg": "%s: %s input schema is not an object" % (source, name)})
            continue
        text_fences = [f for f in sec_fences if f["lang"] == "text" and f["start"] < jf["start"]]
        if text_fences:
            # A text fence holds the description exactly as served, including any edge whitespace
            # or an empty string, so that ingest -> markdown -> from-schemas round-trips byte for byte.
            tools.append((name, "\n".join(text_fences[0]["content"]), schema))
            continue
        d_start = start + 1
        for k in range(start + 1, jf["start"]):
            if not fenced[k] and DESC_MARKER_RE.match(lines[k]):
                d_start = k + 1
                break
        d_end = jf["start"]
        for k in range(d_start, jf["start"]):
            if not fenced[k] and (INPUT_MARKER_RE.match(lines[k]) or lines[k].startswith("#")):
                d_end = k
                break
        # Prose descriptions: drop the capture's own annotations ("Full name: ..." lines and
        # "> NOTE: ..." blockquotes), which are editorial and not part of the served text.
        desc_lines = [
            ln
            for k, ln in enumerate(lines[d_start:d_end], d_start)
            if not fenced[k] and not re.match(r"^(Full (MCP |tool )?name:|>)", ln)
        ]
        desc_lines = _strip_blank_edges(list(desc_lines))
        description = "\n".join(desc_lines).strip()
        if not description:
            errors.append({"code": "NO_DESCRIPTION", "msg": "%s: section %s has no description" % (source, name)})
            continue
        tools.append((name, description, schema))
    return tools, errors


def parse_schema_dir(schemas_dir):
    """Parse every *.md file in schemas_dir. Returns (tools, family_of, errors)."""
    if not os.path.isdir(schemas_dir):
        raise SnapshotError("NO_SCHEMAS_DIR", "schemas directory not found: %s" % schemas_dir)
    tools, errors, family_of = [], [], {}
    for fname in sorted(os.listdir(schemas_dir)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(schemas_dir, fname)
        with open(path, encoding="utf-8") as fh:
            parsed, errs = parse_schema_markdown(fh.read(), source=fname)
        errors.extend(errs)
        for name, desc, schema in parsed:
            if name in family_of:
                errors.append(
                    {"code": "DUPLICATE", "msg": "%s appears in both %s.md and %s" % (name, family_of[name], fname)}
                )
                continue
            family_of[name] = fname[:-3]
            tools.append((name, desc, schema))
    return tools, family_of, errors


# ---------------------------------------------------------------------------------------------------
# Raw tool-list loading (ingest input, and --raw cross-checks)
# ---------------------------------------------------------------------------------------------------


def bare_name(name):
    """'mcp__<server>__get_accounts' -> 'get_accounts'; bare names pass through."""
    if name.startswith("mcp__") and "__" in name[5:]:
        return name.rsplit("__", 1)[1]
    return name


def _objects_from_json(data):
    if isinstance(data, dict):
        if isinstance(data.get("result"), dict) and "tools" in data["result"]:
            data = data["result"]
        if isinstance(data.get("tools"), list):
            return data["tools"]
        if "name" in data:
            return [data]
        raise SnapshotError("BAD_TOOLS_LIST", "JSON object has no 'tools' list")
    if isinstance(data, list):
        return data
    raise SnapshotError("BAD_TOOLS_LIST", "expected a JSON list or an object with a 'tools' list")


def load_tools_list(path):
    """Load and normalize a tools-list dump. Returns [(bare_name, description, inputSchema)]."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise SnapshotError("NO_TOOLS_LIST", "cannot read %s: %s" % (path, exc)) from None
    try:
        objs = _objects_from_json(json.loads(raw))
    except ValueError:
        objs = []
        for n, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                objs.append(json.loads(line))
            except ValueError as exc:
                raise SnapshotError("BAD_TOOLS_LIST", "%s line %d is not JSON: %s" % (path, n, exc)) from None
    return normalize_tools(objs, source=path)


def normalize_tools(objs, source="<tools>"):
    out, seen = [], set()
    for i, o in enumerate(objs):
        if not isinstance(o, dict):
            raise SnapshotError("BAD_TOOLS_LIST", "%s: item %d is not an object" % (source, i))
        name = o.get("name")
        if not isinstance(name, str) or not name:
            raise SnapshotError("BAD_TOOLS_LIST", "%s: item %d has no name" % (source, i))
        name = bare_name(name)
        if not TOOL_NAME_RE.match(name):
            raise SnapshotError("BAD_TOOLS_LIST", "%s: %r is not a valid tool name" % (source, name))
        if name in seen:
            raise SnapshotError("BAD_TOOLS_LIST", "%s: %s appears more than once" % (source, name))
        seen.add(name)
        desc = o.get("description", "")
        if not isinstance(desc, str):
            raise SnapshotError("BAD_TOOLS_LIST", "%s: %s description is not a string" % (source, name))
        schema = None
        for key in ("inputSchema", "parameters", "input_schema"):
            if key in o:
                schema = o[key]
                break
        if not isinstance(schema, dict):
            raise SnapshotError("BAD_TOOLS_LIST", "%s: %s has no inputSchema object" % (source, name))
        out.append((name, desc, schema))
    if not out:
        raise SnapshotError("EMPTY_TOOLS_LIST", "%s contains no tools" % source)
    return out


# ---------------------------------------------------------------------------------------------------
# Diff, CHANGES.md and regenerated schema markdown
# ---------------------------------------------------------------------------------------------------


def _entries_by_name(snapshot):
    return {t["name"]: t for t in (snapshot or {}).get("tools", []) if isinstance(t, dict) and "name" in t}


def _first_difference(a, b, context=60):
    n = 0
    limit = min(len(a), len(b))
    while n < limit and a[n] == b[n]:
        n += 1
    lo = max(0, n - 20)
    return n, a[lo : n + context], b[lo : n + context]


def _schema_changes(old, new):
    changes = {}
    op, np_ = old.get("properties", {}) or {}, new.get("properties", {}) or {}
    added = sorted(set(np_) - set(op))
    removed = sorted(set(op) - set(np_))
    changed = sorted(k for k in set(op) & set(np_) if canonical_json(op[k]) != canonical_json(np_[k]))
    oreq, nreq = set(old.get("required", []) or []), set(new.get("required", []) or [])
    if added:
        changes["params_added"] = added
    if removed:
        changes["params_removed"] = removed
    if changed:
        changes["params_changed"] = changed
    if sorted(nreq - oreq):
        changes["required_added"] = sorted(nreq - oreq)
    if sorted(oreq - nreq):
        changes["required_removed"] = sorted(oreq - nreq)
    other = sorted(
        k
        for k in set(old) | set(new)
        if k not in ("properties", "required") and canonical_json(old.get(k)) != canonical_json(new.get(k))
    )
    if other:
        changes["other_keys_changed"] = other
    return changes


def suggest_class(name, description):
    """A hint for the human doing the classification; never written into tool-classes.json."""
    if GUARD_LAYER1_RE.match(name) or "real money" in description.lower():
        return "money"
    if MONEY_LIKE_RE.match(name):
        return "money"
    if name.startswith(("review_", "preview_")):
        return "simulate"
    if name.startswith(("cancel_", "delete_")):
        return "cancel"
    if name.endswith(("_upgrade_info", "_onboarding_info")):
        return "enroll_link"
    if name.startswith(("create_", "update_", "add_", "remove_", "follow_", "unfollow_", "mark_")):
        return "write_confirm"
    if name.startswith(("get_", "run_")) or name == "search":
        return "read"
    return "decide by hand"


def suggest_account_param(schema):
    props = schema.get("properties", {}) or {}
    if "rhs_account_number" in props:
        return "rhs_account_number"
    if "account_number" in props:
        desc = (props["account_number"] or {}).get("description", "")
        return "account_number=rhs_value" if "rhs_account_number" in desc else "account_number"
    return None


def suggest_paginates(schema):
    props = schema.get("properties", {}) or {}
    cursor = (props.get("cursor") or {}).get("description", "")
    if cursor:
        if "next_cursor" in cursor:
            return "next_cursor"
        if "next URL" in cursor:
            return "next_url"
        if "next field" in cursor or "next`" in cursor:
            return "next"
        return "unknown (read the cursor description)"
    offset = (props.get("offset") or {}).get("description", "")
    if offset and "next_offset" in offset:
        return "next_offset"
    return None


def diff_snapshots(old_snapshot, new_tools):
    """Compare the current snapshot with a new normalized tools list."""
    old = _entries_by_name(old_snapshot)
    new = {n: make_entry(n, d, s) for (n, d, s) in new_tools}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    desc_changed, schema_changed = [], []
    for name in sorted(set(old) & set(new)):
        o, n = old[name], new[name]
        if o.get("description") != n["description"]:
            pos, before, after = _first_difference(o.get("description", ""), n["description"])
            desc_changed.append(
                {
                    "name": name,
                    "old_length": len(o.get("description", "")),
                    "new_length": len(n["description"]),
                    "first_difference_at": pos,
                    "old_excerpt": before,
                    "new_excerpt": after,
                    "was_truncated": bool(o.get("description", "").endswith(TRUNCATION_MARKER)),
                    "now_truncated": n["description"].endswith(TRUNCATION_MARKER),
                }
            )
        if canonical_json(o.get("inputSchema", {})) != canonical_json(n["inputSchema"]):
            schema_changed.append(
                {"name": name, "changes": _schema_changes(o.get("inputSchema", {}) or {}, n["inputSchema"])}
            )
    needs = []
    for name in added:
        e = new[name]
        needs.append(
            {
                "name": name,
                "suggested_class": suggest_class(name, e["description"]),
                "suggested_account_param": suggest_account_param(e["inputSchema"]),
                "suggested_paginates": suggest_paginates(e["inputSchema"]),
                "money_like": bool(MONEY_LIKE_RE.match(name) or "real money" in e["description"].lower()),
                "guard_layer1_covers": bool(GUARD_LAYER1_RE.match(name)),
            }
        )
    return {
        "added": added,
        "removed": removed,
        "description_changed": desc_changed,
        "schema_changed": schema_changed,
        "needs_classification": needs,
        "remove_from_classes": removed,
        "new_money_like": [x["name"] for x in needs if x["money_like"]],
        "old_count": len(old),
        "new_count": len(new),
    }


def _fmt_list(names):
    return ", ".join("`%s`" % n for n in names)


def render_changes_entry(diff, date, captured_at, source_label):
    lines = []
    lines.append(
        "## %s: snapshot refresh (captured %s; %d tools, previously %d)"
        % (date, captured_at, diff["new_count"], diff["old_count"])
    )
    lines.append("")
    lines.append("Source: `%s`, ingested with `tools/snapshot_tools.py ingest`." % source_label)
    lines.append("")
    if not (diff["added"] or diff["removed"] or diff["description_changed"] or diff["schema_changed"]):
        lines.append("No tool changes: every name, description and input schema matches the previous snapshot.")
        lines.append("")
        return "\n".join(lines)
    needs = {x["name"]: x for x in diff["needs_classification"]}
    if diff["added"]:
        lines.append("### Added (%d)" % len(diff["added"]))
        for name in diff["added"]:
            x = needs.get(name, {})
            note = ""
            if x.get("money_like"):
                note = " **Money-like name or description: classify before merging.** Guard layer 1 %s it." % (
                    "covers" if x.get("guard_layer1_covers") else "does NOT cover"
                )
            lines.append("- `%s` (suggested class: %s)%s" % (name, x.get("suggested_class", "decide by hand"), note))
        lines.append("")
    if diff["removed"]:
        lines.append("### Removed (%d)" % len(diff["removed"]))
        for name in diff["removed"]:
            lines.append("- `%s`" % name)
        lines.append("")
    if diff["description_changed"]:
        lines.append("### Description changed (%d)" % len(diff["description_changed"]))
        for d in diff["description_changed"]:
            extra = ""
            if d["was_truncated"] and not d["now_truncated"]:
                extra = " The previous capture was truncated at the source; this one is not."
            elif d["now_truncated"]:
                extra = " This capture is truncated at the source."
            lines.append(
                "- `%s` (%d to %d characters; first difference at character %d).%s"
                % (d["name"], d["old_length"], d["new_length"], d["first_difference_at"], extra)
            )
            lines.append("  - before: %s" % json.dumps(d["old_excerpt"], ensure_ascii=False))
            lines.append("  - after: %s" % json.dumps(d["new_excerpt"], ensure_ascii=False))
        lines.append("")
    if diff["schema_changed"]:
        lines.append("### Input schema changed (%d)" % len(diff["schema_changed"]))
        labels = [
            ("params_added", "parameters added"),
            ("params_removed", "parameters removed"),
            ("params_changed", "parameters changed"),
            ("required_added", "now required"),
            ("required_removed", "no longer required"),
            ("other_keys_changed", "other schema keys changed"),
        ]
        for s in diff["schema_changed"]:
            parts = ["%s %s" % (label, _fmt_list(s["changes"][key])) for key, label in labels if s["changes"].get(key)]
            lines.append("- `%s`: %s" % (s["name"], "; ".join(parts) or "formatting-only change"))
        lines.append("")
    if diff["added"] or diff["removed"]:
        lines.append("### To do by hand in `connector/tool-classes.json`")
        if diff["added"]:
            lines.append("- Classify: %s" % _fmt_list(diff["added"]))
        if diff["removed"]:
            lines.append("- Remove: %s" % _fmt_list(diff["removed"]))
        lines.append("- Then run `python3 tools/check_drift.py` and fix every rule, skill and eval it flags.")
        lines.append("")
    return "\n".join(lines)


def _fence_for(text):
    longest = max([len(m) for m in re.findall(r"`+", text)] or [0])
    return "`" * max(3, longest + 1)


def render_family_markdown(family, entries, captured_at):
    names = [e["name"] for e in entries]
    out = []
    out.append("# Robinhood Trading MCP: %s (verbatim schemas)" % family)
    out.append("")
    out.append(
        "Regenerated by `tools/snapshot_tools.py ingest` from a tools list captured %s. Each description "
        "(in a `text` fence) and input schema (in a `json` fence) is reproduced verbatim, with schema key "
        "order as served. No tool was called to produce this file." % captured_at
    )
    if family == UNCLASSIFIED_FAMILY:
        out.append("")
        out.append(
            "These tools are new and have no entry in `connector/tool-classes.json` yet. Assign each one a "
            "class and a family by hand, then run `tools/snapshot_tools.py ingest` again to file it."
        )
    if any(e["description"].endswith(TRUNCATION_MARKER) for e in entries):
        out.append("")
        out.append(
            "Descriptions that end in `%s` were cut off by the client that produced the dump; the rest of "
            "that text is not available from the tool list." % TRUNCATION_MARKER
        )
    out.append("")
    out.append("Tools (%d): %s" % (len(names), _fmt_list(names)))
    for e in entries:
        fence = _fence_for(e["description"])
        out.append("")
        out.append("## %s" % e["name"])
        out.append("")
        out.append("### Description (verbatim)")
        out.append("")
        out.append(fence + "text")
        out.append(e["description"])
        out.append(fence)
        out.append("")
        out.append("### Input schema (verbatim)")
        out.append("")
        out.append("```json")
        out.append(json.dumps(e["inputSchema"], indent=2, ensure_ascii=False))
        out.append("```")
    return "\n".join(out) + "\n"


def load_tool_families(classes_path):
    """Return {tool: family} from tool-classes.json (list form or {"tools": [...]}); {} if absent."""
    if not os.path.exists(classes_path):
        return {}
    with open(classes_path, encoding="utf-8") as fh:
        data = json.load(fh)
    tools = data.get("tools", []) if isinstance(data, dict) else data
    return {t["name"]: t.get("family") for t in tools if isinstance(t, dict) and "name" in t and t.get("family")}


def regenerate_schema_dir(schemas_dir, snapshot, families):
    """Rewrite schemas_dir/*.md from the snapshot, one file per family. Returns the files written."""
    groups = {}
    for e in snapshot["tools"]:
        fam = families.get(e["name"]) or UNCLASSIFIED_FAMILY
        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", fam):
            fam = UNCLASSIFIED_FAMILY
        groups.setdefault(fam, []).append(e)
    os.makedirs(schemas_dir, exist_ok=True)
    written = []
    for fam in sorted(groups):
        path = os.path.join(schemas_dir, fam + ".md")
        _write_text(path, render_family_markdown(fam, groups[fam], snapshot["captured_at"]))
        written.append(path)
    keep = set(os.path.basename(p) for p in written)
    for fname in os.listdir(schemas_dir):
        if fname.endswith(".md") and fname not in keep:
            os.remove(os.path.join(schemas_dir, fname))
    return written


# ---------------------------------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------------------------------


def _write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def load_snapshot(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        raise SnapshotError("NO_SNAPSHOT", "cannot read %s: %s" % (path, exc)) from None
    except ValueError as exc:
        raise SnapshotError("BAD_SNAPSHOT", "%s is not valid JSON: %s" % (path, exc)) from None


def append_changes(changes_path, entry):
    existing = ""
    if os.path.exists(changes_path):
        with open(changes_path, encoding="utf-8") as fh:
            existing = fh.read()
    else:
        existing = "# Connector changes\n"
    if not existing.endswith("\n"):
        existing += "\n"
    _write_text(changes_path, existing + "\n" + entry)


def _today():
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def _valid_date_arg(value):
    if value is None:
        return None
    if parse_date(value) is None:
        raise SnapshotError("BAD_DATE", "%r is not a date (YYYY-MM-DD)" % value)
    return value


# ---------------------------------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------------------------------


def op_from_schemas(args):
    schemas_dir = args.schemas_dir or os.path.join(DEFAULT_CONNECTOR_DIR, "schemas")
    out_path = args.out or os.path.join(DEFAULT_CONNECTOR_DIR, "tools.snapshot.json")
    tools, _families, errors = parse_schema_dir(schemas_dir)
    captured = _valid_date_arg(args.captured_at)
    if captured is None and os.path.exists(out_path):
        captured = load_snapshot(out_path).get("captured_at")
    if not captured:
        errors.append({"code": "NO_CAPTURED_AT", "msg": "pass --captured-at (no existing snapshot to take it from)"})
    crosschecked = 0
    for raw_path in args.raw or []:
        by_name = {n: (d, s) for (n, d, s) in tools}
        for name, desc, schema in load_tools_list(raw_path):
            if name not in by_name:
                errors.append(
                    {"code": "RAW_ONLY", "msg": "%s is in %s but not in the markdown captures" % (name, raw_path)}
                )
                continue
            md_desc, md_schema = by_name[name]
            if md_desc != desc:
                pos, before, after = _first_difference(md_desc, desc)
                errors.append(
                    {
                        "code": "RAW_MISMATCH",
                        "msg": "%s: markdown description differs from %s at character %d: %r vs %r"
                        % (name, os.path.basename(raw_path), pos, before, after),
                    }
                )
            if canonical_json(md_schema) != canonical_json(schema):
                errors.append(
                    {
                        "code": "RAW_MISMATCH",
                        "msg": "%s: markdown input schema differs from %s" % (name, os.path.basename(raw_path)),
                    }
                )
            if list(md_schema.get("properties", {}) or {}) != list(schema.get("properties", {}) or {}):
                errors.append(
                    {
                        "code": "RAW_ORDER",
                        "msg": "%s: property order differs from %s" % (name, os.path.basename(raw_path)),
                    }
                )
            crosschecked += 1
    if errors:
        return {"ok": False, "errors": errors}
    snapshot = build_snapshot(tools, captured)
    text = snapshot_json_text(snapshot)
    result = {"ok": True, "tools": snapshot["tool_count"], "captured_at": captured, "raw_crosschecked": crosschecked}
    if args.check:
        current = ""
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as fh:
                current = fh.read()
        if current != text:
            return {
                "ok": False,
                "errors": [
                    {
                        "code": "SNAPSHOT_STALE",
                        "msg": "%s does not match the parse of %s; run from-schemas" % (out_path, schemas_dir),
                    }
                ],
            }
        result["checked"] = out_path
        return result
    _write_text(out_path, text)
    result["written"] = out_path
    return result


def op_ingest(args, dry_run=False):
    connector_dir = args.connector_dir or DEFAULT_CONNECTOR_DIR
    snapshot_path = os.path.join(connector_dir, "tools.snapshot.json")
    classes_path = os.path.join(connector_dir, "tool-classes.json")
    changes_path = os.path.join(connector_dir, "CHANGES.md")
    schemas_dir = os.path.join(connector_dir, "schemas")
    date = _valid_date_arg(getattr(args, "date", None)) or _today()
    captured = _valid_date_arg(getattr(args, "captured_at", None)) or date
    new_tools = load_tools_list(args.tools_list)
    old_snapshot = load_snapshot(snapshot_path) if os.path.exists(snapshot_path) else {"tools": []}
    diff = diff_snapshots(old_snapshot, new_tools)
    result = {"ok": True, "date": date, "captured_at": captured, "dry_run": bool(dry_run)}
    result.update(diff)
    if dry_run:
        return result
    snapshot = build_snapshot(new_tools, captured)
    _write_text(snapshot_path, snapshot_json_text(snapshot))
    families = load_tool_families(classes_path)
    written = regenerate_schema_dir(schemas_dir, snapshot, families)
    append_changes(changes_path, render_changes_entry(diff, date, captured, os.path.basename(args.tools_list)))
    result["written"] = [snapshot_path, changes_path] + written
    if diff["needs_classification"] or diff["remove_from_classes"]:
        result["next_step"] = (
            "Edit connector/tool-classes.json by hand: classify %d new tool(s), remove %d; "
            "then run python3 tools/check_drift.py."
            % (len(diff["needs_classification"]), len(diff["remove_from_classes"]))
        )
    return result


def op_verify(args):
    path = args.snapshot or os.path.join(DEFAULT_CONNECTOR_DIR, "tools.snapshot.json")
    problems = verify_snapshot(load_snapshot(path))
    if problems:
        return {"ok": False, "errors": [{"code": c, "msg": m} for (c, m) in problems]}
    snap = load_snapshot(path)
    return {"ok": True, "tools": snap["tool_count"], "captured_at": snap["captured_at"]}


def build_parser():
    p = argparse.ArgumentParser(prog="snapshot_tools.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="op")
    a = sub.add_parser("from-schemas", help="build the snapshot from connector/schemas/*.md")
    a.add_argument("--schemas-dir")
    a.add_argument("--raw", nargs="*", help="raw tool dumps to cross-check the markdown against")
    a.add_argument("--captured-at")
    a.add_argument("--out")
    a.add_argument("--check", action="store_true", help="write nothing; fail if the snapshot on disk differs")
    b = sub.add_parser("ingest", help="refresh from a new tools-list dump")
    b.add_argument("tools_list")
    b.add_argument("--date", help="date of this refresh for CHANGES.md (default: today, UTC)")
    b.add_argument("--captured-at", help="capture date of the dump (default: --date)")
    b.add_argument("--connector-dir")
    b.add_argument("--dry-run", action="store_true")
    c = sub.add_parser("diff", help="compare a tools-list dump with the snapshot; writes nothing")
    c.add_argument("tools_list")
    c.add_argument("--connector-dir")
    v = sub.add_parser("verify", help="check the snapshot's count and hashes")
    v.add_argument("--snapshot")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.op:
        parser.print_help(sys.stderr)
        return 2
    try:
        if args.op == "from-schemas":
            result = op_from_schemas(args)
        elif args.op == "ingest":
            result = op_ingest(args, dry_run=args.dry_run)
        elif args.op == "diff":
            result = op_ingest(args, dry_run=True)
        else:
            result = op_verify(args)
    except SnapshotError as exc:
        result = {"ok": False, "errors": [{"code": exc.code, "msg": exc.msg}]}
    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
