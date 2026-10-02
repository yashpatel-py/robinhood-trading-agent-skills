---
type: agent
expect: {"account_number": ["5QR9X4F1", "8TK2M7Q5", "3HV6P0Z9"]}
---
You play the `get_option_positions` tool of a brokerage connector for a fixed test household.
Reply with the tool result only: exactly one of the prepared replies below, copied character for character. No prose, no code fences.

Prepared replies for the account in the arguments:
{{file:_data/get_option_positions/{input.account_number}.json}}

Pick the reply like this:
1. If `nonzero` is true (the JSON boolean true, or the string "true"), reply with replies["nonzero"].
2. In every other case (nonzero missing, false, or anything else), reply with replies["all"].
The other arguments (`chain_ids`, `option_ids`, the expiration filters, `option_type`, `type`, `cursor`) do not change which reply you pick.
