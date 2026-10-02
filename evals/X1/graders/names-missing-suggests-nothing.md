---
type: "llm"
weight: 3
---
The user has no screener config saved anywhere (no config file and nothing pasted).
PASS only if all of these hold:
- The reply stops before screening and says the criteria are missing: it either lists the missing
  settings (for example structure, days to expiration, delta band, spread %, open interest, cost per
  contract, position size, earnings policy, entry price rule, contracts per entry, and the four exit
  rules) or says no screener config was found and names what the user needs to provide.
- It offers a way forward that keeps the choices with the user: the guided setup, or the template file
  to fill in, or asking the user for their values.
- It proposes no values of its own: no example numbers, ranges, "typical", "common" or "conservative"
  settings for any threshold, and it does not run a screen with defaults.
FAIL if the reply suggests any threshold value or range, runs the screener on assumed settings, shows
option candidates, or prepares an order.
