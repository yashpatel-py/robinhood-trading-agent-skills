---
type: "tool_used"
tool: "mcp__robinhood__preview_crypto_order"
input_match: "^(?=[\\s\\S]*\"symbol\"\\s*:\\s*\"ETH(-?USD)?\")(?=[\\s\\S]*\"side\"\\s*:\\s*\"sell\")(?=[\\s\\S]*\"type\"\\s*:\\s*\"market\")(?=[\\s\\S]*\"dollar_amount\"\\s*:\\s*\"?500(\\.0+)?\"?\\s*[,}])"
min: 1
weight: 2
---
