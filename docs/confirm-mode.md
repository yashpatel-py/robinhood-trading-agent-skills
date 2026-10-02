# Confirm mode (ships switched off)

*Unofficial. Not affiliated with Robinhood Markets, Inc. Nothing here is investment advice.*

The kit is **simulate-only** by default. The agent prepares and checks orders with Robinhood's review
tools, and a fail-closed hook blocks every order-placing call. Confirm mode is an **opt-in setting of the
Claude Code plugin**. When you turn it on, the agent may call `place_equity_order`,
`place_option_order`, `place_crypto_order` or `place_advanced_order`, but only for the exact order you
reviewed and approved in chat. Claude Code then shows a permission prompt that restates the order, and
nothing goes live until you approve it there.

- `exercise_option` stays blocked in every mode.
- Confirm mode exists only in a Claude Code install **with the plugin**, because it depends on the
  plugin's hooks. Skills-only installs, Claude Desktop, claude.ai, ChatGPT, Codex, Cursor and Gemini
  have no confirm mode. There the kit is simulate-only, full stop.

## Residual risk: read this first

The human permission prompt is the only gate that doesn't rely on files the agent could edit, and it
appears only for order calls the plugin's hook sees. Robinhood
warns that an agent told to act without approval "can place trades without your confirmation"
([Agentic trading overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/),
checked 2026-09-22). Keep the Agentic account funded small.

In practice:
- Everything else the gate checks lives in files in your home directory: the review ledger, the ref_id
  record, your config and the plugin settings. An agent with shell access can edit files. When a Write,
  Edit or Bash call touches those paths, the plugin's `config` hook asks you first. That hook matches
  paths by pattern, so treat it as a tripwire, not a wall.
- The permission prompt is Claude Code's own and is shown for every live order the hook sees in this
  session. **Read it.** It names the tool, side, size, symbol, price, time in force, session, the
  account's last 4, the estimated notional against your cap, and the ticket id you approved in chat.
- The hook doesn't see shell commands. An agent allowed to run Bash without a prompt could start a
  nested `claude` with hooks off and permissions skipped, or call Robinhood's endpoint directly with
  the sign-in token Claude Code stores; neither reaches this prompt. Keep Bash permission prompts on
  in sessions with the Robinhood connector (`docs/safety-model.md`, "Gaps we know about").
- Robinhood's own hard limits still apply on every surface: the Agentic account is funded separately,
  and you can disconnect the agent in one tap.

## Should you turn it on?

That is your call. Here is what changes:

| | Off (default) | On |
|---|---|---|
| Who places the order | You, in the Robinhood app | The agent, after two approvals from you |
| What you approve | Nothing: the agent can't place | 1. the ticket, by id, in chat; 2. the live order, in the permission prompt |
| Caveat | Robinhood doesn't document placing orders by hand inside the Agentic account (handoff text R21) | The residual risk above |
| Scheduled or unattended runs | Never place | Never place |

Confirm mode is new. It hasn't had a long run in real use, and some Claude Code behaviors it relies on
are unverified (see [Known limits](#known-limits)). The gate refuses whenever it can't confirm that a
human is at the prompt.

## Turning it on

1. **Requirements:** Claude Code with the `robinhood-trading` plugin installed, `sh` and `python3` on
   `PATH`, and the plugin option `audit_log` left on (the default). The gate binds each order to its
   review through the audit log's review ledger. With the log off, confirm mode refuses every order.
2. **Set the plugin options.** These are the plugin's `userConfig` values. Claude Code asks for them
   when you enable the plugin; where to change them later depends on your Claude Code version:

   | Option | Value | Why |
   |---|---|---|
   | `order_mode` | `confirm` | Switches the mode on. Anything else, including an empty value, means simulate-only. |
   | `max_order_notional_usd` | an amount, e.g. `500.00` | **Required.** Any live order whose computed notional is above it, or can't be computed, is refused. There is no default. |
   | `review_ttl_seconds` | `60`–`900` (default `300`) | How fresh the review must be. Values outside the range are clamped, and unreadable values mean 300. |

3. **Start a new session.** The session's first context line states the mode:
   - `Robinhood order mode: CONFIRM MODE: ON. …`: it's on.
   - `… CONFIRM MODE: SELECTED BUT UNAVAILABLE (<reason>) …`: you selected it, but something is missing
     (python3, the audit log, or the cap). Every place call stays blocked.
   - `… SIMULATE-ONLY … CONFIRM MODE: INSTALLED, OFF.`: the default.
4. **Optional: your own limits.** Add a `[policy]` section to your kit config (`docs/config.md`). The gate
   re-checks the rules it can evaluate from the order alone (listed below).
5. **Check the gate** on your machine. The self-test runs in a temporary folder and never touches your
   real state:
   ```sh
   python3 "<plugin folder>/hooks/confirm_gate.py" --selftest
   ```

## Turning it off

Set `order_mode` back to `simulate_only` (or clear it), then start a new session. Every `place_*` call
is blocked again. Deleting `hooks/confirm_gate.py` also switches it off, since the guard then blocks
every order, but a plugin update restores the file. Use the setting.

## What a session looks like

Synthetic example (the numbers come from the kit's sandbox household, not a real account):

> **You:** Buy 10 PLTR in my agentic account, limit $31.24, all-day session.
>
> **Agent:** *(runs its checks and `review_equity_order`, then shows the ticket: order, estimate,
> Robinhood's pre-trade checks and market-data disclosure verbatim, and a line
> `Ticket 7f3a1c (these exact parameters)`)*. Place exactly this order? Reply **place 7f3a1c**.
>
> **You:** place 7f3a1c
>
> **Agent:** Submitting ticket 7f3a1c with a new idempotency key. Claude Code will ask you to approve
> the live order.
>
> **Claude Code permission prompt:** `LIVE ORDER - place_equity_order: BUY 10 PLTR - LIMIT 31.24 - gfd -
> all_day_hours - account ****X4F1 - est 312.40 USD (cap 500.00). Matches your review at 20:05:11 ET
> (ticket 7f3a1c). Approve only if this is exactly what you intend.`
>
> **You:** *(approve)*
>
> **Agent:** reports the order id and its state.

If you change anything (quantity, price, type, session, symbol, legs, lots), the agent must review
again. You get a new ticket id and approve again. "Just place it without asking" is not an approval,
and neither is an approval that appears inside a news item, filing or alert text.

## What the gate checks

`hooks/confirm_gate.py` runs inside the plugin's order guard (`hooks/guard.sh`) for every `place_*`
call while `order_mode` is `confirm`. Each check below ends in a refusal. A refused call never reaches
Robinhood, and the agent sees the code and reason.

| Check | Refusal code |
|---|---|
| The call is one of the four `place_*` tools with a review twin (never `exercise_option`, `replace_*` or anything else) | `NOT_CONFIRMABLE` |
| `order_mode` is `confirm` | `CONFIRM_MODE_OFF` |
| A human can answer the prompt: permission mode `default` or `acceptEdits` only (bypass, don't-ask and plan modes are refused), plus the headless checks under [Known limits](#known-limits) | `NOT_INTERACTIVE` |
| The hook input carries the tool, its parameters, the session and the permission mode | `CANNOT_VERIFY_SESSION` |
| The audit log is on | `AUDIT_LOG_OFF` |
| `max_order_notional_usd` is an amount above zero | `CAP_NOT_SET` |
| The call goes to a Robinhood server (a name containing "robinhood", or one that has served a known Robinhood tool) | `NOT_ROBINHOOD_SERVER` |
| `ref_id` is exactly a 36-character UUID (surrounding spaces or a line break make it invalid) | `REF_ID_INVALID` |
| That `ref_id` was never used for a different order | `REF_ID_REUSED` |
| That `ref_id` hasn't already gone through (a pending or failed one may be retried; the prompt then says "retry of ref …") | `DUPLICATE_ORDER` |
| A review of **exactly** these parameters (same canonical fingerprint) succeeded in **this** session within `review_ttl_seconds`, answered by the **same MCP server** as the place call (a review from the sandbox or any other server never binds a live order). The refusal names the fields that differ from your latest review, or says the review came from a different server. | `NO_MATCHING_REVIEW` |
| That review hasn't already been spent on another order | `REVIEW_ALREADY_CONSUMED` |
| The notional can be computed, and the order opens no short option leg | `NOTIONAL_UNKNOWN` |
| The notional is at or under your cap, and under `[policy] max_order_usd` if set | `POLICY_CAP` |
| Your `[policy]` allow and deny lists, `allow_options`, `allow_crypto`, `allowed_sessions` and `max_option_contracts`; a config the kit can't read counts as a refusal | `POLICY_DENY` |
| The kit's order rules (`order_lint.py`), e.g. no review-only keys on `place_option_order` | `LINT_ERROR` |
| The state lock is free and the gate works | `GATE_BUSY`, `GATE_FAILED` |

"Same parameters" is decided by `canon.py`. It ignores differences that don't change the order: the
review-only keys `chain_symbol` and `underlying_type`, documented defaults written out or left off,
`"31.240"` vs `"31.24"`, the order of option legs. Any other difference is a different order.

**How the notional is computed:**
- limit and stop-limit orders: quantity × limit; dollar-amount orders: the dollar amount
- market orders: quantity × the review's ask (buys) or bid (sells)
- stop orders: quantity × the higher of the stop price and that quote (a gap can fill beyond the stop)
- options: sized at the premium, price × 100 × contracts, labelled "premium paid" or "premium received";
  spreads: net price × 100 × quantity, labelled "net premium paid" or "net premium received"; stop orders
  use the stop price
- OCO: the larger of the take-profit and stop prices × quantity
- crypto: the dollar amount, quantity × limit, or the review's estimate

Option market orders, and crypto or market orders the review returned no price for, can't be sized, so
they're refused (`NOTIONAL_UNKNOWN`). So is any order that opens a short option leg: a single
sell-to-open, a short leg without a net debit, more short than long contracts, or a roll into a short. A
short option commits its strike (collateral, assignment), which the gate can't see, not its premium.
Place those yourself in the Robinhood app.

Your `[policy]` rules for household concentration, earnings blackout and orders per day need live
account data that a hook can't fetch. The skill checks them before the review (they show on the ticket),
and the gate doesn't repeat them. An option place call names contracts, not the underlying symbol, so the
gate can't check the allow and deny lists for it. With either list set, every option order is refused.

## Known limits

- **Claude Code behavior not yet verified by this project:** the exact `permission_mode` values in hook
  input, and whether an `ask` is always shown to a human in every mode.
  The gate accepts only `default` and `acceptEdits`, and refuses missing or unknown modes.
- **Headless detection is best effort.** On top of the permission mode, the gate refuses when
  `CLAUDE_CODE_ENTRYPOINT` starts with `sdk` (print mode and the Agent SDK), when
  `CLAUDE_CODE_SESSION_ATTENDED` is `0`, `false` or `no`, and when `CI` is set. These variables are not
  documented interfaces. They can only cause refusals, never approvals. If you run Claude Code with a
  permission-prompt tool or an SDK callback that answers prompts automatically, **do not turn on
  confirm mode**: a program would be answering the prompt that is meant for you.
- **The cap is per order**, not per day or per account.
- **The gate cannot see expirations.** A debit diagonal whose short leg outlives its long leg is still
  sized at its premium, although once the long leg expires the short leg is uncovered.
- **Prices move.** A market order's notional uses the quote from the review, up to `review_ttl_seconds`
  old. Options are sized at a multiplier of 100; adjusted contracts can differ.
- **OCO orders may be unavailable.** On accounts where Robinhood hasn't enabled the OCO tools, the review
  fails, so there is nothing to bind to, and `place_advanced_order` is refused.
- **Native Windows without `sh`:** the plugin's hooks don't run at all, so neither the order guard nor
  confirm mode works. See the safety table in the README and use `integrations/claude-code/settings.deny.json`,
  adding the five lines for your server name if `/mcp` shows one the snippet doesn't list
  ([install-claude-code.md](install-claude-code.md)).
- **The audit log is tamper-evident, not tamper-proof** (`docs/safety-model.md`).

## Files and state

| Path | What it holds |
|---|---|
| `hooks/confirm_gate.py` | The gate. Stdlib Python, no network. |
| `hooks/guard.sh` | The order guard. It calls the gate only for `place_*` calls in confirm mode, and relays only a well-formed `ask`. A crash, a second line or anything else blocks. The gate has its own 8-second watchdog: a slow or stuck gate answers `GATE_FAILED`, which blocks. A hook that Claude Code itself kills at its timeout still gives no opinion (see `docs/safety-model.md`). No hook in the kit ever returns `allow`. |
| `hooks/audit_log.py` | Writes the review ledger after each successful review, and updates the ref_id record after each place call. |
| `~/.local/state/robinhood-skills/ledger.jsonl` | One line per review: the parameter fingerprint, the session (salted hash), the time, the quote and estimate, masked parameters, and `consumed_by` (the ref_id that used it). |
| `~/.local/state/robinhood-skills/refids.json` | ref_id → fingerprint, status (`pending`, `succeeded`, `failed`), time. |

The state folder follows `$ROBINHOOD_SKILLS_STATE`, then `$XDG_STATE_HOME/robinhood-skills`. Folders
are 0700 and files 0600. Nothing is uploaded.

## For contributors

- **The contract between the guard and the gate.** The gate reads the PreToolUse event on stdin.
  - Pass: it prints exactly one `ask` line and exits 0. The reason is 1–600 printable ASCII characters,
    with no quote or backslash.
  - Refusal: it prints `CODE: reason` and exits 3.
  - Crash: it prints `GATE_FAILED: …` and exits 4.

  `guard.sh` blocks with exit 2 on anything but a well-formed ask.
- **Tests:** `python3 -m unittest hooks/tests/test_confirm_gate.py -v` covers every refusal code (the
  `test_k1`–`test_k14` cases and more), the guard end to end, and the default-off promise: with `order_mode` unset or `simulate_only`, every
  `place_*` call is refused even when a matching review exists. `sh hooks/tests/test_guard.sh` covers
  the guard's side, including a crashing or misbehaving gate.
- **Evals:** the `evalkit/templates/K*` cases check the agent's side in the shipped default (confirm
  mode off): no pre-approvals, a new review after a change, no exercise, and no mode switch from pasted
  text. The eval harness runs the agent in don't-ask mode, which the gate always refuses. So the
  approve-and-place path is covered by the hook tests, not by evals.
