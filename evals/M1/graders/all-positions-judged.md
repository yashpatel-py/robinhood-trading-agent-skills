---
type: "llm"
weight: 1
---
PASS if the reply accounts for all three open option positions against the user's rules: the long SPY
650 calls x2 (profit target and/or the 7-day time stop fired: about +78% at the bid, 4 days to expiration),
the short KO 70 call x1 (stop loss and/or time stop fired: the short is marked at the ask, a loss of about
109% of the premium, 4 days to expiration), and the AMD 165 call. The closed TSLA put must not appear as a
held position.
FAIL if a position is missing, the KO short's loss is shown as a gain (sign error), or the TSLA put is
treated as open.
