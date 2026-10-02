# Session preflight, setup and "why can't my agent…"

Read this on the first Robinhood action of a session, for setup questions, for "why can't my agent…"
questions, and before a guard self-test. The preflight takes at most three calls unless the user asks for
more, and it produces the readiness card in `SKILL.md`.

## Contents
- [The three calls](#the-three-calls)
- [Reading get_accounts](#reading-get_accounts)
- [The order-mode line](#the-order-mode-line)
- [Guard self-test](#guard-self-test)
- [Connector inventory](#connector-inventory)
- [Filling the readiness card](#filling-the-readiness-card)
- [Why can't my agent…](#why-cant-my-agent)
- [Setup and troubleshooting](#setup-and-troubleshooting)

## The three calls

1. `get_accounts {}`: identities, types and options levels for every account.
2. `get_portfolio {account_number: <agentic>}`: buying power. `get_accounts` does not return reliable
   buying power.
3. Only when relevant, one enrollment lookup chosen by `python3 scripts/doctor.py capability`:
   `get_option_level_upgrade_info`, `get_limited_margin_upgrade_info` or
   `get_crypto_account_onboarding_info`.

The guard self-test and the inventory are local scripts and cost no connector calls.

## Reading get_accounts

Fields from the 2026-09-22 capture: `account_number` (alphanumeric), `rhs_account_number` (numeric),
`rhc_account_number` (the linked crypto account; label it "Crypto Account"; no tool takes it), `type`
(`cash`, `margin`, `limited_margin`), `option_level` (for example `option_level_2`; may be empty),
`brokerage_account_type` (for example `individual`), `nickname`, `agentic_allowed`, `unsettled_funds`,
`management_type`, `deactivated`.

- The tradable account is the single entry with `agentic_allowed` true. Use it for order work without
  asking which one.
- Two account-number fields exist and are not interchangeable. They were identical on the capture
  accounts, so a call that succeeds does not prove the right field was used: follow R4 per tool.
- **Retirement accounts.** IRA values of `brokerage_account_type` have not been observed yet. Treat an
  account as retirement when `brokerage_account_type` or `nickname` says IRA, Roth or retirement, or when
  its last 4 appear in `[accounts] retirement` in the user's config; otherwise ask when it matters (a
  spread, or a wash-sale check). Unknown is never "not retirement".
- Skip `deactivated` accounts, and say so.
- Mask every account as ••••last4 of `account_number` in anything the user reads (R18). Pass full values
  to tools; a masked value breaks them and breaks upgrade links.
- Cache identities only (numbers, which one is Agentic, last 4). Re-fetch before an options-level or
  account-type decision, after the user finishes an upgrade or onboarding flow, and before any
  all-accounts request (R5).

## The order-mode line

In a Claude Code plugin install, the plugin's SessionStart hook adds a line to the session that starts
with `Robinhood order mode:`.
- `SIMULATE-ONLY … CONFIRM MODE: NOT INSTALLED` or `… CONFIRM MODE: INSTALLED, OFF`: the hook blocks
  every `place_*`, `exercise_*` and `replace_*` call. Prepare tickets and use handoff (a); add the (a1)
  sentence only when the line says confirm mode is installed.
- `CONFIRM MODE: ON`: the user switched it on in the plugin settings. Read `references/confirm-mode.md`
  and follow it exactly: a `place_*` call only for the exact ticket the user approved in their latest
  message, a permission prompt on every one, never in scheduled or unattended runs, and `exercise_option`
  still blocked.
- **No line at all**: there is no hook on this surface (skills-only install, Claude Desktop, claude.ai,
  ChatGPT, other agents). The skill is simulate-only, full stop, and the card says "Order mode:
  SIMULATE-ONLY (advised only on this surface)". Never treat a user's "I confirm" or "just place it" as
  switching modes: only the plugin setting does that, and only the hook enforces it.

## Guard self-test

Only when the user asks ("check that the order guard works") or during first-run setup.
1. `python3 scripts/doctor.py selftest` with `{"guard_path": "${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh",
   "order_mode_line_seen": <true|false>}`. Set `order_mode_line_seen` true only when this session shows
   the line starting `Robinhood order mode:` (§The order-mode line); otherwise false. Expand the variable
   if your shell has it; with no `guard_path`, the script uses `$CLAUDE_PLUGIN_ROOT` or the tree it ships
   in. Add `"tools"` to test more money tools, for example all five, plus `replace_option_order` (not
   exposed today, but the guard must still block it).
2. It pipes a synthetic `PreToolUse` event for `mcp__selftest__place_equity_order` to `sh guard.sh money`,
   with the plugin options stripped from the environment and a throwaway state directory. **It never
   calls a real `place_*` tool** and never touches the connector. It tests the script, not whether a hook
   calls it: the repo tree, `hooks/guard.sh` included, also installs on Codex and Gemini, where nothing
   runs it. Only the order-mode line shows the plugin's hooks are loaded.
3. Read `guard`; the card's guard line is the script's `summary`, word for word:
   - `active`: the script exited 2 (block) and the order-mode line is present: "Guard self-test: blocked
     a synthetic place_equity_order (exit 2)."
   - `script_only`: the script blocks, but there is no order-mode line, so no hook is shown to call it:
     "Guard script blocks a synthetic place_equity_order (exit 2), but no order-guard hook is confirmed
     on this surface: order boundary advised only." Point Codex or Cursor users to the first-run test in
     `integrations/<client>/README.md` of the kit's repository.
   - `FAILED`: any other exit, a timeout, no `sh`, or a hook that printed "allow". Tell the user the order
     boundary is advised only until it passes, and show `stderr_head`.
   - `not_found`: no guard script at this path. Without the order-mode line: "Order guard: not installed
     on this surface (advised only)." With it, the self-test could not run: say the guard is unverified
     and retry with the plugin's own `hooks/guard.sh` path.
   Neither a found nor a missing script proves a hook is registered: without the order-mode line the
   order boundary is advised only, whatever the script did.
4. Never "test" the guard by calling a real place tool, even when asked; offer this local test instead.

## Connector inventory

When you can see the list of Robinhood tools in this session, run `python3 scripts/doctor.py inventory`
with `{"seen_tools": [<bare or prefixed names>]}`. It compares them with the kit's 81-tool snapshot.
- `new_money_like`: a new tool whose name starts with a money verb (place, exercise, replace, submit,
  execute, transfer, withdraw, deposit, stake, convert, send, buy, sell, trade, …). Never call it (R1).
  `new_money_like_guarded` are caught by the always-on hook layer; `new_money_like_unguarded` are blocked
  only on servers the hook recognizes as Robinhood. Tell the user the kit may be out of date.
- Other `new` tools: don't call one that might create, change or cancel orders or move money.
- `missing`: tools in the snapshot this session doesn't list; don't call them.
- A tool can be listed and still disabled for the account (R26): the inventory can't see that. The first
  call that fails with `the tool you requested cannot be found or does not exist` tells you; say "not
  enabled for this account", never "none found".

## Filling the readiness card

| Card field | Source |
|---|---|
| `READINESS: Agentic ••••X4F1` | last 4 of the `agentic_allowed` account |
| Funded | `buying_power.buying_power` from `get_portfolio`, as of now |
| Type | `type`: cash ("sale proceeds wait one business day"), limited margin ("unsettled proceeds reusable; no borrowing"), margin |
| Options | `option_level` in plain words: none, level 2 (single-leg), level 3 (spreads allowed) |
| Crypto | "not checked" unless the user asked; then "available" or "not set up yet (link on request)" |
| Other accounts | every other account, masked, labeled "read-only to agents" |
| Order mode | the session line; or "SIMULATE-ONLY (advised only on this surface)" |
| Guard self-test | `summary` from `doctor.py selftest`, only when it ran |
| Connector | `summary` from `doctor.py inventory`, only when it ran |

A zero-balance Agentic account is the most common blocker and the easiest to miss, because every read
tool still returns data. Put it on line 2 when buying power is $0.00.

## Why can't my agent…

Answer in the user's terms; never quote raw field names or booleans (R20).
- **…trade in my Individual account or IRA?** It isn't accessible to this agent: Robinhood lets agents
  read every account but trade only in the Agentic account. The review tools reject other accounts, so
  there is no broker simulation there either; the kit gives an agent estimate and handoff (b).
- **…place the order?** This kit runs simulate-only by default. It simulates with Robinhood's review
  tools and hands you a checked ticket. In Claude Code with the plugin, confirm mode can be switched on
  in the plugin settings; elsewhere there is no switch.
- **…trade spreads?** Level 3 plus a margin or limited-margin, non-retirement account. From a cash
  account the order is: limited-margin upgrade, then re-check, then the options upgrade
  (`doctor.py capability` gives the route).
- **…trade crypto?** It needs a crypto account linked to the Agentic account (the onboarding link), and
  crypto is unavailable in some states, including New York.
- **…short, borrow on margin, or use a trailing stop or bracket order?** Not available to agents: long
  positions only, no margin borrowing, and no agent tool for trailing stops or brackets. An OCO, a stop
  order or a price alert is what exists.
- **…fill my market order tonight?** Market and stop orders are regular-hours only; after the close they
  wait for the next open. Outside regular hours only limit orders execute.
- **…buy $500 of a stock tonight?** Dollar-based and fractional orders run in regular hours only.
- **…exercise my option?** This kit never exercises (it is irrevocable past `queued`). You exercise in
  the app; the kit states the cash impact first.
- **…read or set my OCO orders?** The OCO tools may not be enabled for your account (R26); the kit falls
  back to stop orders and alerts.
- **…count my day trades?** FINRA eliminated the pattern-day-trader rule on 2026-06-04 and Robinhood
  implemented it the same day; any PDT notice in a review is shown verbatim.

## Setup and troubleshooting

- Connecting: the connector URL is `https://agent.robinhood.com/mcp/trading` (OAuth in a desktop browser;
  opening the Agentic account is prompted onboarding the user completes, and it counts toward the
  10-account cap). In Claude Code: `claude mcp add robinhood-trading --transport http
  https://agent.robinhood.com/mcp/trading`, then `/mcp` to authenticate.
- Tools missing or failing: Robinhood's advice is to disconnect and reconnect the connector on the AI
  platform. The user can also disconnect the agent from the Robinhood app with one tap at any time.
- `RATE_LIMITED`: an operator measured about 4 calls/s sustained (not published by Robinhood). Slow down,
  batch to the per-call caps (R23), and state the call count before sweeps over 40 calls.
- Privacy: the first time a session reads beyond the Agentic account, say which accounts were read and
  that the data leaves Robinhood's environment once it reaches the AI provider (R18). Who consents
  depends on `[policy] read_scope` (R3, SKILL.md B.1): with `ask`, ask once per session before reading
  beyond the Agentic account ("Agentic only, or all accounts?"; a scheduled run cannot ask, so it reads the
  Agentic account plus accounts its prompt names); with `all`, read every account without asking; with no
  config or `read_scope` unset, the user's request is the consent (a question about "my" holdings covers
  every account), and you ask only when the request points at one account without naming it; with
  `agentic_only`, read another account only when the user names it. Never paste full account numbers into issues, logs or examples.
