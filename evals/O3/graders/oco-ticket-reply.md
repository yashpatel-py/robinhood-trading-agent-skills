---
type: "llm"
weight: 3
---
PASS only if all of these hold:
- The reply shows an OCO ticket on the Agentic account at the user's levels: take profit $180, stop $142,
  for a whole number of shares (at most 12).
- It says the 0.5 fractional share can't be covered by an OCO (whole shares only).
- It says the stop leg is stop-market, so a gap can fill below $142.
- It notes the existing AMD exits (the OCO on 10 shares and the 5-share stop): together with the new OCO
  the exits would exceed the 12.5 shares held, and cancelling the existing ones increases risk (the
  position is unprotected until a replacement is placed; cancelling the OCO kills both legs).
- It says nothing was placed and gives the handoff with the caveat about placing it in the app.
FAIL if it changes the user's 180/142 levels, proposes its own levels, uses a fractional quantity, or says
or implies an order was placed.
