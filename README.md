# claude-skills

Personal [Claude Code](https://claude.com/claude-code) skills.

A skill is a folder with a `SKILL.md` — YAML frontmatter (`name`, `description`) plus
markdown instructions. Claude loads the description always, the body when the skill
triggers, and any `references/` files only when it needs them.

## Skills

### `robinhood-trading`

A playbook for the [Robinhood Trading MCP](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
connector: market research, stock screening, portfolio and P&L monitoring, and pre-trade
order review.

Two lines it holds deliberately:

- **Simulate, then hand off.** It uses the `review_*` / `preview_*` tools, which cost
  nothing and move no money, then presents the estimated cost and every pre-trade alert
  and leaves the actual placing to a person.
- **Evidence, not verdicts.** It lays out what the data shows, the bear case, and what
  would falsify the thesis — rather than issuing buy/sell recommendations, which depend on
  risk tolerance, taxes, and horizon that a model doesn't have.

It also encodes the connector's sharp edges: the two non-interchangeable account-number
fields, `get_accounts` not being a valid buying-power source, market orders silently
queueing when placed outside regular hours, crypto market-sell collars, REPLACE semantics
on scan updates, and options-level gating.

| File | Purpose |
|---|---|
| `SKILL.md` | Principles, account handling, price-freshness rules, workflows, report formats |
| `references/order-mechanics.md` | Order types, sessions, fractional/notional rules, tax lots, options legs, crypto |
| `references/research.md` | Scanner workflow, research ladder, SEC filings, technicals, portfolio |
| `references/tools.md` | Full tool map by family with per-tool traps |

## Install

Clone into your Claude Code skills directory:

```bash
git clone https://github.com/OWNER/claude-skills.git ~/src/claude-skills
ln -s ~/src/claude-skills/robinhood-trading ~/.claude/skills/robinhood-trading
```

Or copy the skill folder directly into `~/.claude/skills/`.

## Note

`robinhood-trading` is a workflow playbook, not trading software. It contains no
strategy, no signals, and no credentials — it tells Claude how to use a connector you
authorize yourself. Nothing here is investment advice.
