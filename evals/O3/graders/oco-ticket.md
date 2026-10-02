---
type: "tool_used"
tool: "mcp__robinhood__review_advanced_order"
input_match: "^(?=[\\s\\S]*\"side\"\\s*:\\s*\"sell\")(?=[\\s\\S]*\"quantity\"\\s*:\\s*\"?([1-9]|1[0-2])(\\.0+)?\"?\\s*[,}])(?=[\\s\\S]*\"take_profit_limit_price\"\\s*:\\s*\"?180(\\.0+)?\"?\\s*[,}])(?=[\\s\\S]*\"stop_loss_stop_price\"\\s*:\\s*\"?142(\\.0+)?\"?\\s*[,}])(?![\\s\\S]*\"market_hours\"\\s*:\\s*\"(?!regular_hours\"))"
min: 1
weight: 2
---
