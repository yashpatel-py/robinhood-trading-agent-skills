---
type: agent
---
You play the `preview_scan` tool of a brokerage connector for a fixed test market.
Reply with the tool result only: exactly one of the prepared replies below, copied character for character. No prose, no code fences.

Prepared replies, one per result set:
{{file:_data/preview_scan/replies.json}}

Pick the result set from the `filters` argument:
1. If `filters` is missing or empty, reply with the ordinary tool error "At least one filter is required."
2. If any filter is an RSI filter (filter_type "FILTER_TYPE_RSI", or an expression containing "rsi(") tested with less-than (a predicate containing LESS, or "<" in the expression), reply with replies["rsi_below"].
3. Otherwise, if any RSI filter is tested with greater-than (a predicate containing GREATER, or ">" in the expression), reply with replies["rsi_above"].
4. Otherwise reply with replies["default"]. `columns` does not change the reply.
