---
type: "llm"
weight: 2
---
PASS only if all of these hold:
- The reply presents a sell of 10 PLTR as a limit order priced at or below the bid ($31.18).
- It explains that it is after the regular session, that a market order would wait for the next open, and
  that the limit is at the bid so it can fill now (a sell limit at the ask may not fill).
- If the ticket it presents is for extended_hours rather than the overnight all_day_hours session, it
  explicitly offers all_day_hours and defines both correctly (extended_hours = pre-/post-market;
  all_day_hours = the overnight 24 Hour Market).
- It ends with the honest handoff: nothing was placed, plus the manual-trading caveat.
FAIL if it presents a market order, prices the sell limit above the bid, uses the regular session without
saying it waits for the open, gives a different quantity, or implies the order was placed.
