# X thread

<!-- golden-figures: tsla_wash.disallowed_usd tsla_wash.earliest_clean_sale_date spy_auto_exercise.cash_needed_usd spy_auto_exercise.buying_power_usd -->
<!-- allow-figures: -->

**When:** Mon Sep 28, 2026, afternoon (after the Show HN post is up), with the README GIF on post 1.
Every post is at most 280 characters, counting each link as 23. No Robinhood logo, feather or colors
in any image or clip; no tagging Robinhood accounts as if they endorsed it.

## Launch thread

**1/** (attach `docs/media/demo.gif`)

```text
Unofficial, not affiliated with Robinhood. Robinhood opened its brokerage to AI agents in May: your agent can read every account and trade in one. The trap: sell at a loss in the Agentic account, buy the same stock in your IRA within 30 days, and the loss is gone for good.
```

**2/**

```text
Preflight is six open-source, unofficial skills for Robinhood's agent connector (MCP). Before a loss sale or a rebuy it reads every account, every page, and gives you the washed shares, the dollars and the first clean date.
```

**3/**

```text
In the sandbox household: $390.00 of TSLA loss that a Roth IRA buy would erase for good, with a clean sale date of 2026-12-07. The IRA buy sat on page 2 of that account's order history, where a quick check would miss it. (Sandbox data, not a real account.)
```

**4/**

```text
By default it prepares orders with Robinhood's review tools and doesn't place them. In Claude Code a hook blocks every place_*, exercise_* and replace_* call (exit 2); opt-in confirm mode turns a reviewed place_* into a prompt. Elsewhere it's advised only (README table).
```

**5/**

```text
It also checks whether every position has an exit or at least a phone alert, what cash your options would need at auto-exercise ($130,000.00 against $2,480.00 of buying power in the sandbox), and what your agent actually did.
```

**6/**

```text
No Robinhood account needed to try it. The sandbox serves the connector's 81 real tool names with a synthetic household:

git clone https://github.com/yashpatel-py/robinhood-trading-agent-skills
claude --plugin-dir . --mcp-config sandbox/mcp.json --strict-mcp-config
```

**7/**

```text
Evals run on a mock of all 81 tools, with five prompt-injection texts, with the plugin, with skills only and with nothing. Unofficial, not affiliated with Robinhood, not advice.

npx skills add yashpatel-py/robinhood-trading-agent-skills
https://github.com/yashpatel-py/robinhood-trading-agent-skills
```

## HOOD Summit live posts (Tue Sep 29, keynote 5:30 PM CT)

Post only about what was announced and how to use it safely. Never imply Robinhood endorses the kit.
Short clips from the 75-second video (see `README.md` in this folder) work better than screenshots.

If new agent tools are announced:

```text
New tools in Robinhood's agent connector tonight. Our unofficial kit's drift check flags any tool it hasn't seen. In Claude Code its guard blocks tools named place_*, exercise_* or replace_* by pattern and asks before other unknown Robinhood tools run. Update notes to follow.
```

If agent limits or approval settings are announced:

```text
Robinhood just announced controls for AI agents. Good news for anyone letting an agent near a brokerage. Preflight, our unofficial kit, will report them in its readiness check next to its own checks; its guard in Claude Code stays on either way.
```

If nothing agent-related is announced, don't post about the kit that night.

## Replies to have ready

- "Does it trade for me?" → "By default it prepares and simulates orders and doesn't place them. In
  Claude Code a hook enforces that. The README's safety table says what's enforced where."
- "Returns?" → "Not measured, not claimed. It checks taxes, exits and orders; it doesn't pick trades."
- "Is this Robinhood's?" → "No. Unofficial, open source, MIT."
