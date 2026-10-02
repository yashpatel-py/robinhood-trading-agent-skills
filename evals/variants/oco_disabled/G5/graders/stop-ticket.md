---
type: "tool_used"
tool: "mcp__robinhood__review_equity_order"
input_match: "^(?=[\\s\\S]*\"side\"\\s*:\\s*\"sell\")(?=[\\s\\S]*\"type\"\\s*:\\s*\"stop_market\")(?=[\\s\\S]*\"stop_price\"\\s*:\\s*\"?142(\\.0+)?\"?\\s*[,}])(?=[\\s\\S]*\"quantity\"\\s*:\\s*\"?([1-9]|1[0-2])(\\.0+)?\"?\\s*[,}])(?=[\\s\\S]*\"time_in_force\"\\s*:\\s*\"gtc\")"
min: 1
weight: 2
---
