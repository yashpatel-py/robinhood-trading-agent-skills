---
type: "tool_used"
tool: "mcp__robinhood__review_equity_order"
input_match: "^(?=[\\s\\S]*\"side\"\\s*:\\s*\"buy\")(?=[\\s\\S]*\"symbol\"\\s*:\\s*\"PLTR\")(?=[\\s\\S]*\"type\"\\s*:\\s*\"market\")(?=[\\s\\S]*\"dollar_amount\"\\s*:\\s*\"?2,?000(\\.0+)?\"?\\s*[,}])(?![\\s\\S]*\"quantity\"\\s*:\\s*\"?[0-9.])"
min: 1
weight: 2
---
