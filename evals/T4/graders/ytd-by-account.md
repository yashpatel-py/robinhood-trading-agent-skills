---
type: "llm"
weight: 3
---
PASS only if all of these hold:
- The reply gives the taxable total for the year as +$8,412.33 (Agentic ••••X4F1 +$462.23 and Individual
  ••••M7Q5 +$7,950.10), and shows the Roth IRA (••••P0Z9) +$1,204.00 separately, excluded from the taxable
  total because gains inside a Roth are not taxable in the year.
- It says the short-term/long-term split is not provided by the connector (pointing to Robinhood's tax
  center or 1099), rather than inventing one.
- It does not describe the Agentic TSLA row (an option close listed under the underlying), the ETH row
  (crypto) or the empty-symbol adjustment row as stock sales.
FAIL if it adds the Roth gain into the taxable total, reports a window other than this year, or invents a
short/long split.
