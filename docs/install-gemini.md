# Install: Gemini CLI

*Unofficial. Not affiliated with Robinhood Markets, Inc.* Safety label: **Verify**. The
extension's own server entry is configured to hide the order-placing tools from the model, and this
stays advised until a first-run test shows Gemini CLI hiding them; everything else is advised
([why](safety-model.md)). **Untested:** signing in to Robinhood's connector through Gemini CLI has
not been verified by this project yet.

> **Before you start**
> - US only. You need a primary individual investing account in good standing.
> - The Agentic account is opened through prompted onboarding when you first connect an agent, from a desktop browser, and it counts toward the 10-account cap.
> - Crypto needs a Robinhood Crypto account and is unavailable in some states, including New York.

**1. Install the extension:**

```sh
gemini extensions install https://github.com/yashpatel-py/robinhood-trading-agent-skills
```

This adds the six skills, a short `GEMINI.md` with the order rules, and an MCP server entry named
`robinhood` for `https://agent.robinhood.com/mcp/trading`. That entry lists `place_equity_order`,
`place_option_order`, `place_crypto_order`, `place_advanced_order` and `exercise_option` under
`excludeTools`, so Gemini should never offer them to the model ("Verify": not yet confirmed on a
real Gemini CLI build; step 4 checks it).

**2. Sign in.** Start `gemini`, check the server with `/mcp`, and complete Robinhood's OAuth sign-in
in a desktop browser if prompted. If sign-in fails, please open a `connector-behavior` issue with your
Gemini CLI version: that result decides whether this page keeps its "untested" label.

**3. One server entry only.** The exclusion applies to this extension's `robinhood` entry. If you
also added Robinhood's connector under another name in your own settings, nothing hides the order
tools there. Remove the other entry, or add the same five names to its `excludeTools`. The same goes
for a server named `robinhood` in your own `~/.gemini/settings.json` or the project's
`.gemini/settings.json`: Gemini CLI uses that entry instead of the extension's, so the extension's
`excludeTools` no longer applies.

**4. Check the order tools are hidden.** Run `/mcp`: the `robinhood` server should list none of the
five tools above. If any is listed, treat the rule as advised only, and please report it in a
`connector-behavior` issue with your Gemini CLI version (this is first-run test D in
[safety-model.md](safety-model.md)).

**5. Check it works.** Ask "What's my buying power?" (one line from `get_portfolio`, account masked as
`••••` plus its last 4), then "What stocks do I hold?" It should read every account, name each one,
and never present one account's holdings as "your positions".

**Update and remove.** `gemini extensions update robinhood-trading`; `gemini extensions uninstall
robinhood-trading`. Disconnect the agent in the Robinhood app when you're done.
