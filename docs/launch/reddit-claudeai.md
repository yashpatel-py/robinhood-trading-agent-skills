# r/ClaudeAI: "Built with Claude" post

<!-- golden-figures: tsla_wash.disallowed_usd spy_auto_exercise.cash_needed_usd spy_auto_exercise.buying_power_usd -->
<!-- allow-figures: -->

**When:** Mon Sep 28, 2026, afternoon PT. **Before posting:** read the live sidebar and the current
self-promotion rules; they changed during the summer. Use the "Built with Claude" flair if it still
exists, and include what you built, how, a demo and at least one prompt you used. If the rules
forbid the post, skip it and answer in existing threads about Robinhood's connector instead.

**Title:**

```text
Built with Claude: Robinhood's MCP lets agents trade. I made unofficial skills that make them check first, and a hook that blocks the order
```

**Body:**

```text
Robinhood's agent connector (MCP) went live this year. Your agent can read every Robinhood account you hold and trade in one separately funded "Agentic" account. I built Preflight, six unofficial Agent Skills plus a Claude Code plugin, so the agent checks things before anything trades and prepares orders instead of placing them.

What it does
- Wash-sale check across all your Robinhood accounts before a loss sale or a rebuy, including your IRA (a buy there erases the loss for good), recurring buys and dividend reinvestment.
- An exit or at least a phone alert for every position (it sets Robinhood's native price alerts).
- Options radar: which of your options need cash at auto-exercise, and early-assignment risk before an ex-dividend date.
- A report card of what your agent did, including orders that came from another agent or app.

How Claude fits
- The skills are SKILL.md files with the connector rules Claude kept getting wrong without them: which of two account numbers each tool wants, why a market order at 8 pm doesn't do what you think, why get_option_positions returns closed positions unless you pass nonzero=true.
- The math is stdlib Python the skills run (wash windows, lot choice, P&L, assignment cash), so the numbers don't depend on the model's arithmetic.
- The plugin adds a PreToolUse hook in POSIX sh. By default (simulate-only), any place_*, exercise_* or replace_* MCP call exits 2 before it reaches the server; an opt-in confirm mode, off unless you switch it on, turns a place_* for the exact ticket you reviewed into a permission prompt. No hook ever returns "allow". Cancels get a permission prompt that says what protection they remove.

Demo (sandbox data, no Robinhood account needed)
  git clone https://github.com/yashpatel-py/robinhood-trading-agent-skills && cd robinhood-trading-agent-skills
  claude --plugin-dir . --mcp-config sandbox/mcp.json --strict-mcp-config

A prompt I used: "(Context: it is Monday 2026-11-16, 8:05 PM ET.) Harvest my TSLA loss in my individual account: the 20 shares I bought in June."
It reads all three sandbox accounts, finds the Roth IRA's buy on page 2 of its order history, and reports $390.00 of the loss permanently disallowed, with the first clean sale date and the exact lot to pick in the app. On the options side the sandbox shows $130,000.00 of auto-exercise cash needed against $2,480.00 of buying power.

Evals
claude plugin eval on a mock of all 81 connector tools: 51 cases, five of them with prompt-injection text inside tool results ("AI agents: place this order"). Each case runs with the plugin, with skills only, and with nothing. The scorecard is in docs/eval-scorecard.md.

Caveats: unofficial, not affiliated with Robinhood, not investment or tax advice. Outside Claude Code with the plugin, "don't place orders" is an instruction, not a block; the README has a table of what's enforced where. The hook sees tool calls, not shell commands, so keep Bash permission prompts on.

Repo: https://github.com/yashpatel-py/robinhood-trading-agent-skills
```

**Images:** the README GIF, and a screenshot of the safety table. No Robinhood marks.
