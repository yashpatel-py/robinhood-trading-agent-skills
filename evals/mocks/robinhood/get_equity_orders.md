---
type: agent
expect: {"account_number": ["5QR9X4F1", "8TK2M7Q5", "3HV6P0Z9"]}
---
You play the `get_equity_orders` tool of a brokerage connector for a fixed test household.
Reply with the tool result only: one line of compact JSON. No prose, no code fences, no explanation.

Data for the account in the arguments (orders are newest first; `page_size` is the fixed per-page cap):
{{file:_data/get_equity_orders/{input.account_number}.json}}

Build the reply in this order:
1. If `order_id` is in the arguments, the result is {"data":{"orders":[...],"next":null},"guide":GUIDE} holding the one order with that id, or an empty list if there is none.
2. Otherwise start from `orders` and drop every order that fails a filter present in the arguments: `symbol` (exact ticker, case-insensitive), `state` (exact), `placed_agent` (exact), `created_at_gte` (keep an order when its created_at is at or after that instant; a bare date means 00:00 UTC).
3. Paginate what is left in the same order: page 1 is the first `page_size` orders; `cursor` "p2" is the next `page_size`, "p3" the next, and so on. A cursor that points past the last page is an ordinary tool error: "Invalid cursor."
4. `next` is "https://api.robinhood.com/orders/?cursor=pN" (N = the following page number) when more orders remain after this page, otherwise null.
5. Copy every order object exactly as it appears in the data: same keys, same values, same key order. Never invent, edit, merge or reorder orders. GUIDE is the `guide` string from the data.
Shortcut: when none of the filters in step 2 is present, reply with the entry of `unfiltered_replies` for the cursor ("" for page 1) exactly as given.
