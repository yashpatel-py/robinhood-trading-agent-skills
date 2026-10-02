---
type: agent
expect: {"account_number": ["779903418", "551208867", "660417225"]}
---
You play the `get_pnl_trade_history` tool of a brokerage connector for a fixed test household.
Reply with the tool result only: exactly one of the prepared replies below, copied character for character. No prose, no code fences.

Prepared replies for the account in the arguments:
{{file:_data/get_pnl_trade_history/{input.account_number}.json}}

Pick the reply like this:
1. If `span` is present, reply with replies["span:<span>"], matching the span case-insensitively. A span with no prepared reply is the ordinary tool error "Invalid span: <span>. Use week, month, 3month, ytd or all."
2. If `span` is missing or empty, reply with replies["span:week"] (the documented default).
`symbol` and `cursor` do not change which reply you pick.
