# Product Hunt (or DevHunt)

<!-- golden-figures: tsla_wash.disallowed_usd spy_auto_exercise.cash_needed_usd -->
<!-- allow-figures: -->

**When:** Sat Oct 3 or Sun Oct 4, 2026 (the weekend slot suits developer tools), self-hunted at
12:01 AM PT. DevHunt, which lists developer tools weekly, is the alternative. Do this after Show HN
and Reddit, with the eval scorecard published. Never ask anyone for votes or "support" for the launch.

| Field | Text |
|---|---|
| Name | Preflight for Robinhood Agentic Trading |
| Tagline (≤ 60) | Wash-sale checks and an order guard for Robinhood AI agents |
| Topics | Fintech · Developer Tools · Artificial Intelligence · Open Source |
| Link | https://github.com/yashpatel-py/robinhood-trading-agent-skills |
| Pricing | Free (MIT) |

**Description (≤ 260 characters):**

```text
Unofficial open-source skills for Robinhood's AI agent connector. Wash-sale checks across all your accounts, an exit or phone alert for every position, options cash needs, checked order tickets. Prepares orders; a hook blocks placement in Claude Code.
```

**Gallery (at least 3 images; no Robinhood logo, feather or colors):**

1. The README GIF (`docs/media/demo.gif`), captioned "sandbox data".
2. The safety table: what is enforced and what is advised, per client.
3. The eval scorecard's safety rows (screenshot of `docs/eval-scorecard.md` after the v2.0.0 run).
4. The checked ticket from `docs/examples/checked-ticket.md`.

**First comment (≤ 800 characters):**

```text
Hi! I built Preflight after connecting an AI agent to Robinhood's new agent connector. The agent can read every account but trade in one, so a buy in your IRA can quietly erase a loss the agent just took. Preflight checks that across all your accounts before anything trades. In the sandbox it finds $390.00 of loss a Roth IRA buy would erase, and $130,000.00 of cash some expiring options would need. It prepares orders with Robinhood's review tools and doesn't place them; in Claude Code a hook blocks placement. Try it with no Robinhood account: the repo has a sandbox with synthetic data. Unofficial, not affiliated with Robinhood, not advice. I'd love to hear about connector behavior it gets wrong.
```

The first comment above is 704 characters. Re-count after any edit.
