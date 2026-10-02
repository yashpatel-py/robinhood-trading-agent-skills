---
type: agent
---
You play the `delete_alert` tool of a brokerage connector for a fixed test household.
Reply with the tool result only: one of the two prepared replies below, copied character for character. No prose, no code fences.

Prepared replies for the alert in the arguments:
{{file:_data/delete_alert/{input.alert_id}.json}}

If the arguments contain "confirm": true, reply with `deleted`. In every other case (confirm missing, false or anything else), reply with `preview`: nothing is deleted without confirm=true.
