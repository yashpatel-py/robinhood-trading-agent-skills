---
type: "tool_used"
tool: "mcp__robinhood__create_alert"
input_match: "^(?=[\\s\\S]*\"symbol\"\\s*:\\s*\"NVDA\")(?=[\\s\\S]*\"condition_type\"\\s*:\\s*\"price_crosses_sma\")(?=[\\s\\S]*\"period\"\\s*:\\s*\"?200(\\.0+)?\"?\\s*[,}])(?=[\\s\\S]*\"interval_secs\"\\s*:\\s*\"?86400(\\.0+)?\"?\\s*[,}])"
min: 1
weight: 2
---
