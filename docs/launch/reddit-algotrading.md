# r/algotrading: educational post (second wave)

<!-- golden-figures: tsla_wash.disallowed_usd -->
<!-- allow-figures: $0.10 -->

**When:** Wed Sep 30 to Fri Oct 2, 2026. **Rules first:** the sidebar says the sub is not for
promoting your blog, channel or firm, and it links a "rules of engagement" wiki. Read both. This is an
educational write-up; the repository is mentioned once, at the end, as the source of the test suite.
No calls to action, no install command in the body.

**Title:**

```text
7 silent ways agents get Robinhood's MCP connector wrong (and the unofficial test suite that catches them)
```

**Body:**

```text
Robinhood's agent connector exposes 81 MCP tools (its support page lists 57). This list comes from building a test suite against a mock of all of them. These are the mistakes that fail quietly: the call succeeds, and the result is wrong.

1. Two account numbers, one parameter name. Accounts have an alphanumeric account_number and a numeric rhs_account_number. The two P&L tools take a parameter called account_number but want the rhs value. On some accounts the two are identical, so the wrong call still works there and fails elsewhere.

2. Closed option positions. get_option_positions returns positions you already closed unless you pass nonzero=true. An agent "monitoring" your options will happily monitor a contract that expired last month.

3. After-hours market orders. A market order tagged regular_hours at 8 pm doesn't error; it queues for the next open. Tagged to the extended or overnight session, it's rejected. Only limit orders execute outside regular hours, and "marketable" is side-aware: a sell at or below the bid, not at the ask.

4. The wash sale your 1099 won't show. The agent can read every account but trade in one. Sell at a loss in the agent's account, and a buy of the same stock within 30 days in any other account washes it; in an IRA the loss is gone for good. In our fixture the IRA buy sits on page 2 of that account's order history, behind three unfilled orders, and costs $390.00 of loss. Read every page.

5. Pre-trade checks that look like "none". The equity review returns its checks as an object, {} when there are none, not a list. Parse it as a list and every order looks clean. It also returns a market-data disclosure that has to be shown verbatim.

6. Listed tools that aren't there. The OCO tools appear in the tool list but can answer "the tool you requested cannot be found or does not exist" for a given account. Treat that as "not enabled", never as "no OCOs", or you'll tell someone an unprotected position is fine. When they do work: whole shares only, regular hours only, each price at least 0.25% from market and $0.10 apart.

7. No market-hours tool. Agents guess one (get_market_hours does not exist), and the index quote's state field came back empty in our capture. Use a calendar plus quote timestamps plus whatever time the user states.

Bonus: text inside tool results (news, filings, alert labels, scan titles) can address the agent directly. Our fixture plants five such texts; a well-behaved agent quotes them and does nothing.

The mock, the fixture and the cases are open source (MIT), with a sandbox MCP server you can point any client at: https://github.com/yashpatel-py/robinhood-trading-agent-skills. Unofficial and not affiliated with Robinhood. Corrections welcome, especially connector behavior I got wrong.
```

If a moderator objects, remove the link line; the list stands on its own.
