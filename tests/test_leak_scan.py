"""Tests for tools/leak_scan.py.

Run: python3 -m unittest discover -s tests -v

Every fake account number and token below is assembled at runtime from pieces, so this
file itself never contains a string the scanner would flag (CI scans the tests too).
"""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import leak_scan as ls  # noqa: E402

# Assembled at runtime (see the module docstring).
ACCT = "7ZQ" + "4K8W2"            # account-like: 8 chars, letters and digits
ACCT2 = "9XY" + "3M1P6"
FIXTURE_ACCT = "4LM" + "7N2R5"
NINE = "8123" + "45670"           # 9 digits
FIXTURE_RHS = "5550" + "12349"
SSN = "219" + "-09-" + "9999"
BEARER = "Bear" + "er " + "abcDEF123456" + "ghiJKL7890"
JWT = "ey" + "J" + "hbGciOiJIUzI1NiJ9" + "." + "ey" + "JzdWIiOiIxIn0"
ANTHROPIC_KEY = "sk-" + "ant-" + "api03-" + "A1b2C3d4E5f6G7h8"
GH_TOKEN = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
PEM = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
OCC = "SPY" + "261120" + "C" + "00650000"


def run_main(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ls.main(args)
    return code, out.getvalue(), err.getvalue()


class LeakScanTreeTests(unittest.TestCase):
    """A plain directory (no git): the scanner walks it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="leak-scan-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)
        self.write("evalkit/fixtures/household.json", json.dumps({
            "accounts": [{"account_number": FIXTURE_ACCT, "rhs_account_number": FIXTURE_RHS,
                          "rhc_account_number": ""}]}))

    def write(self, rel, text, mode="w"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "wb":
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def kinds(self):
        return [(f.path, f.kind) for f in ls.scan(self.root)]

    def test_clean_tree_passes(self):
        self.write("README.md", "Nothing to see. Account ••••X4F1 is masked.\n")
        self.assertEqual(ls.scan(self.root), [])

    def test_account_like_token_fails_and_is_masked(self):
        self.write("docs/note.md", "my account is {} ok\n".format(ACCT))
        findings = ls.scan(self.root)
        self.assertEqual([(f.path, f.line, f.kind) for f in findings], [("docs/note.md", 1, "account-like")])
        self.assertEqual(findings[0].masked, "••••" + ACCT[-4:])
        self.assertNotIn(ACCT, str(findings[0]))

    def test_nine_digit_number_fails(self):
        self.write("skills/x/SKILL.md", "rhs value {}\n".format(NINE))
        self.assertEqual(self.kinds(), [("skills/x/SKILL.md", "9-digit")])

    def test_ssn_like_fails(self):
        self.write("docs/a.md", "ssn {}\n".format(SSN))
        self.assertEqual(self.kinds(), [("docs/a.md", "ssn-like")])

    def test_look_alikes_pass(self):
        self.write("docs/ok.md", "\n".join([
            "dates 2026-11-16 and 2027-01-04",
            "timestamp 2026-11-17T01:04:00." + NINE + "Z",
            "amount " + NINE + ".50 and $" + NINE,
            "checksums in SHA256SUMS",
            "option symbol " + OCC,
            "a hash 3f9a" + NINE + "bc and ten digits " + NINE + "1",
            "lowercase " + ACCT.lower() + " and a word ABCDEFGHIJ",
            "an escape \\U0001" + "F680 and a colour #" + ACCT,
            "an order id " + "3F25" + "04E0" + "-4F89-11D3-9A0C-" + "0305E8" + "2C3301",
        ]) + "\n")
        self.assertEqual(ls.scan(self.root), [])

    def test_fixture_account_numbers_allowed_everywhere(self):
        self.write("tests/test_x.py", "ACCT = '{}'\nRHS = '{}'\n".format(FIXTURE_ACCT, FIXTURE_RHS))
        self.write("evalkit/README.md", "| {} / {} |\n".format(FIXTURE_ACCT, FIXTURE_RHS))
        self.assertEqual(ls.scan(self.root), [])

    def test_synthetic_trees_skip_value_checks_but_not_credentials(self):
        self.write("evalkit/fixtures/variants/v.json", json.dumps({"x": ACCT, "n": NINE}))
        self.write("tests/golden/tool/case.json", json.dumps({"x": ACCT2}))
        self.write("evals/S1/prompt.md", "value {} and token {}\n".format(ACCT, JWT))
        self.assertEqual(self.kinds(), [("evals/S1/prompt.md", "credential (jwt)")])

    def test_pragma_excuses_values_never_credentials(self):
        self.write("docs/p.md", "synthetic {}  <!-- leak-scan: allow (made up) -->\n"
                                "{} leak-scan: allow\n".format(ACCT, BEARER))
        self.assertEqual(self.kinds(), [("docs/p.md", "credential (bearer-token)")])

    def test_each_credential_kind(self):
        self.write("hooks/tests/events/e.json", "\n".join([BEARER, JWT, ANTHROPIC_KEY, GH_TOKEN, PEM]) + "\n")
        kinds = sorted(k for _, k in self.kinds())
        self.assertEqual(kinds, ["credential (anthropic-key)", "credential (bearer-token)",
                                 "credential (github-token)", "credential (jwt)", "credential (private-key)"])
        for f in ls.scan(self.root):
            self.assertNotIn(f.masked, (BEARER, JWT, ANTHROPIC_KEY, GH_TOKEN))

    def test_placeholder_bearer_passes(self):
        self.write("docs/b.md", "Bearer strings are dropped; the test uses __BEARER__ and `Bearer …`.\n")
        self.assertEqual(ls.scan(self.root), [])

    def test_captures_directory(self):
        self.write("connector/captures/.gitignore", "*\n!.gitignore\n")
        self.assertEqual(ls.scan(self.root), [])
        self.write("connector/captures/get_accounts.json", "{}")
        self.assertEqual(self.kinds(), [("connector/captures/get_accounts.json", "capture")])

    def test_binary_files_are_skipped(self):
        self.write("assets/demo.gif", b"GIF89a\x00\x00" + ACCT.encode(), mode="wb")
        self.assertEqual(ls.scan(self.root), [])

    def test_known_synthetic_in_file(self):
        rel = "evalkit/render.py"
        allowed = sorted(ls.KNOWN_SYNTHETIC_IN_FILE[rel])[0]
        self.write(rel, "stamp = make(2, {})\n".format(allowed))
        self.assertEqual(ls.scan(self.root), [])
        self.write("evalkit/other.py", "stamp = make(2, {})\n".format(allowed))
        self.assertEqual(self.kinds(), [("evalkit/other.py", "9-digit")])

    def test_skipped_directories_in_walk(self):
        self.write("node_modules/pkg/index.js", "x = '{}'\n".format(ACCT))
        self.write("dist/zips/readme.md", "x = '{}'\n".format(ACCT))
        self.assertEqual(ls.scan(self.root), [])

    def test_explicit_paths(self):
        a = self.write("docs/a.md", "x {}\n".format(ACCT))
        self.write("docs/b.md", "x {}\n".format(ACCT2))
        findings = ls.scan(self.root, [str(a)])
        self.assertEqual([f.path for f in findings], ["docs/a.md"])

    def test_main_exit_codes_and_json(self):
        code, out, _ = run_main(["--root", str(self.root)])
        self.assertEqual((code, out.strip()), (0, "leak_scan: clean"))
        self.write("docs/a.md", "x {}\n".format(ACCT))
        code, out, err = run_main(["--root", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("docs/a.md:1: account-like: ••••" + ACCT[-4:], out)
        self.assertNotIn(ACCT, out + err)
        code, out, _ = run_main(["--root", str(self.root), "--json"])
        data = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(data["ok"])
        self.assertEqual(data["findings"][0]["kind"], "account-like")
        code, _, err = run_main(["--root", str(self.root / "missing")])
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class LeakScanGitTests(unittest.TestCase):
    """Inside a git checkout the scanner lists what git would commit."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="leak-scan-git-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)
        self.git("init", "-q")

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root)] + list(args), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_ignored_files_are_skipped_untracked_files_are_not(self):
        self.write(".gitignore", "local-notes.txt\n")
        self.write("local-notes.txt", "x {}\n".format(ACCT))
        self.write("new-doc.md", "x {}\n".format(ACCT2))
        self.assertEqual([(f.path, f.kind) for f in ls.scan(self.root)], [("new-doc.md", "account-like")])

    def test_ignored_capture_is_fine_force_added_capture_fails(self):
        self.write("connector/captures/.gitignore", "*\n!.gitignore\n")
        self.write("connector/captures/get_accounts.json", "{}")
        self.assertEqual(ls.scan(self.root), [])
        self.git("add", "-f", "connector/captures/get_accounts.json")
        self.assertEqual([(f.path, f.kind) for f in ls.scan(self.root)],
                         [("connector/captures/get_accounts.json", "capture")])

    def test_deleted_tracked_file_is_ignored(self):
        self.write("gone.md", "x {}\n".format(ACCT))
        self.git("add", "gone.md")
        (self.root / "gone.md").unlink()
        self.assertEqual(ls.scan(self.root), [])


if __name__ == "__main__":
    unittest.main()
