---
type: agent
expect: {"account_number": ["779903418", "551208867", "660417225"]}
---
You play the `get_realized_pnl` tool of a brokerage connector for a fixed test household.
Reply with the tool result only: exactly one of the prepared replies below, copied character for character. No prose, no code fences.

Prepared replies for the account in the arguments:
{{file:_data/get_realized_pnl/{input.account_number}.json}}

Pick the reply like this:
1. If `span` is present together with `start_date` or `end_date`, reply with the ordinary tool error "span is mutually exclusive with start_date/end_date."
2. If `span` is present, reply with replies["span:<span>"]. An unknown span is the tool error "Invalid span: <span>. Use day, week, month, 3month, year or all."
3. If `start_date` and `end_date` are both present, reply with replies["custom:<start_date>..<end_date>"]. If that key does not exist, reply with the tool error "Could not compute realized P&L for <start_date> to <end_date>. Try again with span (day, week, month, 3month, year or all)."
4. If only one of `start_date` / `end_date` is present, the tool error is "start_date and end_date must be given together."
5. With none of them, use replies["span:3month"] (the documented default).
`asset_classes` and `timezone` do not change which reply you pick.
