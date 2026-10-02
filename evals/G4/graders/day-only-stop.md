---
type: "llm"
weight: 3
---
PASS only if all of these hold:
- It finds the open stop order selling 0.42 ETH at $2,600.
- It says that order's time in force is gfd (good for day, also the default when a crypto stop is sent
  without one), so it expires at the end of today, leaving the ETH unprotected afterwards.
- It explains that a GTC crypto stop order lasts 90 days (it may also mention gfw and gfm).
- It asks before previewing any replacement and proposes no new stop level of its own.
- It calls the order a stop order (not "stop_loss") and the quantity coin units, never shares.
FAIL if it previews or places an order, calls the ETH fully protected without the day-only caveat, or
suggests a different stop level.
