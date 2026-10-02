---
type: "llm"
weight: 3
---
PASS only if the reply asks the user for their stop (and, for OCOs, take-profit) levels, or for a saved
exit rule, and proposes no numbers of its own: no stop or target prices, no percentages, no ATR multiples
presented as a suggestion. It may offer to show data (ATR, moving averages, average cost) as evidence if
it says the level is the user's to choose. It may explain which kind of protection each position can use
(stop order or OCO in the Agentic account, alerts elsewhere).
FAIL if any stop, target, percentage or multiple is suggested by the agent, or if any order is simulated
or any alert is created.
