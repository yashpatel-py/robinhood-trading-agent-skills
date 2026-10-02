"""Tests for tools/check_readme.py, mainly the --release gate that release.yml runs on final tags.

Run: python3 -m unittest discover -s tests -v

Why: the README and docs/eval-scorecard.md promise a passing safety run on Haiku, Sonnet and Opus
before the v2.0.0 tag. `check_readme.py --release` is the step in release.yml that keeps that
promise, so it must fail on a pending, failed, partial or mismatched run, and pass on a real one.
Each test builds a small synthetic checkout, so it does not depend on the live README's wording.
"""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import aggregate_evals as ae  # noqa: E402
import check_readme as cr  # noqa: E402
import test_aggregate_evals as tae  # noqa: E402  (the synthetic `claude plugin eval --json` builders)

SAFETY = "| Surface | Blocked |\n|---|---|\n| Claude Code | by the hook |\n"
PENDING_EXCERPT = "NOT YET RUN for v2.0.0. Published here before the v2.0.0 tag."
PENDING_SCORECARD = "NOT YET RUN for v2.0.0. The v2.0.0 tag requires the release gate above to pass first."
MODELS = ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5")


def readme_text(excerpt=PENDING_EXCERPT):
    return "\n".join([
        "# Kit",
        "",
        "*Unofficial. Not affiliated with Robinhood Markets, Inc.*",
        "",
        "![demo](docs/media/demo.gif)",
        "",
        "Found $390.00 of loss. <!-- golden: a.disallowed_usd -->",
        "",
        "We looked for others and we found none as of 2026-09-22.",
        "",
        cr.BEGIN_SAFETY,
        SAFETY + cr.END_SAFETY,
        "",
        cr.BEGIN_EXCERPT,
        excerpt,
        cr.END_EXCERPT,
        "",
        "> " + " ".join(cr.DISCLAIMER_SENTENCES),
        "",
    ])


class Checkout(object):
    def __init__(self, test):
        self.root = Path(tempfile.mkdtemp(prefix="check-readme-"))
        test.addCleanup(shutil.rmtree, str(self.root), True)
        self.write("evalkit/golden/demo.json", json.dumps(
            {"complete": True, "items": {}, "readme": {"a.disallowed_usd": "390.00"}}))
        self.write("README.md", readme_text())
        self.write("docs/safety-model.md", "# Safety\n\n{}\n{}{}\n".format(cr.BEGIN_SAFETY, SAFETY, cr.END_SAFETY))
        self.write("docs/eval-scorecard.md", "# Eval scorecard\n\nIntro.\n\n{}\n{}\n{}\n\nMore prose.\n".format(
            cr.BEGIN_SCORECARD, PENDING_SCORECARD, cr.END_SCORECARD))
        self.results = self.root / "eval-out"
        self.results.mkdir()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def record_demo(self):
        self.write("docs/media/demo.gif", "GIF89a")

    def put_run(self, models=MODELS, placed=(), started="2026-09-26T18:00:00Z", tags=("safety",)):
        for f in self.results.glob("*.json"):
            f.unlink()
        for model in models:
            for arm in ("with", "skills-only"):
                data = tae.result([tae.case("S1", [tae.run(placed=placed if arm == "with" else ())]),
                                   tae.case("A1", [tae.run()])],
                                  model=model, started=started, ablation="with-without" if arm == "with" else "none")
                data["suite"]["tagFilters"] = list(tags)
                (self.results / "results-{}-{}.json".format(model, arm)).write_text(json.dumps(data), encoding="utf-8")

    def aggregate(self, *extra, readme=True):
        args = [str(self.results), "--root", str(REPO),
                "--scorecard", str(self.root / "docs" / "eval-scorecard.md"),
                "--badge", str(self.root / "docs" / "badges" / "evals.json"),
                "--evals-dir", str(self.root / "evals"),
                "--run-url", "https://github.com/o/r/actions/runs/9"] + list(extra)
        if readme:
            args += ["--readme", str(self.root / "README.md")]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return ae.main(args)

    def check(self, release=True):
        return cr.check(self.root, release)

    def main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cr.main(["--root", str(self.root)] + list(args))
        return code, out.getvalue()


def errors(findings):
    return [str(f) for f in findings if f.level == "error"]


class ReleaseGateTests(unittest.TestCase):
    def assertOnlyErrors(self, findings, *fragments):
        errs = errors(findings)
        for fragment in fragments:
            self.assertTrue(any(fragment in e for e in errs), "no error mentions {!r}: {}".format(fragment, errs))
        self.assertEqual(len(errs), len(fragments), errs)

    def test_pending_run_and_missing_media_block_a_release_but_only_warn_in_ci(self):
        co = Checkout(self)
        self.assertEqual(errors(co.check(release=False)), [])
        self.assertOnlyErrors(co.check(), "docs/media/demo.gif", "excerpt has no published run",
                              "no published eval run yet", "docs/badges/evals.json: error: missing")
        self.assertEqual(co.main("--release")[0], 1)
        self.assertEqual(co.main()[0], 0)

    def test_a_merged_pass_run_on_three_models_with_recorded_media_passes(self):
        co = Checkout(self)
        co.put_run()
        self.assertEqual(co.aggregate("--gate", "--expect-models", ",".join(MODELS)), 0)
        co.record_demo()
        findings = co.check()
        self.assertEqual(errors(findings), [])
        self.assertEqual([str(f) for f in findings], [])
        code, out = co.main("--release")
        self.assertEqual(code, 0, out)

    def test_demo_media_is_still_required(self):
        co = Checkout(self)
        co.put_run()
        co.aggregate()
        self.assertOnlyErrors(co.check(), "recorded demo media is missing")

    def test_a_failed_run_blocks_a_release(self):
        co = Checkout(self)
        co.put_run(placed=("place_equity_order",))
        self.assertEqual(co.aggregate("--gate"), 1)
        co.record_demo()
        self.assertOnlyErrors(co.check(), "release gate is FAIL, not PASS",
                              "excerpt does not show a PASS release gate (FAIL)",
                              "evals badge is not a green passing run")
        self.assertEqual(errors(co.check(release=False)), [])

    def test_an_incomplete_run_blocks_a_release(self):
        co = Checkout(self)
        co.put_run()
        co.aggregate("--expect-models", ",".join(MODELS + ("claude-extra-1",)))
        co.record_demo()
        self.assertOnlyErrors(co.check(), "release gate is INCOMPLETE, not PASS",
                              "excerpt does not show a PASS release gate (INCOMPLETE)",
                              "evals badge is not a green passing run")

    def test_a_pass_on_one_model_is_not_the_three_model_gate(self):
        co = Checkout(self)
        co.put_run(models=("claude-sonnet-5",))
        self.assertEqual(co.aggregate("--gate", "--expect-models", "claude-sonnet-5"), 0)
        co.record_demo()
        self.assertOnlyErrors(co.check(), "results for any haiku model", "results for any opus model")

    def test_a_run_without_the_safety_cases_is_not_the_gate(self):
        co = Checkout(self)
        co.put_run(tags=("tax",))
        co.aggregate()
        co.record_demo()
        self.assertOnlyErrors(co.check(), "leaves out the safety cases")

    def test_the_full_suite_counts_as_including_the_safety_cases(self):
        co = Checkout(self)
        co.put_run(tags=())
        co.aggregate()
        co.record_demo()
        self.assertEqual(errors(co.check()), [])

    def test_readme_excerpt_from_another_run_is_caught(self):
        co = Checkout(self)
        co.put_run(started="2026-09-25T10:00:00Z")
        co.aggregate()
        co.put_run(started="2026-09-26T18:00:00Z")
        co.aggregate(readme=False)
        co.record_demo()
        self.assertOnlyErrors(co.check(), "excerpt is from the run started 2026-09-25T10:00:00Z")

    def test_a_hand_written_excerpt_is_not_accepted(self):
        co = Checkout(self)
        co.put_run()
        co.aggregate(readme=False)
        co.write("README.md", readme_text("Evals: all green on every model."))
        co.record_demo()
        self.assertOnlyErrors(co.check(), "excerpt does not show a PASS release gate (no verdict)",
                              "excerpt does not name its run")

    def test_missing_or_broken_badge_blocks_a_release(self):
        co = Checkout(self)
        co.put_run()
        co.aggregate()
        co.record_demo()
        badge = co.root / "docs" / "badges" / "evals.json"
        badge.write_text("{not json", encoding="utf-8")
        self.assertOnlyErrors(co.check(), "not valid JSON")
        badge.unlink()
        self.assertOnlyErrors(co.check(), "docs/badges/evals.json: error: missing")

    def test_selftest(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = cr.selftest()
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
