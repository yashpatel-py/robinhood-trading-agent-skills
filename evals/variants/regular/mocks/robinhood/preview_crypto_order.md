---
expect: {"rhs_account_number": ["779903418"]}
---
{"data":{"symbol":"{{input.symbol}}","side":"{{input.side}}","type":"{{input.type}}","quantity":"{{input.quantity}}","dollar_amount":"{{input.dollar_amount}}","limit_price":"{{input.limit_price}}","stop_price":"{{input.stop_price}}","time_in_force":"{{input.time_in_force}}","quote":{{file:_data/crypto_quote/{input.symbol}.json}},"fees":"0.00","validation_errors":[]},"guide":"Show the estimated total and fees. After the user confirms, call place_crypto_order."}
