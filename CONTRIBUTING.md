# Contributing

*Unofficial. Not affiliated with Robinhood Markets, Inc.*

Thanks for helping. This kit sits next to a real brokerage account, so a few rules are firmer than
in most projects. Each one comes with its reason.

## Ground rules

- **Never call a Robinhood `place_*`, `exercise_*` or `replace_*` tool while developing**, not even
  "just once to see". Use the sandbox (below) and the `review_*`/`preview_*` tools, which only
  simulate. A placed order can't be taken back.
- **Never commit an account number, token, balance or live capture.** Mask account numbers to the
  last 4 characters (••••X4F1) everywhere, including issues and PR descriptions. CI runs
  `tools/leak_scan.py` on every push, but the scan is a net, not a license.
- **Evidence, not advice.** Skills compute rules, dates and dollars and show both sides; the user
  decides. No sizing, no agent-chosen quantity, limit, stop, target or option price, and no
  performance claims.
- **Edit sources, never generated or synced copies** (see "Where things live"). CI fails on drift,
  and a hand edit to a copy is lost at the next sync anyway.

## Setup

Python 3.9 or later, with no packages to install: every script is stdlib only. Git and a POSIX
`sh` run the hook tests. Claude Code is needed only for `claude plugin validate` and for evals.

## Try it with no Robinhood account

`sandbox/mock_server.py` is a local stdio MCP server with all 81 connector tools and a synthetic
household (details in `sandbox/README.md`):

```sh
claude mcp add rh-sandbox -- python3 "$PWD/sandbox/mock_server.py"
```

Disable your real Robinhood connector while you use it: both expose the same tool names.

## Run the checks

CI (`.github/workflows/validate.yml`) runs all of these on every push and pull request, on Python
3.9 and 3.12. Run them before you open a PR:

```sh
python3 tools/validate_skills.py                  # frontmatter, layout, SKILL.md size, script self-tests
python3 tools/sync_shared.py --check              # synced copies match shared/
python3 tools/gen_rules_tables.py --check         # generated tables in shared/connector-rules.md
python3 tools/check_drift.py                      # every tool and parameter exists in the snapshot
python3 tools/check_versions.py                   # one version everywhere
python3 -m unittest discover -s tests -v          # script and tool tests (golden files in tests/golden/)
sh hooks/tests/test_guard.sh                      # the order guard, every mode
sh hooks/tests/test_guard.sh --no-python          # ...and fail-closed with no Python on the path
python3 -m unittest hooks/tests/test_audit_log.py hooks/tests/test_confirm_gate.py -v
python3 evalkit/gen_evals.py --check              # evals/ matches its templates and fixture
python3 evalkit/gen_evals.py --golden --check     # README demo numbers still come out of the scripts
python3 tools/check_readme.py                     # README figures equal evalkit/golden/demo.json
python3 tools/leak_scan.py                        # no account numbers, tokens or captures
python3 tools/no_network_check.py                 # shipped code never reaches the network
python3 tools/build_release.py --dry-run          # release assets still build
```

## Where things live

| Edit this | Not this (generated or synced) | Then run |
|---|---|---|
| `shared/connector-rules.md`, `shared/invariants.md`, `shared/wash-sweep.md`, `shared/scripts/*.py` | `skills/*/references/connector-rules.md`, `skills/*/references/wash-sweep.md`, `skills/*/scripts/<shared>.py`, the invariants block inside each `SKILL.md`, `hooks/lib/*.py` | `python3 tools/sync_shared.py` |
| `connector/tool-classes.json` | `hooks/tool-classes.json`, `hooks/known-tools.txt`, `skills/robinhood-trading/scripts/tool_inventory.json`, the R4 and R15 tables | `python3 tools/sync_shared.py && python3 tools/gen_rules_tables.py` |
| `evalkit/fixtures/`, `evalkit/templates/`, `evalkit/triggers/` | anything under `evals/` (except `evals/results/`) | `python3 evalkit/gen_evals.py` (and `--golden` if a README number moves) |
| `LICENSE` | `skills/*/LICENSE.txt` | `python3 tools/sync_shared.py` |

Each skill must work when copied alone, which is why shared text is copied into every skill rather
than linked.

## Connector changes

Robinhood can add, rename or re-describe tools at any time. When it does:

1. Dump the live tool list from your own client (a read-only `tools/list`; never call an order tool).
2. `python3 tools/snapshot_tools.py ingest <tools-list.json>` rewrites `connector/tools.snapshot.json`
   and `connector/schemas/`, appends a dated diff to `connector/CHANGES.md`, and lists the new tools.
3. Classify every new tool by hand in `connector/tool-classes.json`. A person decides whether a tool
   can move money; nothing assigns classes automatically. The guard, the never-place graders and
   the Gemini exclusion list all follow the `money` class.
4. Update the connector rules and skills the diff touches, then run `python3 tools/check_drift.py`
   until it passes.
5. Add a "Connector changes" entry to `CHANGELOG.md`.

The weekly `drift-watch` workflow opens an issue when the snapshot is more than 30 days old or the
skills drift from it.

### Adding a parameter fact

`connector/param-facts.json` pins claims in the docs to the tool schemas, so a schema change can't
silently make a sentence wrong. Add an entry to `facts`:

```json
{
  "id": "short-kebab-id",
  "file": "skills/robinhood-trading/references/orders.md",
  "claim_regex": "(?is)regex that matches the sentence making the claim",
  "tool": "get_pnl_trade_history",
  "schema_path": "inputSchema.properties.span.description",
  "must_contain": ["exact substring from the schema that backs the claim"],
  "note": "why this fact matters"
}
```

`check_drift.py` fails if the regex stops matching the file, or if the schema text at
`schema_path` stops containing every `must_contain` string.

## Evals

Evals call real models and **cost money**, so they never run on pushes or pull requests.

- **In CI:** Actions > `evals` > Run workflow. The defaults run the `safety` cases on Haiku, Sonnet
  and Opus in two arms: WITH (the plugin, hooks included) and SKILLS-ONLY
  (`python3 tools/build_variant.py skills-only`). The WITH job also runs the no-kit baseline unless
  you turn `baseline` off. `max_cost_usd` caps each model-and-arm run; a capped run shows up as
  INCOMPLETE rather than as a pass. The workflow fails if any never-place grader is below 100% in
  WITH or SKILLS-ONLY. It needs the `ANTHROPIC_API_KEY` repository secret. The weekly schedule
  stays off until the repository variable `EVALS_SCHEDULE` is `on`. `release_tag` attaches the
  reports to a release. `open_pr` opens a pull request with the new scorecard, the badge and the
  README's generated scorecard excerpt, which needs "Allow GitHub Actions to create and approve pull
  requests" in the repository settings.
- **Locally:** from the repository root. The harness serves the Robinhood tools from the mocks in
  `evals/` and denies any tool a case doesn't grant, including a real MCP server configured on your
  machine:

  ```sh
  claude plugin eval . --case S1 --runs 1 --allow-tools Bash --scaffold --no-publish --json results-claude-sonnet-5-with.json --model claude-sonnet-5
  python3 tools/build_variant.py skills-only
  claude plugin eval dist/variants/skills-only --case S1 --runs 1 --ablation none --allow-tools Bash --scaffold --no-publish --json results-claude-sonnet-5-skills-only.json --model claude-sonnet-5
  python3 tools/aggregate_evals.py results-claude-sonnet-5-*.json --no-write --expect-arms with,skills-only
  ```

  Never pass `--allow-tools 'mcp__*'`: on a machine with the live connector, that grant would let a
  case reach it. `--scaffold` runs the generated `scaffold.sh` of cases that stage files (RC1 and RC2
  copy the audit fixture to `./audit`); without it those cases run against an empty directory. Each
  local run bills your own Claude account.
- **Adding a case:** add `evalkit/templates/<ID>/prompt.md.tmpl` and `graders/*.md.tmpl` (format in
  `evalkit/README.md`), then `python3 evalkit/gen_evals.py`. The never-place, canary and
  account-number graders are added to every case automatically.

`tools/aggregate_evals.py` turns the result files into `docs/eval-scorecard.md` (only the section
between its markers) and `docs/badges/evals.json`. It separates *attempted* money calls (what the
skills do alone) from *executed* ones (what got past the hook), and it never rounds a safety number
up.

## The leak scan

`tools/leak_scan.py` fails on anything shaped like an account number (8-16 uppercase letters and
digits with at least 2 of each), a standalone 9-digit number, an SSN-like number, a bearer token,
JWT, API key or private key, and on any committed file under `connector/captures/`. The synthetic
fixture accounts in `evalkit/fixtures/` are allowed everywhere. For another synthetic value, prefer
writing it so it doesn't look like an account number (for example `118_204_331` in Python). If that
isn't possible, put `leak-scan: allow` and the reason on the same line. Credentials are never
excusable: if one reaches a commit, rotate it.

## Writing copy

Docs, skills, README and launch posts follow the same rules:

- Say "Unofficial. Not affiliated with Robinhood Markets, Inc." where the brand appears, and use no
  Robinhood logo or colors.
- Never write "only" or "first" as a claim. A "nobody else does this" claim is worded "we found none
  as of <date>" and links its evidence.
- No performance, win-rate or "beats the market" claims, anywhere.
- Alerts notify; they don't execute. Never write that an exit "fires while your laptop is closed".
- "Nothing was placed" means exactly that; never suggest placing an order in the app without the
  caveats in connector rule R21.
- Explain the *why* behind a rule. An agent that knows why a rule exists applies it better at the
  edges.

## Pull requests

Fill in the checklist in the PR template. Keep one topic per PR, and add a "Connector changes" line
to the CHANGELOG whenever connector behavior or the snapshot changes.

## Releases (maintainer)

1. Set the new version everywhere `python3 tools/check_versions.py` looks, and date the CHANGELOG
   heading (`## [x.y.z] - YYYY-MM-DD`).
2. Run the `evals` workflow with the `safety` tag, the models `claude-haiku-4-5`, `claude-sonnet-5`
   and `claude-opus-5`, and `open_pr` on.
3. Merge the scorecard pull request only if its title says gate PASS. It updates README.md's
   generated scorecard excerpt, the badge and `docs/eval-scorecard.md` from the same run. Never
   hand-edit those generated blocks into a PASS state: `check_readme.py --release` requires both to
   come from `tools/aggregate_evals.py --readme README.md` for the same run (the same "run started"
   time), with a green `docs/badges/evals.json`.
4. Record `docs/media/demo.gif` from `docs/media/demo.tape`.
5. Run `python3 tools/check_readme.py --release`.
6. Only then push the final tag `vx.y.z`. `.github/workflows/release.yml` re-runs validation, checks
   the tag against every manifest, refuses a final tag unless `check_readme.py --release` passes,
   builds the zips, bundles and `SHA256SUMS`, and publishes the release with the CHANGELOG section as
   its notes. A prerelease tag such as `v2.0.0-rc.1` skips the eval and demo-media gate.
7. Optionally run the `evals` workflow with `release_tag` set to attach the reports to the release.

## Security

Report guard bypasses, unexpected orders and data leaks privately; see `SECURITY.md`.

## Conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).
