"""Guards on .github/workflows/*.yml (no YAML library needed).

Run: python3 -m unittest discover -s tests -v

Why: two workflow mistakes would be expensive and silent. A paid eval run wired to push or
pull_request bills the owner on every commit (and hands the API key to fork code), and a
`${{ ... }}` expression pasted into a shell script lets a branch name or an input run commands.
These tests read the files as text, so they run anywhere the rest of the suite runs.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"


def read(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def top_level_block(text, key):
    """Lines of a top-level YAML mapping block (`key:` at column 0) as (indent, stripped) pairs."""
    lines = text.splitlines()
    out = []
    inside = False
    for line in lines:
        if re.match(r"^%s:\s*(#.*)?$" % re.escape(key), line):
            inside = True
            continue
        if inside:
            if line and not line[0].isspace() and not line.startswith("#"):
                break
            if line.strip() and not line.strip().startswith("#"):
                out.append((len(line) - len(line.lstrip(" ")), line.strip()))
    return out


def trigger_names(text):
    """Event names under the top-level `on:` key (mapping form or `on: [a, b]`)."""
    m = re.search(r"^on:\s*\[(.*)\]\s*$", text, re.M)
    if m:
        return sorted(t.strip() for t in m.group(1).split(",") if t.strip())
    block = top_level_block(text, "on")
    if not block:
        return []
    base = min(indent for indent, _ in block)
    return sorted(s.split(":", 1)[0].strip() for indent, s in block if indent == base)


def run_bodies(text):
    """(line number, body) for every `run:` value, block scalars included."""
    lines = text.splitlines()
    bodies = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        value = m.group(2)
        start = i + 1
        if value.strip() in ("|", "|-", "|+", ">", ">-", ">+"):
            body = []
            i += 1
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip(" ")) > indent):
                body.append(lines[i])
                i += 1
            bodies.append((start, "\n".join(body)))
        else:
            bodies.append((start, value))
            i += 1
    return bodies


class WorkflowFilesTests(unittest.TestCase):
    def test_all_expected_workflows_exist(self):
        names = {p.name for p in WORKFLOWS.glob("*.yml")}
        self.assertLessEqual({"drift-watch.yml", "evals.yml", "release.yml", "validate.yml"}, names)

    def test_paid_evals_never_run_on_push_or_pull_request(self):
        self.assertEqual(trigger_names(read("evals.yml")), ["schedule", "workflow_dispatch"])

    def test_triggers(self):
        self.assertEqual(trigger_names(read("validate.yml")), ["pull_request", "push", "workflow_call"])
        self.assertEqual(trigger_names(read("drift-watch.yml")), ["schedule", "workflow_dispatch"])
        self.assertEqual(trigger_names(read("release.yml")), ["push"])
        self.assertRegex(read("release.yml"), r'tags:\s*\["v\*"\]')
        self.assertIn("uses: ./.github/workflows/validate.yml", read("release.yml"))

    def test_no_expression_is_pasted_into_a_shell_script(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for line, body in run_bodies(path.read_text(encoding="utf-8")):
                self.assertNotIn("${{", body, "{}:{}: pass values through env:, not ${{ }} in run".format(
                    path.name, line))

    def test_no_pull_request_target_and_read_only_default(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("pull_request_target", text, path.name)
            self.assertRegex(text, r"(?m)^permissions:\s*\{\s*contents:\s*read\s*\}\s*$", path.name)

    def test_actions_are_pinned_to_a_major_version(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for m in re.finditer(r"uses:\s*(\S+)", path.read_text(encoding="utf-8")):
                ref = m.group(1)
                if ref.startswith("./"):
                    continue
                self.assertRegex(ref, r"^[\w.-]+/[\w.-]+@v\d+$", "{}: {}".format(path.name, ref))

    def test_validate_runs_every_check(self):
        text = read("validate.yml")
        for command in (
            "python3 tools/validate_skills.py",
            "python3 tools/sync_shared.py --check",
            "python3 tools/gen_rules_tables.py --check",
            "python3 tools/check_drift.py",
            "python3 tools/check_versions.py",
            "python3 -m unittest discover -s tests -v",
            "sh hooks/tests/test_guard.sh",
            "sh hooks/tests/test_guard.sh --no-python",
            "python3 -m unittest hooks/tests/test_audit_log.py -v",
            "python3 -m unittest hooks/tests/test_confirm_gate.py -v",
            "python3 evalkit/gen_evals.py --anchor 2026-11-16T20:05:00-05:00 --variant all --check",
            "python3 tools/check_readme.py",
            "python3 tools/leak_scan.py",
            "python3 tools/no_network_check.py",
            "python3 tools/build_release.py --dry-run",
            "shellcheck -s sh hooks/guard.sh",
            "claude plugin validate . --strict",
        ):
            self.assertIn(command, text)
        self.assertIn('python: ["3.9", "3.12"]', text)

    def test_confirm_off_check_covers_every_money_tool(self):
        text = read("validate.yml")
        for tool in ("place_equity_order", "place_option_order", "place_crypto_order", "place_advanced_order",
                     "exercise_option", "replace_option_order"):
            self.assertIn(tool, text)
        self.assertIn("for mode in unset simulate_only", text)

    def test_evals_gate_and_arms(self):
        text = read("evals.yml")
        self.assertIn("tools/aggregate_evals.py", text)
        self.assertIn("--gate", text)
        self.assertIn("--expect-arms with,skills-only", text)
        self.assertIn("python3 tools/build_variant.py skills-only", text)
        self.assertIn("python3 evalkit/gen_evals.py --check", text)
        self.assertIn("--max-cost-usd", text)
        self.assertIn("--no-publish", text)
        for number, line in enumerate(text.splitlines(), start=1):
            code = line.split("#", 1)[0]
            self.assertIsNone(re.search(r"--allow-tools\s+['\"]?mcp__", code),
                              "evals.yml:{}: never grant the real MCP servers".format(number))

    def test_release_applies_the_documented_release_gate_before_publishing(self):
        # docs/eval-scorecard.md and the README promise a passing three-model safety run and the
        # recorded demo before the v2.0.0 tag; without this step a tag publishes regardless.
        text = read("release.yml")
        gate = text.find("python3 tools/check_readme.py --release")
        self.assertGreater(gate, 0, "release.yml must run tools/check_readme.py --release")
        for later in ("python3 tools/build_release.py", "gh skill publish", "gh release create"):
            self.assertGreater(text.find(later), gate, "{} must come after the release gate".format(later))
        bodies = [body for _, body in run_bodies(text) if "check_readme.py --release" in body]
        self.assertEqual(len(bodies), 1)
        body = bodies[0]
        # Only prerelease tags (v2.0.0-rc.1) may skip it, and nothing may swallow its exit code.
        self.assertRegex(body, r'case "\$TAG" in\s+\*-\*\)')
        self.assertNotRegex(body, r"check_readme\.py --release[^\n]*(\|\||;\s*true|--no-|2>/dev/null)")
        step = text[text.rfind("- name:", 0, gate):gate]
        self.assertNotIn("continue-on-error", step)
        self.assertNotIn("if:", step)

    def test_evals_publish_the_readme_excerpt_with_the_scorecard(self):
        # check_readme.py --release needs the README excerpt from the same run as the scorecard.
        text = read("evals.yml")
        self.assertIn("--readme README.md", text)
        self.assertIn("git add docs/eval-scorecard.md docs/badges/evals.json README.md", text)

    def test_api_key_reaches_only_the_eval_step(self):
        text = read("evals.yml")
        self.assertEqual(len(re.findall(r"ANTHROPIC_API_KEY:\s*\$\{\{\s*secrets\.ANTHROPIC_API_KEY\s*\}\}", text)), 1)
        for name in ("validate.yml", "drift-watch.yml", "release.yml"):
            self.assertNotIn("secrets.", read(name), name)


if __name__ == "__main__":
    unittest.main()
