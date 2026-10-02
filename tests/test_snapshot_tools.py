"""Tests for tools/snapshot_tools.py (WP-A). Stdlib unittest; run with python3 -m unittest discover -s tests."""

import contextlib
import copy
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONNECTOR = os.path.join(REPO, "connector")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


st = _load("snapshot_tools", os.path.join(REPO, "tools", "snapshot_tools.py"))


def run_cli(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = st.main(argv)
    return code, json.loads(out.getvalue())


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def real_snapshot():
    return read_json(os.path.join(CONNECTOR, "tools.snapshot.json"))


TEXT_FENCE_LAYOUT = """# Family

## review_thing

Full MCP name: `mcp__x__review_thing`

### Description (verbatim)

```text
Simulate a thing. Uses `backticks` and "quotes".
```

### Input schema (verbatim)

```json
{
  "additionalProperties": false,
  "properties": {"b": {"type": "string"}, "a": {"type": "integer"}},
  "required": ["b"],
  "type": "object"
}
```
"""

PROSE_MARKER_LAYOUT = """## get_thing

Description (verbatim):

Get a thing. Read-only.

Input schema:

```json
{"additionalProperties": false, "type": "object"}
```

(Note: this schema has no `required` array.)

---

## run_thing

Full tool name: `mcp__x__run_thing`

### Description (verbatim)

Run a thing and cut off… [truncated]

> NOTE: description truncated at the source; the remainder was not served.

### Input schema (verbatim)

```json
{"type": "object", "properties": {"id": {"type": "string"}}}
```
"""

PROSE_DIRECT_LAYOUT = """# Watchlists

## get_lists

List the lists. Use to look up list_id values.

```json
{
  "additionalProperties": false,
  "type": "object"
}
```

## Not a tool heading

Some text.
"""


class ParseMarkdownTests(unittest.TestCase):
    def test_text_fence_layout(self):
        tools, errors = st.parse_schema_markdown(TEXT_FENCE_LAYOUT)
        self.assertEqual(errors, [])
        self.assertEqual(len(tools), 1)
        name, desc, schema = tools[0]
        self.assertEqual(name, "review_thing")
        self.assertEqual(desc, 'Simulate a thing. Uses `backticks` and "quotes".')
        self.assertEqual(list(schema["properties"]), ["b", "a"], "property order is preserved")

    def test_prose_with_markers_drops_editorial_notes(self):
        tools, errors = st.parse_schema_markdown(PROSE_MARKER_LAYOUT)
        self.assertEqual(errors, [])
        by = {n: (d, s) for n, d, s in tools}
        self.assertEqual(by["get_thing"][0], "Get a thing. Read-only.")
        self.assertEqual(by["run_thing"][0], "Run a thing and cut off… [truncated]")
        self.assertEqual(by["run_thing"][1]["properties"]["id"]["type"], "string")

    def test_prose_directly_under_heading(self):
        tools, errors = st.parse_schema_markdown(PROSE_DIRECT_LAYOUT)
        self.assertEqual(errors, [])
        self.assertEqual([t[0] for t in tools], ["get_lists"])
        self.assertEqual(tools[0][1], "List the lists. Use to look up list_id values.")

    def test_missing_schema_is_an_error(self):
        tools, errors = st.parse_schema_markdown("## get_nothing\n\nNo schema here.\n")
        self.assertEqual(tools, [])
        self.assertEqual(errors[0]["code"], "NO_SCHEMA")

    def test_real_captures_parse_to_the_committed_snapshot(self):
        tools, families, errors = st.parse_schema_dir(os.path.join(CONNECTOR, "schemas"))
        self.assertEqual(errors, [])
        self.assertEqual(len(tools), 81)
        snap = {t["name"]: t for t in real_snapshot()["tools"]}
        for name, desc, schema in tools:
            entry = st.make_entry(name, desc, schema)
            self.assertEqual(entry["sha256_description"], snap[name]["sha256_description"], name)
            self.assertEqual(entry["sha256_schema"], snap[name]["sha256_schema"], name)
        self.assertEqual(
            sorted(set(families.values())),
            [
                "accounts",
                "marketdata",
                "orders-equity-advanced",
                "orders-options-crypto",
                "research",
                "scanner",
                "watchlists-alerts",
            ],
        )


class SnapshotTests(unittest.TestCase):
    def test_committed_snapshot_verifies(self):
        snap = real_snapshot()
        self.assertEqual(st.verify_snapshot(snap), [])
        self.assertEqual(snap["tool_count"], 81)
        self.assertEqual(len(snap["tools"]), 81)
        self.assertEqual(snap["captured_at"], "2026-09-21")
        truncated = sorted(t["name"] for t in snap["tools"] if t.get("description_truncated"))
        self.assertEqual(truncated, ["create_scan", "place_option_order", "preview_scan"])

    def test_from_schemas_check_passes_on_the_repo(self):
        code, result = run_cli(["from-schemas", "--check"])
        self.assertEqual(code, 0, result)
        self.assertTrue(result["ok"])

    def test_tampered_snapshot_fails_verification(self):
        snap = copy.deepcopy(real_snapshot())
        snap["tools"][0]["description"] += " (edited)"
        snap["tools"][1]["inputSchema"]["properties"] = {"x": {"type": "string"}}
        snap["tool_count"] = 80
        codes = sorted(c for c, _m in st.verify_snapshot(snap))
        self.assertEqual(codes, ["COUNT_MISMATCH", "HASH_MISMATCH", "HASH_MISMATCH"])

    def test_duplicate_and_missing_captured_at(self):
        snap = copy.deepcopy(real_snapshot())
        snap["tools"].append(copy.deepcopy(snap["tools"][0]))
        snap["tool_count"] = len(snap["tools"])
        del snap["captured_at"]
        codes = sorted(c for c, _m in st.verify_snapshot(snap))
        self.assertIn("DUPLICATE", codes)
        self.assertIn("NO_CAPTURED_AT", codes)

    def test_schema_hash_ignores_key_order(self):
        a = st.make_entry("get_x", "d", {"type": "object", "properties": {"a": {"type": "string"}}})
        b = st.make_entry("get_x", "d", {"properties": {"a": {"type": "string"}}, "type": "object"})
        self.assertEqual(a["sha256_schema"], b["sha256_schema"])


class LoadToolsListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_mcp_result_jsonrpc_list_and_jsonl(self):
        tool = {"name": "get_x", "description": "d", "inputSchema": {"type": "object"}}
        for text in (
            json.dumps({"tools": [tool]}),
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": [tool]}}),
            json.dumps(
                [{"name": "mcp__robinhood-trading__get_x", "description": "d", "parameters": {"type": "object"}}]
            ),
            json.dumps(tool) + "\n\n",
        ):
            tools = st.load_tools_list(self.write("t.json", text))
            self.assertEqual(tools, [("get_x", "d", {"type": "object"})])

    def test_uuid_prefixed_names_are_stripped(self):
        path = self.write(
            "t.jsonl",
            "\n".join(
                json.dumps(o)
                for o in [
                    {
                        "name": "mcp__abcdefab-cdef-4abc-8def-abcdefabcdef__get_a",
                        "description": "a",
                        "inputSchema": {"type": "object"},
                    },
                    {
                        "name": "mcp__plugin_unofficial-rh-connector_robinhood__get_b",
                        "description": "b",
                        "input_schema": {"type": "object"},
                    },
                ]
            ),
        )
        self.assertEqual([t[0] for t in st.load_tools_list(path)], ["get_a", "get_b"])

    def test_bad_lists_raise(self):
        cases = {
            "dup": [
                {"name": "get_x", "description": "", "inputSchema": {}},
                {"name": "mcp__s__get_x", "description": "", "inputSchema": {}},
            ],
            "noschema": [{"name": "get_x", "description": ""}],
            "badname": [{"name": "Get X", "description": "", "inputSchema": {}}],
            "empty": [],
        }
        for label, objs in cases.items():
            with self.assertRaises(st.SnapshotError, msg=label):
                st.load_tools_list(self.write(label + ".json", json.dumps(objs)))


class IngestTests(unittest.TestCase):
    """ingest on a synthetic changed tool list writes a dated CHANGES diff (WP-A acceptance)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cdir = os.path.join(self.tmp, "connector")
        os.makedirs(self.cdir)
        for fn in ("tools.snapshot.json", "tool-classes.json", "CHANGES.md"):
            shutil.copy(os.path.join(CONNECTOR, fn), os.path.join(self.cdir, fn))
        shutil.copytree(os.path.join(CONNECTOR, "schemas"), os.path.join(self.cdir, "schemas"))
        self.base = [(t["name"], t["description"], t["inputSchema"]) for t in real_snapshot()["tools"]]

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write_list(self, tools, name="tools-list.json"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "tools": [
                        {"name": "mcp__robinhood-trading__" + n, "description": d, "inputSchema": s}
                        for n, d, s in tools
                    ]
                },
                fh,
            )
        return path

    def changed_list(self):
        tools = []
        for name, desc, schema in self.base:
            if name == "get_popular_watchlists":
                continue  # removed
            if name == "get_accounts":
                desc = desc + " Also returns the account's display color."
            if name == "get_equity_quotes":
                schema = copy.deepcopy(schema)
                schema["properties"]["include_extended"] = {
                    "description": "Include overnight prints.",
                    "type": "boolean",
                }
                schema["required"] = ["symbols", "include_extended"]
            tools.append((name, desc, schema))
        tools.append(
            (
                "place_bracket_order",
                "Place a bracket order with real money.",
                {
                    "type": "object",
                    "properties": {"account_number": {"description": "Brokerage account number.", "type": "string"}},
                },
            )
        )
        tools.append(
            (
                "get_market_movers",
                "List today's movers.",
                {
                    "type": "object",
                    "properties": {
                        "cursor": {"description": "Pass the prior response's next_cursor.", "type": "string"}
                    },
                },
            )
        )
        return tools

    def test_ingest_writes_dated_changes_and_regenerates_everything(self):
        path = self.write_list(self.changed_list())
        code, result = run_cli(["ingest", path, "--connector-dir", self.cdir, "--date", "2026-09-29"])
        self.assertEqual(code, 0, result)
        self.assertEqual(result["added"], ["get_market_movers", "place_bracket_order"])
        self.assertEqual(result["removed"], ["get_popular_watchlists"])
        self.assertEqual([d["name"] for d in result["description_changed"]], ["get_accounts"])
        self.assertEqual(result["schema_changed"][0]["name"], "get_equity_quotes")
        self.assertEqual(result["schema_changed"][0]["changes"]["params_added"], ["include_extended"])
        self.assertEqual(result["schema_changed"][0]["changes"]["required_added"], ["include_extended"])
        self.assertEqual(result["new_money_like"], ["place_bracket_order"])
        needs = {x["name"]: x for x in result["needs_classification"]}
        self.assertEqual(needs["place_bracket_order"]["suggested_class"], "money")
        self.assertTrue(needs["place_bracket_order"]["guard_layer1_covers"])
        self.assertEqual(needs["get_market_movers"]["suggested_class"], "read")
        self.assertEqual(needs["get_market_movers"]["suggested_paginates"], "next_cursor")
        self.assertEqual(result["remove_from_classes"], ["get_popular_watchlists"])

        with open(os.path.join(self.cdir, "CHANGES.md"), encoding="utf-8") as fh:
            changes = fh.read()
        entry = changes.split("## 2026-09-29: snapshot refresh", 1)
        self.assertEqual(len(entry), 2, "a dated entry was appended")
        self.assertTrue(changes.startswith("# Connector changes"), "the existing history is kept")
        body = entry[1]
        for text in (
            "captured 2026-09-29; 82 tools, previously 81",
            "### Added (2)",
            "`place_bracket_order`",
            "Money-like name or description: classify before merging",
            "### Removed (1)",
            "`get_popular_watchlists`",
            "### Description changed (1)",
            "`get_accounts`",
            "### Input schema changed (1)",
            "parameters added `include_extended`",
            "now required `include_extended`",
            "Classify: `get_market_movers`, `place_bracket_order`",
            "Remove: `get_popular_watchlists`",
        ):
            self.assertIn(text, body)

        snap = read_json(os.path.join(self.cdir, "tools.snapshot.json"))
        self.assertEqual(snap["captured_at"], "2026-09-29")
        self.assertEqual(snap["tool_count"], 82)
        self.assertEqual(st.verify_snapshot(snap), [])

        schemas = sorted(os.listdir(os.path.join(self.cdir, "schemas")))
        self.assertIn("unclassified.md", schemas)
        with open(os.path.join(self.cdir, "schemas", "unclassified.md"), encoding="utf-8") as fh:
            self.assertIn("## place_bracket_order", fh.read())
        # The regenerated markdown parses back to exactly the new snapshot.
        code, check = run_cli(
            [
                "from-schemas",
                "--schemas-dir",
                os.path.join(self.cdir, "schemas"),
                "--out",
                os.path.join(self.cdir, "tools.snapshot.json"),
                "--check",
            ]
        )
        self.assertEqual(code, 0, check)

    def test_unchanged_list_logs_a_no_change_entry(self):
        path = self.write_list(self.base)
        code, result = run_cli(["ingest", path, "--connector-dir", self.cdir, "--date", "2026-10-20"])
        self.assertEqual(code, 0, result)
        self.assertEqual(
            (result["added"], result["removed"], result["description_changed"], result["schema_changed"]),
            ([], [], [], []),
        )
        with open(os.path.join(self.cdir, "CHANGES.md"), encoding="utf-8") as fh:
            changes = fh.read()
        self.assertIn("## 2026-10-20: snapshot refresh (captured 2026-10-20; 81 tools, previously 81)", changes)
        self.assertIn("No tool changes", changes)
        snap = read_json(os.path.join(self.cdir, "tools.snapshot.json"))
        self.assertEqual(snap["captured_at"], "2026-10-20")

    def test_diff_writes_nothing(self):
        before = {}
        for dirpath, _d, files in os.walk(self.cdir):
            for fn in files:
                p = os.path.join(dirpath, fn)
                before[p] = read_bytes(p)
        path = self.write_list(self.changed_list())
        code, result = run_cli(["diff", path, "--connector-dir", self.cdir])
        self.assertEqual(code, 0, result)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["added"], ["get_market_movers", "place_bracket_order"])
        after = {}
        for dirpath, _d, files in os.walk(self.cdir):
            for fn in files:
                p = os.path.join(dirpath, fn)
                after[p] = read_bytes(p)
        self.assertEqual(before, after)

    def test_bad_date_is_rejected(self):
        path = self.write_list(self.base)
        code, result = run_cli(["ingest", path, "--connector-dir", self.cdir, "--date", "29/09/2026"])
        self.assertEqual(code, 1)
        self.assertEqual(result["errors"][0]["code"], "BAD_DATE")


class RenderTests(unittest.TestCase):
    def test_fence_grows_past_backticks_and_round_trips(self):
        desc = "Use ```code``` blocks and `inline`."
        entry = st.make_entry("get_x", desc, {"type": "object"})
        md = st.render_family_markdown("demo", [entry], "2026-09-29")
        self.assertIn("````text", md)
        tools, errors = st.parse_schema_markdown(md)
        self.assertEqual(errors, [])
        self.assertEqual(tools, [("get_x", desc, {"type": "object"})])

    def test_edge_whitespace_and_empty_descriptions_round_trip(self):
        schema = {"type": "object", "properties": {"q": {"type": ["null", "array"], "items": {"type": "string"}}}}
        for desc in ("ends with a newline\n", "", "  padded  ", "para one\n\npara two", "```", "trailing backtick`"):
            entry = st.make_entry("get_x", desc, schema)
            md = st.render_family_markdown("demo", [entry], "2026-09-29")
            tools, errors = st.parse_schema_markdown(md)
            self.assertEqual(errors, [], repr(desc))
            self.assertEqual(tools, [("get_x", desc, schema)], repr(desc))

    def test_suggestions(self):
        self.assertEqual(st.suggest_class("exercise_future", ""), "money")
        self.assertEqual(st.suggest_class("transfer_cash", ""), "money")
        self.assertEqual(st.suggest_class("get_thing", "Moves real money."), "money")
        self.assertEqual(st.suggest_class("review_bracket_order", ""), "simulate")
        self.assertEqual(st.suggest_class("delete_scan", ""), "cancel")
        self.assertEqual(st.suggest_class("get_futures_upgrade_info", ""), "enroll_link")
        self.assertEqual(st.suggest_class("update_scan", ""), "write_confirm")
        self.assertEqual(st.suggest_class("frobnicate", ""), "decide by hand")
        rhs = {"properties": {"account_number": {"description": "the rhs_account_number from get_accounts"}}}
        self.assertEqual(st.suggest_account_param(rhs), "account_number=rhs_value")
        self.assertEqual(
            st.suggest_paginates({"properties": {"offset": {"description": "pass next_offset"}}}), "next_offset"
        )


if __name__ == "__main__":
    unittest.main()
