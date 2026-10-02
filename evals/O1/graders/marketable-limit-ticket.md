---
type: "tool_used"
tool: "mcp__robinhood__review_equity_order"
input_match: "^(?=[\\s\\S]*\"side\"\\s*:\\s*\"sell\")(?=[\\s\\S]*\"type\"\\s*:\\s*\"limit\")(?=[\\s\\S]*\"quantity\"\\s*:\\s*\"?10(\\.0+)?\"?\\s*[,}])(?=[\\s\\S]*\"market_hours\"\\s*:\\s*\"(all_day_hours|extended_hours)\")(?=[\\s\\S]*\"limit_price\"\\s*:\\s*\"?(([12]?\\d|30)(\\.\\d+)?|31(\\.(0\\d*|1([0-7]\\d*|80*)?)?)?)(\\.0+)?\"?\\s*[,}])"
min: 1
weight: 2
---
