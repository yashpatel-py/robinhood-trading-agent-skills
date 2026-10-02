#!/usr/bin/env python3
"""Check the README and the launch copy against the golden demo numbers and the public-copy rules.

Preflight for Robinhood Agentic Trading (unofficial; not affiliated with Robinhood Markets, Inc.).

Why this exists: the README quotes dollar figures from the sandbox household ($390.00 of TSLA loss,
$130,000.00 of auto-exercise cash, ...). Those figures are computed by the skill scripts and stored in
evalkit/golden/demo.json. If a script or the fixture changes and the README does not, the front page
of the project states numbers the code no longer produces. This check makes that a CI failure. It
also enforces the copy rules that are easy to break in a hurry: no "first"/"only" claims, no
performance claims, no "exits that fire while your laptop is closed", no full fixture account
numbers, the safety table identical to docs/safety-model.md, and the disclaimer present.

Usage:
    python3 tools/check_readme.py              # CI mode: errors fail, media/scorecard gaps warn
    python3 tools/check_readme.py --release    # tag day: missing demo media or eval results fail too
                                               # (release.yml runs this on every final v* tag)
    python3 tools/check_readme.py --selftest   # run the embedded examples
    python3 tools/check_readme.py --root DIR   # check another checkout

Golden tags (README):
    Any line may end with   <!-- golden: key1 key2 -->   (keys from demo.json "readme").
    Each key's value must appear in that line, formatted the way the README writes it ($1,234.56 for
    money, YYYY-MM-DD for dates, 61.0% for percentages, 18 for counts), and every $ figure in the
    line must be one of those values.

Release gate (--release; warnings in CI mode once a run is published):
    The generated block of docs/eval-scorecard.md must come from a run whose verdict is
    "**Release gate: PASS.**", with WITH and SKILLS-ONLY results for a Haiku, a Sonnet and an Opus
    model and the safety cases included. The README excerpt must show the same run (the same
    "run started" time) with the same verdict, and docs/badges/evals.json (the README badge) must
    exist and be green. tools/aggregate_evals.py --readme README.md writes all three together.

File-level tags (docs/launch/*.md, where posts sit in code blocks and inline tags would be copied):
    <!-- golden-figures: key1 key2 -->   every listed value must appear in the file
    <!-- allow-figures: $0.10 $3,000 --> $ figures that are not golden numbers
    Every $ figure in the file must be a listed golden value or an allowed figure.

Stdlib only; Python 3.9+. No network access. Exit 0 when there are no errors, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
README_MAX_LINES = 260

MONEY_RE = re.compile(r"\$\d[\d,]*(?:\.\d+)?")
GOLDEN_TAG_RE = re.compile(r"<!--\s*golden:\s*([^>]*?)\s*-->")
FILE_GOLDEN_RE = re.compile(r"<!--\s*golden-figures:\s*([^>]*?)\s*-->")
FILE_ALLOW_RE = re.compile(r"<!--\s*allow-figures:\s*([^>]*?)\s*-->")
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
CODE_SPAN_RE = re.compile(r"`[^`]*`")
BEGIN_SAFETY = "<!-- BEGIN safety-table -->"
END_SAFETY = "<!-- END safety-table -->"
BEGIN_EXCERPT = "<!-- BEGIN generated:scorecard-excerpt -->"
END_EXCERPT = "<!-- END generated:scorecard-excerpt -->"
BEGIN_SCORECARD = "<!-- BEGIN generated:eval-scorecard -->"
END_SCORECARD = "<!-- END generated:eval-scorecard -->"
PENDING_MARK = "NOT YET RUN"
GATE_VERDICT_RE = re.compile(r"\*\*Release gate: ([A-Z]+)\.\*\*")
RUN_STARTED_RE = re.compile(r"run started ([^\s);,]+)")
TAG_FILTER_RE = re.compile(r"tag filter: (.+?)\._")
RESULTS_HEADING = "### Results by model and arm"
# docs/eval-scorecard.md "Release gate": never-place 100% in both gated arms on all three families.
RELEASE_MODEL_FAMILIES = ("haiku", "sonnet", "opus")
GATED_ARM_LABELS = ("WITH", "SKILLS-ONLY")
BADGE_PATH = "docs/badges/evals.json"
UNIQUENESS_PHRASE = "we found none as of 2026-09-22"

# Sentences of the disclaimer (build spec F.6) that must survive every README edit.
DISCLAIMER_SENTENCES = (
    "It is not affiliated with, endorsed by or sponsored by Robinhood Markets, Inc.",
    "It does not provide investment, financial, legal or tax advice",
    "Trading involves risk of loss, including your entire investment.",
    "Read every ticket before acting on it.",
)

# (regex, message). Case-insensitive. Each one is a public-copy rule from build spec A.2/A.4/F.2.
BANNED = (
    (r"\b(?:the|our)\s+(?:first|only)\s+(?:skill|skills|kit|plugin|tool|project|repo|repository|open[- ]source|guard|product|one)\b",
     "uniqueness claim: say \"we found none as of 2026-09-22\" and link the evidence instead of first/only"),
    (r"\bfirst[- ]ever\b|\bworld'?s\s+first\b|\bnobody\s+else\b|\bno\s+one\s+else\b|\bunlike\s+any\b",
     "uniqueness claim: say \"we found none as of 2026-09-22\" and link the evidence"),
    (r"\b(?:exits?|stops?|orders?)\b[^.\n]{0,40}\b(?:fire|fires|execute|executes|trigger|triggers)\b[^.\n]{0,40}\blaptop\b",
     "in simulate-only mode nothing executes with the laptop closed: say alerts notify your phone"),
    (r"\bAI\s+trading\s+bot\b", "\"AI trading bot\" is banned copy"),
    (r"\b(?:beat|beats|beating|outperform\w*)\b[^.\n]{0,30}\b(?:SPY|S&P|market|index)\b", "performance claim"),
    (r"\b\d+(?:\.\d+)?\s?%\s+(?:returns?|gains?|profit|APY|APR)\b", "performance claim"),
    (r"\bguaranteed?\b", "no guarantees in public copy"),
    (r"\bupvotes?\b", "never ask for votes"),
)

EMOJI_RANGES = ((0x1F000, 0x1FAFF), (0x2600, 0x26FF), (0x2700, 0x27BF), (0xFE0F, 0xFE0F))


class Finding:
    def __init__(self, path: str, line: int, msg: str, level: str = "error") -> None:
        self.path, self.line, self.msg, self.level = path, line, msg, level

    def __str__(self) -> str:
        where = "%s:%d" % (self.path, self.line) if self.line else self.path
        return "%s: %s: %s" % (where, self.level, self.msg)


# --------------------------------------------------------------------------- formatting
def money_text(value: str) -> str:
    """'130000.00' -> '$130,000.00'. Negative values keep a leading minus sign."""
    d = Decimal(str(value))
    sign = "-" if d < 0 else ""
    return "%s$%s" % (sign, "{:,.2f}".format(abs(d)))


def render_value(key: str, value) -> Tuple[str, bool]:
    """How the README writes a golden value. Returns (text, is_money)."""
    if isinstance(value, bool):
        return ("permanent" if value else "not permanent"), False
    if isinstance(value, float):
        return "%.1f%%" % value, False
    if isinstance(value, int):
        return str(value), False
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text, False
    if key.endswith(("_usd", "per_share", "extrinsic_mid", "extrinsic_bid", "dividend")) and re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return money_text(text), True
    return text, False


def normalize_money(token: str) -> Optional[Decimal]:
    try:
        return Decimal(token.lstrip("$").replace(",", ""))
    except InvalidOperation:
        return None


def split_keys(text: str) -> List[str]:
    return [k for k in re.split(r"[\s,]+", text.strip()) if k]


# --------------------------------------------------------------------------- golden
def load_golden(root: Path, findings: List[Finding]) -> Dict[str, object]:
    path = root / "evalkit" / "golden" / "demo.json"
    rel = "evalkit/golden/demo.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        findings.append(Finding(rel, 0, "cannot read golden numbers: %s" % exc))
        return {}
    if doc.get("complete") is not True:
        findings.append(Finding(rel, 0, "golden numbers are incomplete; run python3 evalkit/gen_evals.py --golden"))
    for name, item in sorted((doc.get("items") or {}).items()):
        if isinstance(item, dict) and item.get("matches_spec") is False:
            findings.append(Finding(rel, 0, "golden item %r no longer matches its spec (%s)" % (name, item.get("mismatched_fields"))))
    readme = doc.get("readme")
    if not isinstance(readme, dict) or not readme:
        findings.append(Finding(rel, 0, "no 'readme' block in the golden file"))
        return {}
    return readme


def check_tagged_lines(rel: str, lines: Sequence[str], golden: Dict[str, object], findings: List[Finding]) -> int:
    tags = 0
    for n, line in enumerate(lines, 1):
        for match in GOLDEN_TAG_RE.finditer(line):
            tags += 1
            keys = split_keys(match.group(1))
            body = GOLDEN_TAG_RE.sub("", line)
            if not keys:
                findings.append(Finding(rel, n, "empty golden tag"))
                continue
            expected_money: Set[Decimal] = set()
            for key in keys:
                if key not in golden:
                    findings.append(Finding(rel, n, "unknown golden key %r (see evalkit/golden/demo.json 'readme')" % key))
                    continue
                text, is_money = render_value(key, golden[key])
                if is_money:
                    expected_money.add(Decimal(str(golden[key])))
                if text not in body:
                    findings.append(Finding(rel, n, "golden %s = %s, but the line does not say %s" % (key, golden[key], text)))
            for token in MONEY_RE.findall(body):
                amount = normalize_money(token)
                if amount is not None and amount not in expected_money:
                    findings.append(Finding(rel, n, "%s is not a golden value of this line's keys %s" % (token, " ".join(keys))))
    return tags


def check_file_manifest(rel: str, text: str, golden: Dict[str, object], findings: List[Finding]) -> None:
    keys: List[str] = []
    for m in FILE_GOLDEN_RE.finditer(text):
        keys.extend(split_keys(m.group(1)))
    allowed: Set[Decimal] = set()
    for m in FILE_ALLOW_RE.finditer(text):
        for token in MONEY_RE.findall(m.group(1)):
            amount = normalize_money(token)
            if amount is not None:
                allowed.add(amount)
    golden_money: Set[Decimal] = set()
    body = FILE_ALLOW_RE.sub("", FILE_GOLDEN_RE.sub("", text))
    for key in keys:
        if key not in golden:
            findings.append(Finding(rel, 0, "unknown golden key %r in golden-figures" % key))
            continue
        rendered, is_money = render_value(key, golden[key])
        if is_money:
            golden_money.add(Decimal(str(golden[key])))
        if rendered not in body:
            findings.append(Finding(rel, 0, "golden %s = %s is listed but %s does not appear in the file" % (key, golden[key], rendered)))
    for n, line in enumerate(body.splitlines(), 1):
        for token in MONEY_RE.findall(line):
            amount = normalize_money(token)
            if amount is None or amount in golden_money or amount in allowed:
                continue
            findings.append(Finding(rel, n, "%s is neither a golden figure nor listed in allow-figures" % token))


# --------------------------------------------------------------------------- copy rules
def check_banned(rel: str, lines: Sequence[str], findings: List[Finding]) -> None:
    for n, line in enumerate(lines, 1):
        for pattern, msg in BANNED:
            m = re.search(pattern, line, re.IGNORECASE)
            if m:
                findings.append(Finding(rel, n, "%s: %r" % (msg, m.group(0))))


def check_emoji(rel: str, lines: Sequence[str], findings: List[Finding]) -> None:
    for n, line in enumerate(lines, 1):
        for ch in line:
            cp = ord(ch)
            if any(lo <= cp <= hi for lo, hi in EMOJI_RANGES):
                findings.append(Finding(rel, n, "emoji or pictograph U+%04X (not allowed in this file)" % cp))
                break


def fixture_account_numbers(root: Path) -> Set[str]:
    """Every full account number in the fixture household, so docs can be checked for leaks."""
    found: Set[str] = set()
    path = root / "evalkit" / "fixtures" / "household.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return found

    def walk(obj) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("account_number", "rhs_account_number", "rhc_account_number") and isinstance(v, str) and len(v) >= 6:
                    found.add(v)
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(doc)
    return found


def check_account_numbers(rel: str, text: str, numbers: Iterable[str], findings: List[Finding]) -> None:
    for number in sorted(numbers):
        idx = text.find(number)
        if idx >= 0:
            findings.append(Finding(rel, text.count("\n", 0, idx) + 1,
                                    "full fixture account number %s...: show it as ••••%s" % (number[:2], number[-4:])))


def block_between(text: str, begin: str, end: str) -> Optional[str]:
    i = text.find(begin)
    j = text.find(end, i + len(begin)) if i >= 0 else -1
    if i < 0 or j < 0:
        return None
    return text[i + len(begin):j]


def check_links(rel: str, base: Path, root: Path, lines: Sequence[str], release: bool, findings: List[Finding]) -> None:
    in_fence = False
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue  # code blocks show commands and formats, not links
        for target in LINK_RE.findall(CODE_SPAN_RE.sub("", line)):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith(("#", "../../", "mailto:")):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            resolved = (base / path_part).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                findings.append(Finding(rel, n, "link %s points outside the repository" % target))
                continue
            if resolved.exists():
                continue
            media = "docs/media/" in resolved.as_posix()
            level = "error" if (release or not media) else "warning"
            why = "recorded demo media is missing (the owner records it from docs/media/demo.tape)" if media else "broken link"
            findings.append(Finding(rel, n, "%s: %s" % (why, target), level))


# --------------------------------------------------------------------------- published eval run
def results_rows(region: str) -> Set[Tuple[str, str]]:
    """(model, arm label) pairs from the scorecard's "Results by model and arm" table."""
    rows: Set[Tuple[str, str]] = set()
    inside = False
    for line in region.splitlines():
        if line.startswith("### "):
            inside = line.strip() == RESULTS_HEADING
            continue
        if inside and line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[1] in ("WITH", "SKILLS-ONLY", "W/OUT"):
                rows.add((cells[0].lower(), cells[1]))
    return rows


def check_scorecard_run(region: str, level: str, findings: List[Finding]) -> None:
    rel = "docs/eval-scorecard.md"
    verdict = GATE_VERDICT_RE.search(region)
    if verdict is None:
        findings.append(Finding(rel, 0, "the results block has no \"**Release gate: ...**\" verdict; regenerate it "
                                        "with tools/aggregate_evals.py", level))
    elif verdict.group(1) != "PASS":
        findings.append(Finding(rel, 0, "the published eval run's release gate is %s, not PASS" % verdict.group(1), level))
    rows = results_rows(region)
    for family in RELEASE_MODEL_FAMILIES:
        models = {m for m, _ in rows if family in m}
        if not any(all((m, arm) in rows for arm in GATED_ARM_LABELS) for m in models):
            findings.append(Finding(rel, 0, "the published eval run has no WITH and SKILLS-ONLY results for any %s "
                                            "model (the release gate covers Haiku, Sonnet and Opus)" % family, level))
    tags = TAG_FILTER_RE.search(region)
    if tags:
        text = tags.group(1).strip()
        names = [t.strip() for t in text.split(",") if t.strip()]
        if not text.startswith("none") and "safety" not in names:
            findings.append(Finding(rel, 0, "the published eval run filtered to %r, which leaves out the safety "
                                            "cases" % text, level))


def check_excerpt_run(excerpt: str, region: Optional[str], level: str, findings: List[Finding]) -> None:
    verdict = GATE_VERDICT_RE.search(excerpt)
    if verdict is None or verdict.group(1) != "PASS":
        findings.append(Finding("README.md", 0, "the scorecard excerpt does not show a PASS release gate (%s); "
                                                "regenerate it with tools/aggregate_evals.py --readme README.md"
                                % (verdict.group(1) if verdict else "no verdict"), level))
    ours = RUN_STARTED_RE.search(excerpt)
    theirs = RUN_STARTED_RE.search(region) if region is not None else None
    if ours is None:
        findings.append(Finding("README.md", 0, "the scorecard excerpt does not name its run (\"run started ...\"); "
                                                "regenerate it with tools/aggregate_evals.py --readme README.md", level))
    elif theirs is not None and ours.group(1) != theirs.group(1):
        findings.append(Finding("README.md", 0, "the scorecard excerpt is from the run started %s, but "
                                                "docs/eval-scorecard.md is from the run started %s"
                                % (ours.group(1), theirs.group(1)), level))


def check_badge(root: Path, level: str, findings: List[Finding]) -> None:
    path = root / BADGE_PATH
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        findings.append(Finding(BADGE_PATH, 0, "missing: the README evals badge reads it from main; merge the "
                                               "evals workflow's scorecard pull request", level))
        return
    except ValueError as exc:
        findings.append(Finding(BADGE_PATH, 0, "not valid JSON: %s" % exc, level))
        return
    if not isinstance(doc, dict) or doc.get("label") != "evals" or doc.get("color") != "brightgreen":
        shown = (doc.get("message"), doc.get("color")) if isinstance(doc, dict) else doc
        findings.append(Finding(BADGE_PATH, 0, "the evals badge is not a green passing run: %r" % (shown,), level))


# --------------------------------------------------------------------------- the run
def check(root: Path, release: bool) -> List[Finding]:
    findings: List[Finding] = []
    golden = load_golden(root, findings)
    numbers = fixture_account_numbers(root)

    readme_path = root / "README.md"
    try:
        readme = readme_path.read_text(encoding="utf-8")
    except OSError as exc:
        return findings + [Finding("README.md", 0, "cannot read: %s" % exc)]
    lines = readme.splitlines()
    if len(lines) > README_MAX_LINES:
        findings.append(Finding("README.md", 0, "%d lines; keep it at or below %d and move detail to docs/" % (len(lines), README_MAX_LINES)))
    if golden and check_tagged_lines("README.md", lines, golden, findings) == 0:
        findings.append(Finding("README.md", 0, "no <!-- golden: ... --> tag: the demo line must be checked against the golden numbers"))
    check_banned("README.md", lines, findings)
    check_emoji("README.md", lines, findings)
    check_account_numbers("README.md", readme, numbers, findings)
    check_links("README.md", root, root, lines, release, findings)
    for sentence in DISCLAIMER_SENTENCES:
        if sentence not in readme.replace("\n> ", " ").replace("\n", " "):
            findings.append(Finding("README.md", 0, "disclaimer sentence missing: %r" % sentence))
    if "Unofficial" not in readme or "Not affiliated with Robinhood Markets, Inc." not in readme:
        findings.append(Finding("README.md", 0, "the hero must say Unofficial and \"Not affiliated with Robinhood Markets, Inc.\""))
    if UNIQUENESS_PHRASE not in readme:
        findings.append(Finding("README.md", 0, "uniqueness claims must read %r" % UNIQUENESS_PHRASE))

    safety_doc = root / "docs" / "safety-model.md"
    try:
        canonical = block_between(safety_doc.read_text(encoding="utf-8"), BEGIN_SAFETY, END_SAFETY)
    except OSError:
        canonical = None
    ours = block_between(readme, BEGIN_SAFETY, END_SAFETY)
    if canonical is None:
        findings.append(Finding("docs/safety-model.md", 0, "missing the %s ... %s block (the canonical safety table)" % (BEGIN_SAFETY, END_SAFETY)))
    elif ours is None:
        findings.append(Finding("README.md", 0, "missing the safety table block copied from docs/safety-model.md"))
    elif ours.strip() != canonical.strip():
        findings.append(Finding("README.md", 0, "the safety table differs from docs/safety-model.md; copy it verbatim"))

    gate_level = "error" if release else "warning"
    excerpt = block_between(readme, BEGIN_EXCERPT, END_EXCERPT)
    if excerpt is None:
        findings.append(Finding("README.md", 0, "missing the generated scorecard excerpt block"))
    elif PENDING_MARK in excerpt:
        findings.append(Finding("README.md", 0, "the eval scorecard excerpt has no published run yet", gate_level))
    region: Optional[str] = None
    try:
        scorecard = (root / "docs" / "eval-scorecard.md").read_text(encoding="utf-8")
        region = block_between(scorecard, BEGIN_SCORECARD, END_SCORECARD)
        if region is None:
            findings.append(Finding("docs/eval-scorecard.md", 0, "missing the generated results block"))
        elif PENDING_MARK in region:
            findings.append(Finding("docs/eval-scorecard.md", 0, "no published eval run yet", gate_level))
    except OSError:
        findings.append(Finding("docs/eval-scorecard.md", 0, "missing"))
    published = region is not None and PENDING_MARK not in region
    if published:
        check_scorecard_run(region or "", gate_level, findings)
    if excerpt is not None and PENDING_MARK not in excerpt:
        check_excerpt_run(excerpt, region if published else None, gate_level, findings)
    if release or published:
        check_badge(root, gate_level, findings)

    for path in sorted((root / "docs").rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        doc_lines = text.splitlines()
        check_account_numbers(rel, text, numbers, findings)
        if rel.startswith("docs/audit-"):
            continue  # generated audit ledger: quotes the old v1 text on purpose
        check_banned(rel, doc_lines, findings)
        if rel.startswith("docs/launch/"):
            if golden:
                check_file_manifest(rel, text, golden, findings)
            check_links(rel, path.parent, root, doc_lines, release, findings)
        if rel == "docs/launch/awesome-list-entries.md":
            check_emoji(rel, doc_lines, findings)
    return findings


# --------------------------------------------------------------------------- selftest
def selftest() -> int:
    golden = {"a.disallowed_usd": "390.00", "a.date": "2026-12-07", "b.pct": 61.0, "c.days": 18, "d.cash_needed_usd": "130000.00"}
    cases = [
        ("tagged line ok", ["Found $390.00 of loss, clean from 2026-12-07 <!-- golden: a.disallowed_usd a.date -->"], 0),
        ("wrong figure", ["Found $391.00 of loss <!-- golden: a.disallowed_usd -->"], 2),
        ("untagged extra figure", ["$390.00 and $5.00 <!-- golden: a.disallowed_usd -->"], 1),
        ("unknown key", ["$390.00 <!-- golden: nope -->"], 2),
        ("thousands separator", ["needs $130,000.00 <!-- golden: d.cash_needed_usd -->"], 0),
        ("percent and count", ["+61.0% and 18 days <!-- golden: b.pct c.days -->"], 0),
    ]
    failures = 0
    for name, lines, expected in cases:
        found: List[Finding] = []
        check_tagged_lines("t.md", lines, golden, found)
        if len(found) != expected:
            failures += 1
            print("FAIL tagged %s: expected %d findings, got %s" % (name, expected, [str(f) for f in found]))
    manifest = [
        ("manifest ok", "<!-- golden-figures: a.disallowed_usd -->\n<!-- allow-figures: $0.10 -->\n$390.00 lost; gap $0.10\n", 0),
        ("manifest stray figure", "<!-- golden-figures: a.disallowed_usd -->\n$390.00 and $12.00\n", 1),
        ("manifest missing value", "<!-- golden-figures: a.disallowed_usd -->\nnothing here\n", 1),
    ]
    for name, text, expected in manifest:
        found = []
        check_file_manifest("t.md", text, golden, found)
        if len(found) != expected:
            failures += 1
            print("FAIL manifest %s: expected %d, got %s" % (name, expected, [str(f) for f in found]))
    copy = [
        ("first claim", "The first skill that checks wash sales", 1),
        ("only claim", "the only kit for this", 1),
        ("honest only", "a native alert is the only agent-side option there", 0),
        ("laptop exits", "exits that fire while your laptop is closed", 1),
        ("laptop alerts ok", "alerts that notify your phone while your laptop is closed", 0),
        ("bot", "an AI trading bot for Robinhood", 1),
        ("beats spy", "the agent beats SPY", 1),
        ("returns", "made 408% returns", 1),
        ("votes", "please upvote", 1),
        ("dated claim ok", "we found none as of 2026-09-22", 0),
    ]
    for name, line, expected in copy:
        found = []
        check_banned("t.md", [line], found)
        if len(found) != expected:
            failures += 1
            print("FAIL copy %s: expected %d, got %s" % (name, expected, [str(f) for f in found]))
    found = []
    with_links = ["[ok](README.md)", "`[Name](link) - format`", "```", "[in a fence](nowhere.md)", "```", "[bad](missing-file.md)"]
    check_links("t.md", REPO, REPO, with_links, False, found)
    if len(found) != 1 or "missing-file.md" not in found[0].msg:
        failures += 1
        print("FAIL links: %s" % [str(f) for f in found])
    found = []
    check_emoji("t.md", ["plain text · → ×", "rocket \U0001F680"], found)
    if len(found) != 1:
        failures += 1
        print("FAIL emoji: %s" % [str(f) for f in found])
    found = []
    check_account_numbers("t.md", "acct ••••X4F1 and 5QR9X4F1", {"5QR9X4F1"}, found)
    if len(found) != 1 or "••••X4F1" not in found[0].msg:
        failures += 1
        print("FAIL account number: %s" % [str(f) for f in found])
    if money_text("-412.00") != "-$412.00" or money_text("84467.5") != "$84,467.50":
        failures += 1
        print("FAIL money_text")
    table = ["### Results by model and arm", "", "| Model | Arm | Cases |", "|---|---|---:|"]
    for model in ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"):
        table += ["| %s | WITH | 13 |" % model, "| %s | SKILLS-ONLY | 13 |" % model]
    head = "_Generated (latest run started 2026-09-26T18:00:00Z). tag filter: safety._"
    runs = [
        ("published pass", "**Release gate: PASS.** ok\n" + head + "\n" + "\n".join(table), 0),
        ("failed gate, one model", "**Release gate: FAIL.** no\n" + head + "\n" + "\n".join(table[:6]), 3),
        ("wrong tag filter", "**Release gate: PASS.** ok\n" + head.replace("safety", "tax") + "\n" + "\n".join(table), 1),
    ]
    for name, region, expected in runs:
        found = []
        check_scorecard_run(region, "error", found)
        if len(found) != expected:
            failures += 1
            print("FAIL scorecard run %s: expected %d, got %s" % (name, expected, [str(f) for f in found]))
    found = []
    check_excerpt_run("**Release gate: PASS.** From x: run started 2026-09-25T01:00:00Z; y", runs[0][1], "error", found)
    if len(found) != 1 or "2026-09-25T01:00:00Z" not in found[0].msg:
        failures += 1
        print("FAIL excerpt from another run: %s" % [str(f) for f in found])
    print("selftest: %s" % ("ok" if not failures else "%d failure(s)" % failures))
    return 0 if not failures else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check README and launch copy against golden numbers and copy rules.")
    parser.add_argument("--release", action="store_true", help="tag day: missing demo media or eval results are errors")
    parser.add_argument("--selftest", action="store_true", help="run the embedded examples and exit")
    parser.add_argument("--root", default=str(REPO), help="repository root (default: this checkout)")
    ns = parser.parse_args(argv)
    if ns.selftest:
        return selftest()
    findings = check(Path(ns.root), ns.release)
    errors = [f for f in findings if f.level == "error"]
    for f in findings:
        print(f)
    print("check_readme: %d error(s), %d warning(s)" % (len(errors), len(findings) - len(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
