#!/usr/bin/env python3
"""Fail the build when a file looks like it carries a real account number or credential.

Usage:
    python3 tools/leak_scan.py [--root DIR] [--json] [PATH ...]

With no PATH, scans every file git would commit: tracked files plus untracked files
that are not ignored (so a new file is caught before its first `git add`). Outside a git
checkout it walks the tree instead. Exit 0 when clean, 1 on any finding, 2 on a usage
error.

Why this exists: the kit talks to a live brokerage account. Account numbers, a crypto
account's rhs value, bearer tokens and the owner's Day-1 captures all pass through the
people who work on it, and one careless paste into a doc, test or issue template
publishes them for good. CI refuses the push instead of trusting everyone to remember.

What fails:
  account-like  an 8-16 character token of uppercase letters and digits with at least
                2 of each (Robinhood-style account numbers look like this)
  9-digit       a standalone run of exactly 9 digits (rhs account numbers; SSN-sized)
  ssn-like      NNN-NN-NNNN
  credential    `Bearer <token>`, a JWT (`eyJ...`), an Anthropic or GitHub token, or a
                PEM private key. Never excusable, and checked in every file.
  capture       any committed file under connector/captures/ except its .gitignore
                (the owner's masked live responses never leave the owner's machine)

What is allowed, and why:
  - Files under evalkit/fixtures/, evals/ and tests/golden/ are skipped for the
    account-like, 9-digit and ssn-like checks: they hold the synthetic fixture
    household by design (credentials are still checked there).
  - The synthetic account numbers declared in evalkit/fixtures/**/*.json (any key ending
    in `account_number`) are allowed everywhere, so docs and tests can quote the fixture.
  - OCC option symbols (SPY261120C00650000), UUIDs, escapes such as \\U0001F680, hex colours
    and a few identifiers such as SHA256SUMS.
  - A line containing `leak-scan: allow` is skipped for the account-like, 9-digit and
    ssn-like checks. Use it only for a synthetic value, and say why on the same line;
    reviewers see it in the diff. It never excuses a credential.

Findings are printed masked (the last 4 characters, or the first 6 of a credential), so
the CI log never republishes what it caught.

Stdlib only; Python 3.9+. No network access (it runs `git ls-files`, nothing else).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

# Trees that hold the synthetic fixture household (account-like / 9-digit / ssn checks skipped).
SYNTHETIC_PREFIXES = ("evalkit/fixtures/", "evals/", "tests/golden/")
FIXTURE_GLOB_ROOT = "evalkit/fixtures"
CAPTURES_DIR = "connector/captures/"
CAPTURES_KEEP = {"connector/captures/.gitignore"}
PRAGMA = "leak-scan: allow"

# Directories never scanned in a filesystem walk (git mode relies on .gitignore instead).
WALK_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".ruff_cache", ".mypy_cache", ".pytest_cache",
                  ".venv", "venv", "dist", ".tox"}

# Identifiers that match the account-like shape but are not account numbers.
SAFE_TOKENS = {"SHA256SUMS", "BASE64URL", "RFC3339NANO"}

# Values in specific files that are synthetic by construction. Each entry says why. Prefer
# writing such numbers with digit separators (118_204_331) or adding the pragma instead.
KNOWN_SYNTHETIC_IN_FILE: Dict[str, Set[str]] = {
    # Nanosecond fractions of the fixture's quote timestamps and a synthetic bar volume
    # (the evals' mock quote builder, WP-M).
    "evalkit/render.py": {"118204331", "552009120", "301877540", "845112004", "204118650",  # leak-scan: allow
                          "250000000"},  # leak-scan: allow (synthetic values, see the comment above)
}

# A backslash or # before the token means an escape (\U0001F680) or a hex colour, not an account.
ACCOUNT_LIKE = re.compile(r"(?<![A-Za-z0-9_\\#])[A-Z0-9]{8,16}(?![A-Za-z0-9_])")
OCC_SYMBOL = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
NINE_DIGIT = re.compile(r"(?<![0-9A-Za-z_.$\\#])\d{9}(?![0-9A-Za-z_])(?!\.\d)")
UUID = re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b")
SSN_LIKE = re.compile(r"(?<![0-9A-Za-z_.-])\d{3}-\d{2}-\d{4}(?![0-9A-Za-z_-])")

CREDENTIALS = (
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]{8,}=*")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]*)?")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
)


class Finding(object):
    __slots__ = ("path", "line", "kind", "masked")

    def __init__(self, path: str, line: int, kind: str, masked: str) -> None:
        self.path = path
        self.line = line
        self.kind = kind
        self.masked = masked

    def as_dict(self) -> Dict[str, object]:
        return {"path": self.path, "line": self.line, "kind": self.kind, "masked": self.masked}

    def __str__(self) -> str:
        return "{}:{}: {}: {}".format(self.path, self.line, self.kind, self.masked)


# --------------------------------------------------------------------------- file lists

def _git_files(root: Path) -> Optional[List[str]]:
    """Files git would commit (tracked + untracked-not-ignored), or None outside a checkout."""
    try:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        if top.returncode != 0:
            return None
        if Path(top.stdout.decode("utf-8", "replace").strip()).resolve() != root.resolve():
            return None
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    names = [n for n in out.stdout.decode("utf-8", "surrogateescape").split("\0") if n]
    return sorted(set(names))


def _walk_files(root: Path) -> List[str]:
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in WALK_SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, str(root))
        for name in filenames:
            rel = name if rel_dir == "." else os.path.join(rel_dir, name)
            found.append(rel.replace(os.sep, "/"))
    return sorted(found)


def list_files(root: Path) -> List[str]:
    files = _git_files(root)
    return files if files is not None else _walk_files(root)


# --------------------------------------------------------------------------- allowlists

def _collect_account_values(node: object, out: Set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.endswith("account_number") and isinstance(value, str) and value:
                out.add(value)
            _collect_account_values(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_account_values(item, out)


def synthetic_account_numbers(root: Path) -> Set[str]:
    """Every value under a key ending in `account_number` in the fixture JSON files."""
    values: Set[str] = set()
    base = root / FIXTURE_GLOB_ROOT
    if not base.is_dir():
        return values
    for path in sorted(base.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        _collect_account_values(data, values)
    return values


# --------------------------------------------------------------------------- scanning

def _mask_value(token: str) -> str:
    return "••••" + token[-4:]


def _mask_credential(token: str) -> str:
    return token[:6] + "… ({} chars)".format(len(token))


def _is_account_like(token: str) -> bool:
    if token in SAFE_TOKENS or OCC_SYMBOL.match(token):
        return False
    digits = sum(c.isdigit() for c in token)
    letters = len(token) - digits
    return digits >= 2 and letters >= 2


def _read_text(path: Path) -> Optional[str]:
    try:
        with path.open("rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None  # binary (zip, image): nothing a person pastes by hand
    return data.decode("utf-8", "replace")


def scan_text(rel: str, text: str, allowed: Set[str]) -> List[Finding]:
    findings: List[Finding] = []
    synthetic_tree = rel.startswith(SYNTHETIC_PREFIXES)
    file_allow = KNOWN_SYNTHETIC_IN_FILE.get(rel, set())
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, rx in CREDENTIALS:
            for m in rx.finditer(line):
                findings.append(Finding(rel, number, "credential ({})".format(kind), _mask_credential(m.group(0))))
        if synthetic_tree or PRAGMA in line:
            continue
        line = UUID.sub("<uuid>", line)  # request and order ids, never account numbers
        for m in ACCOUNT_LIKE.finditer(line):
            tok = m.group(0)
            if _is_account_like(tok) and tok not in allowed and tok not in file_allow:
                findings.append(Finding(rel, number, "account-like", _mask_value(tok)))
        for m in NINE_DIGIT.finditer(line):
            tok = m.group(0)
            if tok not in allowed and tok not in file_allow:
                findings.append(Finding(rel, number, "9-digit", _mask_value(tok)))
        for m in SSN_LIKE.finditer(line):
            findings.append(Finding(rel, number, "ssn-like", _mask_value(m.group(0))))
    return findings


def scan(root: Path, paths: Optional[Sequence[str]] = None) -> List[Finding]:
    root = root.resolve()
    allowed = synthetic_account_numbers(root)
    rels: Iterable[str]
    if paths:
        rels = []
        for p in paths:
            full = Path(p)
            full = full if full.is_absolute() else (Path.cwd() / full)
            try:
                rels.append(full.resolve().relative_to(root).as_posix())
            except ValueError:
                rels.append(full.as_posix())
    else:
        rels = list_files(root)
    findings: List[Finding] = []
    for rel in rels:
        full = root / rel
        if rel.startswith(CAPTURES_DIR) and rel not in CAPTURES_KEEP:
            if full.is_file() or full.is_symlink():
                findings.append(Finding(rel, 1, "capture", "files under connector/captures/ must never be committed"))
            continue
        if full.is_symlink() or not full.is_file():
            continue  # deleted in the working tree, or a link (never followed)
        text = _read_text(full)
        if text is None:
            continue
        findings.extend(scan_text(rel, text, allowed))
    return findings


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on committed account numbers, credentials or captures.")
    parser.add_argument("paths", nargs="*", help="files to scan (default: every file git would commit)")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="repository root (default: %(default)s)")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print("leak_scan: --root {} is not a directory".format(root), file=sys.stderr)
        return 2
    findings = scan(root, args.paths or None)
    if args.json:
        print(json.dumps({"ok": not findings, "findings": [f.as_dict() for f in findings]}, indent=2,
                         ensure_ascii=False))
    else:
        for f in findings:
            print(f)
        if findings:
            print("leak_scan: {} finding(s). Remove the value, or, if it is synthetic, add it to the fixture or "
                  "mark the line with '{}' and a reason. Credentials are never excusable; rotate any that "
                  "reached a commit.".format(len(findings), PRAGMA), file=sys.stderr)
        else:
            print("leak_scan: clean")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
