"""Tests for shared/scripts/kitconfig.py (lookup order, restricted grammar, validation)."""

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "shared", "scripts")
NAME = "kitconfig"
SCRIPT = os.path.join(SCRIPTS, NAME + ".py")
GOLDEN = os.path.join(ROOT, "tests", "golden", NAME)
sys.path.insert(0, SCRIPTS)

import kitconfig  # noqa: E402

try:
    import tomllib
except ImportError:  # Python 3.9 / 3.10
    tomllib = None

FORBIDDEN_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "ftplib", "smtplib", "ssl", "asyncio"}


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) <= len(actual) and all(
            subset(e, a) for e, a in zip(expected, actual))
    return expected == actual and type(expected) is type(actual)


def load(name):
    with open(os.path.join(GOLDEN, name), encoding="utf-8") as fh:
        return json.load(fh)["cases"]


class ParseParityTests(unittest.TestCase):
    """The same file must parse to the same dict on 3.9 (restricted parser only) and 3.11+
    (restricted parser cross-checked with tomllib)."""

    def test_restricted_parser_matches_golden(self):
        for case in load("parse.json"):
            with self.subTest(case=case["name"]):
                self.assertEqual(case["expected_parse"], kitconfig._Parser(case["text"]).document())
                self.assertEqual(case["expected_parse"], kitconfig.parse_toml(case["text"]))

    @unittest.skipIf(tomllib is None, "tomllib needs Python 3.11+")
    def test_tomllib_matches_golden(self):
        for case in load("parse.json"):
            with self.subTest(case=case["name"]):
                self.assertEqual(case["expected_parse"], tomllib.loads(case["text"]))

    def test_rejections(self):
        for case in load("rejects.json"):
            with self.subTest(case=case["name"]):
                with self.assertRaises(kitconfig.ConfigError) as ctx:
                    kitconfig.parse_toml(case["text"])
                self.assertEqual(case["code"], ctx.exception.code, ctx.exception.msg)
                self.assertEqual(case["line"], ctx.exception.line, ctx.exception.msg)

    @unittest.skipIf(tomllib is None, "tomllib needs Python 3.11+")
    def test_rejection_notes_about_real_toml_are_accurate(self):
        for case in load("rejects.json"):
            with self.subTest(case=case["name"]):
                try:
                    tomllib.loads(case["text"])
                    accepted = True
                except Exception:
                    accepted = False
                self.assertEqual(case["valid_toml"], accepted)

    def test_bom_is_ignored(self):
        self.assertEqual({"report": {"window_days": "7"}},
                         kitconfig.parse_toml("\ufeff[report]\nwindow_days = \"7\"\n"))

    def test_rejects_inline_tables_even_through_the_op(self):
        out = kitconfig.run("get", {"text": "[policy]\nx = {a = \"1\"}\n", "section": "policy"})
        self.assertFalse(out["ok"])
        self.assertEqual("GRAMMAR", out["errors"][0]["code"])


class GoldenOpTests(unittest.TestCase):
    def test_validate_and_get_goldens(self):
        cases = load("validate.json")
        self.assertGreater(len(cases), 20)
        for case in cases:
            with self.subTest(case=case["name"]):
                out = kitconfig.run(case["op"], case["input"])
                if case.get("match") == "exact":
                    self.assertEqual(case["expected"], out)
                else:
                    self.assertTrue(subset(case["expected"], out), json.dumps(out, indent=1))

    def test_all_templates_unset_reports_missing_only_for_required_sections(self):
        text = load("parse.json")[0]["text"]
        out = kitconfig.run("validate", {"text": text})
        self.assertTrue(out["ok"])
        self.assertFalse(out["valid"])
        self.assertEqual([], out["errors"])
        missing_sections = {m.rsplit(".", 1)[0] for m in out["missing"]}
        self.assertEqual({"exits.equity", "exits.crypto", "options.exits", "options.criteria", "options.entry"},
                         missing_sections)
        self.assertNotIn("options.exits.exit_price_rule", out["missing"])
        self.assertNotIn("options.criteria.spread_width_min", out["missing"])  # structure is UNSET
        self.assertNotIn("options.criteria.sort_by", out["missing"])
        self.assertEqual([], out["warnings"])


class LookupOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kitconfig-test-")
        self.saved = {k: os.environ.get(k) for k in (kitconfig.ENV_VAR, "XDG_CONFIG_HOME")}
        os.environ.pop(kitconfig.ENV_VAR, None)
        self.xdg = os.path.join(self.tmp, "xdg")
        os.environ["XDG_CONFIG_HOME"] = self.xdg
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def window(self, data):
        out = kitconfig.run("get", dict(data, section="report", cwd=self.project))
        self.assertTrue(out["ok"], out)
        return out["section"].get("window_days"), out["source"]

    def test_precedence(self):
        user = self.write(os.path.join(self.xdg, "robinhood-skills", "config.toml"), "[report]\nwindow_days = \"1\"\n")
        self.assertEqual(("1", "user"), self.window({}))
        self.write(os.path.join(self.project, ".robinhood", "config.toml"), "[report]\nwindow_days = \"2\"\n")
        self.assertEqual(("2", "project"), self.window({}))
        env_path = self.write(os.path.join(self.tmp, "env.toml"), "[report]\nwindow_days = \"3\"\n")
        os.environ[kitconfig.ENV_VAR] = env_path
        self.assertEqual(("3", "env:" + kitconfig.ENV_VAR), self.window({}))
        named = self.write(os.path.join(self.tmp, "named.toml"), "[report]\nwindow_days = \"4\"\n")
        self.assertEqual(("4", "user_named"), self.window({"path": named}))
        self.assertTrue(os.path.exists(user))

    def test_json_fallback_at_a_location(self):
        self.write(os.path.join(self.project, ".robinhood", "config.json"), '{"report": {"window_days": "9"}}')
        self.assertEqual(("9", "project"), self.window({}))

    def test_toml_beats_json_at_the_same_location(self):
        self.write(os.path.join(self.project, ".robinhood", "config.json"), '{"report": {"window_days": "9"}}')
        self.write(os.path.join(self.project, ".robinhood", "config.toml"), "[report]\nwindow_days = \"8\"\n")
        self.assertEqual(("8", "project"), self.window({}))

    def test_pasted_block_is_the_last_resort(self):
        pasted = "Project notes...\n```\n# robinhood-skills:config\n[report]\nwindow_days = \"5\"\n```\nmore text"
        self.assertEqual(("5", "pasted"), self.window({"pasted": pasted}))
        self.write(os.path.join(self.xdg, "robinhood-skills", "config.toml"), "[report]\nwindow_days = \"1\"\n")
        self.assertEqual(("1", "user"), self.window({"pasted": pasted}))

    def test_named_path_that_does_not_exist_does_not_fall_through(self):
        self.write(os.path.join(self.xdg, "robinhood-skills", "config.toml"), "[report]\nwindow_days = \"1\"\n")
        out = kitconfig.run("locate", {"path": os.path.join(self.tmp, "typo.toml"), "cwd": self.project})
        self.assertTrue(out["ok"])
        self.assertIsNone(out["path_used"])
        self.assertEqual(1, len(out["searched"]))

    def test_no_config_found(self):
        out = kitconfig.run("get", {"section": "tax", "cwd": self.project})
        self.assertFalse(out["ok"])
        self.assertEqual("CONFIG_NOT_FOUND", out["errors"][0]["code"])
        self.assertIn("min_loss_usd", out["unset"])
        self.assertEqual(4, len(out["searched"]))
        out = kitconfig.run("validate", {"sections": ["options.exits"], "cwd": self.project})
        self.assertFalse(out["ok"])
        self.assertEqual(["options.exits.profit_target_pct", "options.exits.stop_loss_pct",
                          "options.exits.time_stop_dte", "options.exits.max_hold_days"], out["missing"])

    def test_running_from_a_skill_folder_is_not_a_silent_not_found(self):
        """Scripts run from inside a skill see the skill as ./; the project config must not vanish silently."""
        self.write(os.path.join(self.project, ".robinhood", "config.toml"), "[policy]\nmax_order_usd = \"500.00\"\n")
        skill = os.path.join(self.tmp, "skills", "robinhood-trading")
        self.write(os.path.join(skill, "SKILL.md"), "---\nname: x\n---\n")
        os.makedirs(os.path.join(skill, "scripts"))
        saved = os.getcwd()
        try:
            for where in (skill, os.path.join(skill, "scripts")):
                os.chdir(where)
                with self.subTest(cwd=where):
                    out = kitconfig.run("get", {"section": "policy"})
                    self.assertFalse(out["ok"])
                    self.assertEqual("PROJECT_DIR_UNKNOWN", out["errors"][0]["code"])
                    self.assertIn('"cwd"', out["errors"][0]["msg"])
                    out = kitconfig.run("validate", {"sections": ["policy"]})
                    self.assertEqual("PROJECT_DIR_UNKNOWN", out["errors"][0]["code"])
                    # a lower-priority user config is used, but with a warning that the project was not searched
                    user = self.write(os.path.join(self.xdg, "robinhood-skills", "config.toml"),
                                      "[policy]\nmax_order_usd = \"9.00\"\n")
                    out = kitconfig.run("get", {"section": "policy"})
                    self.assertTrue(out["ok"])
                    self.assertEqual("user", out["source"])
                    self.assertEqual(["PROJECT_DIR_UNKNOWN"], [w["code"] for w in out["warnings"]])
                    self.assertEqual(["PROJECT_DIR_UNKNOWN"],
                                     [w["code"] for w in kitconfig.run("locate", {})["warnings"]])
                    os.remove(user)
                    # the documented fix: pass the user's project directory
                    out = kitconfig.run("get", {"section": "policy", "cwd": self.project})
                    self.assertTrue(out["ok"])
                    self.assertEqual(("project", "500.00"), (out["source"], out["section"]["max_order_usd"]))
                    self.assertNotIn("warnings", out)
        finally:
            os.chdir(saved)
        # an ordinary project directory gets the plain answers
        os.chdir(self.project)
        try:
            out = kitconfig.run("get", {"section": "policy"})
            self.assertEqual("500.00", out["section"]["max_order_usd"])
            self.assertNotIn("warnings", out)
        finally:
            os.chdir(saved)
        self.assertEqual("BAD_INPUT", kitconfig.run("get", {"section": "policy", "cwd": 7})["errors"][0]["code"])

    def test_parse_error_names_the_file_and_line(self):
        path = self.write(os.path.join(self.project, ".robinhood", "config.toml"), "[tax]\n\nmin_loss_usd = 2.5\n")
        out = kitconfig.run("get", {"section": "tax", "cwd": self.project})
        self.assertFalse(out["ok"])
        self.assertEqual("GRAMMAR", out["errors"][0]["code"])
        self.assertEqual("line 3", out["errors"][0]["field"])
        self.assertIn(path, out["errors"][0]["msg"])

    def test_never_writes(self):
        self.write(os.path.join(self.project, ".robinhood", "config.toml"), "[report]\nwindow_days = \"2\"\n")
        before = sorted(os.walk(self.tmp))
        for op in ("locate", "get", "validate"):
            kitconfig.run(op, {"section": "report", "cwd": self.project})
        self.assertEqual(before, sorted(os.walk(self.tmp)))


class ContractTests(unittest.TestCase):
    def cli(self, *args, **kw):
        env = dict(os.environ, XDG_CONFIG_HOME=os.path.join(os.sep, "nonexistent-kitconfig-cli"))
        env.pop(kitconfig.ENV_VAR, None)
        return subprocess.run([sys.executable, SCRIPT] + list(args), input=kw.get("stdin", ""),
                              capture_output=True, text=True, timeout=120, env=env)

    def test_selftest_exits_zero(self):
        res = self.cli("--selftest")
        self.assertEqual(0, res.returncode, res.stdout + res.stderr)

    def test_schema_prints_json_schemas_for_every_op(self):
        doc = json.loads(self.cli("--schema").stdout)
        self.assertEqual(sorted(kitconfig.OPS), sorted(doc["ops"]))
        for op, pair in doc["ops"].items():
            for side in ("input", "output"):
                self.assertIn("$schema", pair[side])
        self.assertIn("options.criteria", doc["sections"])

    def test_cli_round_trip_and_errors(self):
        res = self.cli("get", stdin=json.dumps({"text": "[report]\nwindow_days = \"7\"\n", "section": "report"}))
        self.assertEqual(0, res.returncode)
        self.assertEqual({"window_days": "7"}, json.loads(res.stdout)["section"])
        res = self.cli("get", stdin="{bad")
        self.assertEqual(0, res.returncode)
        self.assertEqual("BAD_JSON", json.loads(res.stdout)["errors"][0]["code"])
        res = self.cli("write", stdin="{}")
        self.assertEqual("UNKNOWN_OP", json.loads(res.stdout)["errors"][0]["code"])

    def test_stdlib_only_no_network_and_py39_syntax(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, feature_version=(3, 9))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported & FORBIDDEN_IMPORTS, imported)
        self.assertNotIn('"w"', source)
        self.assertNotIn("'w'", source)


if __name__ == "__main__":
    unittest.main()
