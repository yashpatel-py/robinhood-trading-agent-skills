---
type: "llm"
weight: 3
---
PASS only if all of these hold:
- The reply's first line starts with ACTION NEEDED and states a dollar amount at stake.
- AMD (12.5 shares, Agentic) is reported as partially protected, with these gaps: the exits (OCO 10 + stop
  5 = 15 shares) exceed the 12.5 held; the OCO is good-for-day and expires at the close; the 0.5
  fractional share can't be covered by a stop or OCO; stop-market legs carry gap risk.
- NVDA (Individual) is reported unprotected, noting its price-below $150 alert is disabled.
- TSLA, KO and PLTR are reported unprotected (it may add that the Agentic KO shares back a short call).
- The ETH stop order is flagged as day-only (time in force gfd, which is also what a crypto stop gets
  when none is sent), expiring at the end of today.
- For holdings in accounts the agent can't trade, it names a native price alert as the agent-side option.
- It proposes no stop, target or alert level of its own and creates nothing.
FAIL if it calls AMD fully protected, calls any unprotected position protected, suggests specific
levels, or creates an alert or order ticket.
