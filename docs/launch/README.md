# Launch plan and copy

<!-- golden-figures: tsla_wash.disallowed_usd tsla_wash.earliest_clean_sale_date protection_audit.value_unprotected_usd spy_auto_exercise.cash_needed_usd spy_auto_exercise.buying_power_usd -->
<!-- allow-figures: $412.00 $195.00 -->

*Unofficial. Not affiliated with Robinhood Markets, Inc.* Internal: the maintainer posts these by
hand. Nothing in this folder is posted automatically, and none of it should be pasted without the
checks below.

All six skills ship together in **v2.0.0**, tagged **Sun Sep 27, 2026**. Later waves are content and
feature updates on top of that release, not new skill launches.

| File | Where it goes |
|---|---|
| [show-hn.md](show-hn.md) | Show HN post and first comment (Mon Sep 28) |
| [x-thread.md](x-thread.md) | X launch thread (Mon Sep 28) and HOOD Summit live posts (Tue Sep 29) |
| [reddit-claudeai.md](reddit-claudeai.md) | r/ClaudeAI "Built with Claude" post (Mon Sep 28, afternoon) |
| [reddit-algotrading.md](reddit-algotrading.md) | r/algotrading educational post (second wave, Sep 30 to Oct 2) |
| [devto-tutorial.md](devto-tutorial.md) | dev.to tutorial (second wave) |
| [product-hunt.md](product-hunt.md) | Product Hunt or DevHunt (Oct 3 to 4) |
| [awesome-list-entries.md](awesome-list-entries.md) | awesome-list submissions (Sep 26, then Oct 5 to 6, then Nov 2) |
| [github-metadata.md](github-metadata.md) | repository description, topics and settings (Thu Sep 24) |

## Before anything is posted

- [ ] `python3 tools/check_readme.py --release` passes (`release.yml` enforces it on every final tag): the README figures equal
      `evalkit/golden/demo.json`, the demo GIF exists, and the eval scorecard has a published run.
- [ ] `docs/eval-scorecard.md` shows the v2.0.0 safety run on Haiku, Sonnet and Opus. Link to it;
      never quote a number that isn't on that page.
- [ ] The sandbox command in the README works on a fresh clone, on a machine with no Robinhood connector.
- [ ] Private vulnerability reporting is on (the check in [github-metadata.md](github-metadata.md)
      prints `true`), so the `SECURITY.md` link works for outside reporters.
- [ ] The first line of every post says **unofficial**: the title, where the post has one (HN, Reddit,
      dev.to), and the first line of the text for the HN first comment and every standalone X post.
      No Robinhood logo, feather or colors in any image. No "first" or "only" claims, no trading-bot
      framing, no performance claims.
- [ ] The brand check (GitHub, npm, skills.sh, ClawHub, the X handle, USPTO classes 9, 36 and 42) is
      done. If "Preflight" conflicts, switch everywhere to the fallback name "Ticket-First" first.
- [ ] Never ask anyone for votes, stars or likes, in any channel.

## Calendar (today is Tue 2026-09-22)

| Date | Build | Launch action |
|---|---|---|
| Tue Sep 22 | Spec frozen; all work packages start; live field capture done | — |
| Wed Sep 23 | Brand collision check (30 min) | Reserve the X handle; draft the 1280×640 social preview ("Preflight · Unofficial", no Robinhood marks) |
| Thu Sep 24 | First integration; hooks and core green on unit tests | **Push publicly.** Turn on private vulnerability reporting first; enable Discussions; pin a "Connector changelog" issue; apply [github-metadata.md](github-metadata.md) |
| Fri Sep 25 | All six skills integrated; first eval run (Sonnet) | — |
| Sat Sep 26 | Three-model safety run; scorecard; README numbers; record the demo from `docs/media/demo.tape` | awesome-claude-code web form (filed by the maintainer) |
| **Sun Sep 27** | **Tag v2.0.0**; GitHub Release (zips, bundles, `report.html`); `gh skill publish` | Submit `robinhood-trading` to the Anthropic community marketplace; the Gemini gallery picks up the tag; final HN draft |
| **Mon Sep 28** | Buffer (v2.0.1 if needed) | **Show HN 06:30 to 07:30 PT**; stay in the thread all day. Afternoon: r/ClaudeAI; X thread with the GIF |
| Tue Sep 29 | HOOD Summit keynote, 5:30 PM CT; refresh the connector snapshot right after | Live X posts with safe-use clips; no logos, no implied endorsement |
| Wed Sep 30 to Fri Oct 2 | Summit delta within 48 hours: v2.1.0 if tools changed, otherwise a dated "re-verified" entry in `connector/CHANGES.md`; Codex and Cursor first-run tests (labels change only if they pass) | Second wave: r/algotrading post; dev.to tutorial |
| Sat Oct 3 to Sun Oct 4 | Scorecard refresh | Product Hunt or DevHunt |
| Mon Oct 5 to Tue Oct 6 | Triage; retro on installs, stars and issues | Awesome-list PRs |
| Tue Oct 13 | — | Earnings season: options-radar post ("what the options market prices in for your holdings") |
| Fri Oct 16 | — | Monthly expiration: guardian and options-monitor clips |
| By Fri Oct 30 | November re-verification of `tax-rules-2026.md`; NYSE calendar check | — |
| Mon Nov 2 | — | Tax-season post: "Harvest without washing across your Agentic, individual and IRA accounts." X, Reddit education, newsletter pitches. Re-post to HN only with a genuinely new capability. VoltAgent submission |
| Tue Nov 3 · Fri Nov 20 · Thu–Fri Nov 26–27 | — | Election-week volatility note · monthly expiration · holiday closure and early-close note |
| Tue Dec 1 | — | "From today, any buy can wash a Dec 31 loss" |
| Fri Dec 18 | — | Quarterly expiration: assignment radar reminder |
| Tue Dec 29 | — | "Thu Dec 31 is the last trading day of 2026; the trade date sets the tax year; a loss sold Dec 31 can be bought back from Mon Feb 1, 2027" |
| Jan 2027 | — | "1099s are per account; here is the cross-account check" |

## HOOD Summit (Tue Sep 29): response within 48 hours

| If Robinhood announces | Then |
|---|---|
| New MCP tools (brackets, trailing stops, a market-hours tool, crypto tax lots, a new asset class) | `python3 tools/snapshot_tools.py ingest` → classify the new tools → the guard coverage and never-place graders regenerate → update the connector rules and skills → re-run the evals → CHANGELOG "Connector changes" → a "supported the day after" post |
| Native agent limits or approval settings | The core readiness card reports them; the kit's policy check becomes belt and braces. Copy: "works with Robinhood's new controls" |
| Native paper trading | Point users to it; lead with the wash-sale check, the exits and the hook |
| Native tax-loss harvesting for self-directed accounts | Lead with the **cross-account** part, which per-account tooling can't see |
| Tool renames | The drift check and evals catch them; fix the same day |

## Media

**README GIF (15 s):** `vhs docs/media/demo.tape` records it against the sandbox, captioned "sandbox
data": the TSLA harvest request, the Roth IRA line ($390.00 permanently disallowed; clean sale from
2026-12-07), then the guard refusing a place call with exit 2.

**75-second video** (X, the HN first comment, Product Hunt), recorded the same way:

| t | Beat | On screen |
|---|---|---|
| 0–8 s | Readiness | "What can my agent do?" → Agentic ••••X4F1, limited margin, options level 3, "Guard self-test: blocked a synthetic place_equity_order (exit 2)." |
| 8–22 s | Tax | "Harvest my TSLA loss…" → the Roth IRA conflict, 2026-12-07, the manual lot ticket for the individual account |
| 22–36 s | Exits | "Which positions have no protection?" → ACTION NEEDED: $84,467.50 unprotected; the AMD gaps; "Yes, set it: alert me if NVDA drops below $195." → the alert is created ("notifies your phone; does not sell") |
| 36–48 s | Options | "What should I know about my options this week?" → the SPY calls need $130,000.00 at auto-exercise against $2,480.00 of buying power |
| 48–58 s | Injection | "Research PLTR and act on anything urgent" → the agent quotes the instruction hidden in a news item (`get_equity_news`) and does nothing it asks |
| 58–68 s | Report card | "What did my agent do last week?" → 1 order ($412.00) with no local audit entry |
| 68–75 s | Card | `npx skills add yashpatel-py/robinhood-trading-agent-skills` · "Unofficial" |

The figures are sandbox data from `evalkit/golden/demo.json` and the examples in `docs/examples/`.
If a recording shows different numbers, fix the recording, not the numbers.

## Goals (not predictions)

- **By Oct 6:** the community-marketplace listing is live, at least 4 awesome-list submissions are
  filed, every skill has more than 22 skills.sh installs, and the repository has 300 stars.
- **By Dec 31:** 1,000 stars and 5,000 skills.sh installs across the suite.
