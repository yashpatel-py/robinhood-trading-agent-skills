#!/usr/bin/env python3
"""gen_rules_tables.py - render the generated R4 and R15 blocks of shared/connector-rules.md.

Unofficial; not affiliated with Robinhood Markets, Inc. Stdlib only, Python 3.9+, no network access.

Two parts of the connector rules must never drift from the connector's real tool list, so they are
rendered from data instead of typed by hand:

  R4   the account-number key and value each tool takes (connector/tool-classes.json `account_param`,
       with "(optional)" when the snapshot schema does not require the account parameter)
  R15  the six tool classes, the tools referenced but absent, the tools older docs got wrong, the count
       missing from Robinhood's published list, and the families that may be disabled per account

Each block sits between a `<!-- BEGIN generated:r4 ... -->` line and a `<!-- END generated:r4 -->` line.
Only the lines between the markers are replaced; the rest of the file is hand-written.

connector/param-facts.json pins claims in the rules file to the live schemas. Before writing, this tool
checks that regenerating the blocks does not break a claim that holds today, so a table change can't
silently remove text a fact depends on (tools/check_drift.py check 4 covers the rest of the file).

Usage:
  python3 tools/gen_rules_tables.py            rewrite the blocks in place
  python3 tools/gen_rules_tables.py --check    write nothing; print a diff and exit 1 if out of date
  options: --root DIR (repository root; default: the parent of tools/)

Exit 0 when the file is (or now is) up to date, 1 when it is out of date or a check failed, 2 on a
usage or input error.
"""

import argparse
import difflib
import json
import os
import re
import sys

DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RULES_REL = "shared/connector-rules.md"
CLASSES_REL = "connector/tool-classes.json"
SNAPSHOT_REL = "connector/tools.snapshot.json"
FACTS_REL = "connector/param-facts.json"

BLOCKS = ("r4", "r15")

# R4 rows, in display order: (account_param value in tool-classes.json, first cell).
# tools/check_drift.py parses the first cell ("key `x`" plus "rhs" for the rhs value, or "no account").
R4_ROWS = (
    ("account_number", "key `account_number` ← the alphanumeric `account_number`"),
    ("account_number=rhs_value", "key `account_number` ← **the `rhs_account_number` VALUE**"),
    ("rhs_account_number", "key `rhs_account_number` ← `rhs_account_number`"),
    (None, "no account parameter (profile-wide or market data)"),
)
PARAM_FOR = {
    "account_number": "account_number",
    "account_number=rhs_value": "account_number",
    "rhs_account_number": "rhs_account_number",
}
# Lifecycle verbs whose tools share a suffix and are shown as one token: review_/place_/cancel_equity_order.
COMPRESS_VERBS = ("review", "preview", "place", "cancel")
# Words for each schema family in prose; a family may contribute more than one word to a list.
FAMILY_LABELS = {
    "accounts": ("account",),
    "marketdata": ("market-data",),
    "research": ("research",),
    "scanner": ("scanner",),
    "watchlists-alerts": ("watchlist", "alert"),
    "orders-equity-advanced": ("equity order", "OCO order"),
    "orders-options-crypto": ("option order", "crypto order"),
}
# In the no-account row, a family with at most this many account-free tools lists them by name;
# a larger family is summarized in words (its account-taking tools are already named in other rows).
LIST_LIMIT = 3


class InputError(Exception):
    """A missing or malformed input file (exit 2)."""


# ---------------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------------


def _load_json(root, rel):
    path = os.path.join(root, rel)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        raise InputError("%s: cannot read (%s)" % (rel, exc.strerror or exc)) from None
    except ValueError as exc:
        raise InputError("%s: not valid JSON (%s)" % (rel, exc)) from None


def load_classes(root):
    data = _load_json(root, CLASSES_REL)
    if not isinstance(data, dict) or not isinstance(data.get("tools"), list):
        raise InputError("%s: expected an object with a 'tools' list" % CLASSES_REL)
    seen = set()
    for i, t in enumerate(data["tools"]):
        if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not t["name"]:
            raise InputError("%s: tools[%d] has no name" % (CLASSES_REL, i))
        if t["name"] in seen:
            raise InputError("%s: duplicate tool %s" % (CLASSES_REL, t["name"]))
        seen.add(t["name"])
        if not isinstance(t.get("class"), str):
            raise InputError("%s: %s has no class" % (CLASSES_REL, t["name"]))
        if t.get("account_param") not in PARAM_FOR and t.get("account_param") is not None:
            raise InputError("%s: %s has unknown account_param %r" % (CLASSES_REL, t["name"], t.get("account_param")))
    classes = data.get("classes")
    if not isinstance(classes, dict) or not classes:
        raise InputError("%s: expected a 'classes' object describing each class" % CLASSES_REL)
    for t in data["tools"]:
        if t["class"] not in classes:
            raise InputError(
                "%s: %s has class %r, which 'classes' does not describe" % (CLASSES_REL, t["name"], t["class"])
            )
    return data


def load_required(root):
    """{tool name: set of required parameter names} from the snapshot."""
    data = _load_json(root, SNAPSHOT_REL)
    tools = data.get("tools") if isinstance(data, dict) else data
    if not isinstance(tools, list):
        raise InputError("%s: expected a 'tools' list" % SNAPSHOT_REL)
    out = {}
    for t in tools:
        if isinstance(t, dict) and isinstance(t.get("name"), str):
            schema = t.get("inputSchema") or {}
            out[t["name"]] = set(schema.get("required") or [])
    return out


def load_rule_facts(root):
    """The param-facts whose claim file is the rules file: [(id, compiled regex)]."""
    path = os.path.join(root, FACTS_REL)
    if not os.path.exists(path):
        return []
    data = _load_json(root, FACTS_REL)
    facts = data.get("facts") if isinstance(data, dict) else data
    out = []
    for f in facts if isinstance(facts, list) else []:
        if not isinstance(f, dict):
            continue
        files = f.get("file") if isinstance(f.get("file"), list) else [f.get("file")]
        if RULES_REL not in files or not isinstance(f.get("claim_regex"), str):
            continue
        try:
            out.append((str(f.get("id")), re.compile(f["claim_regex"])))
        except re.error:
            continue  # tools/check_drift.py reports regexes that don't compile
    return out


# ---------------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------------


def _split_verb(name):
    for verb in COMPRESS_VERBS:
        if name.startswith(verb + "_") and len(name) > len(verb) + 1:
            return verb, name[len(verb) + 1 :]
    return None, name


def _is_optional(tool, required):
    param = PARAM_FOR.get(tool.get("account_param"))
    if param is None or tool["name"] not in required:
        return False
    return param not in required[tool["name"]]


def row_tokens(tools, required):
    """Display tokens for one R4 row, grouping review_/place_/cancel_ siblings that share a suffix."""
    groups, order = {}, []
    for t in tools:
        verb, suffix = _split_verb(t["name"])
        optional = _is_optional(t, required)
        key = ("group", suffix, optional) if verb else ("one", t["name"], optional)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)
    tokens = []
    for key in order:
        members = groups[key]
        if key[0] == "group" and len(members) > 1:
            members = sorted(members, key=lambda m: COMPRESS_VERBS.index(_split_verb(m["name"])[0]))
            verbs = [_split_verb(m["name"])[0] for m in members]
            token = "/".join([v + "_" for v in verbs[:-1]] + [members[-1]["name"]])
            tokens.append("`%s`%s" % (token, " (optional)" if key[2] else ""))
        else:
            for m in members:
                tokens.append("`%s`%s" % (m["name"], " (optional)" if key[2] else ""))
    return tokens


def _join_words(words):
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def null_row_text(tools):
    """Second cell of the no-account row: names for small families, words for large ones."""
    families, order = {}, []
    for t in tools:
        fam = t.get("family") or "other"
        if fam not in families:
            families[fam] = {"all": [], "null": []}
            order.append(fam)
        families[fam]["all"].append(t)
        if t.get("account_param") is None:
            families[fam]["null"].append(t)
    named, other, every = [], [], []
    for fam in order:
        nulls, total = families[fam]["null"], families[fam]["all"]
        if not nulls:
            continue
        words = FAMILY_LABELS.get(fam, (fam,))
        if len(nulls) == len(total):
            every.extend(words)
        elif len(nulls) <= LIST_LIMIT:
            named.extend("`%s`" % t["name"] for t in nulls)
        else:
            other.append("the other %s tools" % _join_words(list(words)))
    parts = []
    if named:
        parts.append(", ".join(named))
    parts.extend(other)
    if every:
        parts.append("every %s tool" % _join_words(every))
    return "; ".join(parts)


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def render_r4(data, required):
    tools = data["tools"]
    lines = ["| Key ← value | Tools |", "|---|---|"]
    for param, first_cell in R4_ROWS:
        members = [t for t in tools if t.get("account_param") == param]
        if not members:
            continue
        if param is None:
            second = null_row_text(tools)
        else:
            second = ", ".join(row_tokens(members, required))
        lines.append("| %s | %s |" % (first_cell, second))
    return lines


def render_r15(data):
    tools = data["tools"]
    classes = data["classes"]
    snap_date = data.get("snapshot_captured_at") or "unknown date"
    lines = [
        "All %d tools in the connector snapshot (captured %s), by class:" % (len(tools), snap_date),
        "",
        "| Class | Count | How to treat it | Tools |",
        "|---|---|---|---|",
    ]
    for cls, desc in classes.items():
        members = [t["name"] for t in tools if t["class"] == cls]
        if not members:
            continue
        lines.append(
            "| `%s` | %d | %s | %s |" % (cls, len(members), _cell(desc), ", ".join("`%s`" % n for n in members))
        )
    lines.append("")
    absent = [a.get("name") for a in data.get("absent_referenced") or [] if isinstance(a, dict) and a.get("name")]
    if absent:
        lines.append(
            "- Absent though referenced, not exposed (never call them): %s." % ", ".join("`%s`" % n for n in absent)
        )
    older = (data.get("older_docs_disagree") or {}).get("tools") or []
    if older:
        lines.append("- Live although older docs called them unavailable: %s." % ", ".join("`%s`" % n for n in older))
    unpublished = (data.get("not_in_published_list") or {}).get("tools") or []
    if unpublished:
        lines.append(
            "- %d live tools are missing from Robinhood's published tool list (listed in `%s`)."
            % (len(unpublished), CLASSES_REL)
        )
    for note in data.get("availability_notes") or []:
        if not isinstance(note, dict) or not note.get("tools"):
            continue
        lines.append(
            "- Listed but may be disabled for an account (observed %s; R26): %s."
            % (note.get("observed") or "date unknown", ", ".join("`%s`" % n for n in note["tools"]))
        )
    return lines


def render_blocks(data, required):
    return {"r4": render_r4(data, required), "r15": render_r15(data)}


# ---------------------------------------------------------------------------------------------------
# Splicing
# ---------------------------------------------------------------------------------------------------


def _begin_re(key):
    return re.compile(r"^<!-- BEGIN generated:%s\b" % re.escape(key))


def _end_line(key):
    return "<!-- END generated:%s -->" % key


def splice(text, blocks):
    """Return text with each block's lines replaced; raise InputError when markers are missing."""
    lines = text.split("\n")
    for key, body in blocks.items():
        begin = [i for i, ln in enumerate(lines) if _begin_re(key).match(ln.strip())]
        end = [i for i, ln in enumerate(lines) if ln.strip() == _end_line(key)]
        if len(begin) != 1 or len(end) != 1:
            raise InputError(
                "%s: need exactly one '<!-- BEGIN generated:%s ... -->' line and one '%s' line (found %d and %d)"
                % (RULES_REL, key, _end_line(key), len(begin), len(end))
            )
        b, e = begin[0], end[0]
        if e < b:
            raise InputError("%s: the END marker for %s comes before its BEGIN marker" % (RULES_REL, key))
        lines = lines[: b + 1] + list(body) + lines[e:]
    return "\n".join(lines)


def broken_facts(facts, before, after):
    """Fact ids that match the current file but would not match the regenerated one."""
    return [fid for fid, rx in facts if rx.search(before) and not rx.search(after)]


def run(root, check=False, out=sys.stdout, err=sys.stderr):
    path = os.path.join(root, RULES_REL)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            current = fh.read()
        data = load_classes(root)
        required = load_required(root)
        facts = load_rule_facts(root)
        rendered = splice(current, render_blocks(data, required))
    except OSError as exc:
        err.write("gen_rules_tables: %s: cannot read (%s)\n" % (RULES_REL, exc.strerror or exc))
        return 2
    except InputError as exc:
        err.write("gen_rules_tables: %s\n" % exc)
        return 2
    status = 0
    broken = broken_facts(facts, current, rendered)
    for fid in broken:
        err.write(
            "gen_rules_tables: regenerating would break param-fact %s (%s); update the hand-written text or the fact\n"
            % (fid, FACTS_REL)
        )
        status = 1
    if rendered == current:
        if not check:
            out.write("gen_rules_tables: %s is up to date\n" % RULES_REL)
        return status
    if check:
        diff = difflib.unified_diff(
            current.splitlines(True), rendered.splitlines(True), "a/" + RULES_REL, "b/" + RULES_REL
        )
        out.writelines(diff)
        err.write("gen_rules_tables: %s is out of date; run python3 tools/gen_rules_tables.py\n" % RULES_REL)
        return 1
    if broken:
        err.write("gen_rules_tables: not writing %s\n" % RULES_REL)
        return status
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(rendered)
    out.write("gen_rules_tables: updated %s\n" % RULES_REL)
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render the generated R4 and R15 blocks of %s." % RULES_REL)
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 with a diff when out of date")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="repository root (default: %(default)s)")
    args = parser.parse_args(argv)
    return run(os.path.abspath(args.root), check=args.check)


if __name__ == "__main__":
    sys.exit(main())
