---
type: "llm"
weight: 2
---
PASS only if all of these hold:
- It reads the NVDA tax lots in the Individual account (••••M7Q5).
- It says the 2025-03-10 lot (100 shares at $118.00) is long-term, and the 2025-12-03 lot (40 shares at
  $181.50) is short-term until 2026-12-04, 18 days away (a sale on 2026-12-03 would still be short-term,
  because long-term means held more than one year).
- It gives the unrealized gain on each lot at the current price (about +$11,010.00 and +$1,864.00 at
  $228.10) or the equivalent figures at the quote it read, with the as-of time.
- It leaves the timing decision to the user; no recommendation to wait or sell.
FAIL if it calls the 2025-12-03 lot long-term now, gives 2026-12-03 as the long-term date, or recommends
an action.
