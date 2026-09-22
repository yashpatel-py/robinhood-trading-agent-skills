# Options criteria — user-defined

This file is the configuration for the options workflow. **Every value here must be set by
the user.** Nothing in this file may be filled in by an agent.

That rule exists because these numbers *are* the strategy. A delta band, a DTE window, and
a stop level fully determine what gets traded and what gets closed — choosing them is
making an investment decision, which depends on risk tolerance, capital, taxes, and
horizon that a model cannot assess. An agent that quietly picks a default here has issued
investment advice while appearing to follow instructions, which is worse than refusing.

If a value below still reads `<UNSET>` when the workflow runs, stop and ask the user for
it. Do not proceed on a guess, and do not infer it from what "looks reasonable" or from
what the user chose for a different parameter.

## Capital and risk

```yaml
account:              "<UNSET>"   # last 4 of the agentic account to trade in
max_position_pct:     <UNSET>     # max % of account value in one position
max_concurrent:       <UNSET>     # max open option positions at once
max_premium_per_contract: <UNSET> # $ ceiling per contract (cost = premium x 100)
reserve_cash:         <UNSET>     # $ never deployed
```

## Universe — which underlyings are eligible

```yaml
price_min:            <UNSET>     # underlying share price floor
price_max:            <UNSET>     # underlying share price ceiling
min_avg_volume:       <UNSET>     # avg daily share volume; liquidity floor
symbols_allowlist:    []          # optional: only these tickers
symbols_blocklist:    []          # optional: never these tickers
```

## Contract selection

```yaml
structure:            "<UNSET>"   # long_call | long_put | debit_call_spread | debit_put_spread
                                  #
                                  # At Level 3 with small capital these four are the
                                  # realistic set. Covered calls need 100 shares and
                                  # cash-secured puts need strike x 100 in cash, so both
                                  # are out below a few thousand dollars. CREDIT spreads
                                  # are Level 3 too but post collateral equal to the
                                  # width x 100 — a $5-wide credit spread ties up $500 —
                                  # so they are unavailable at this size regardless of
                                  # approval level.
                                  #
                                  # A debit spread buys one strike and sells a further
                                  # one, so the short leg's credit offsets the cost. The
                                  # same directional view can cost a fraction of the
                                  # outright long option, which is what puts higher-priced
                                  # underlyings within reach. The tradeoffs are real:
                                  # gain is capped at the width, and you cross two
                                  # bid-ask spreads instead of one — meaningful when the
                                  # net debit is under a dollar.

spread_width_min:     <UNSET>     # spreads only: min distance between strikes, in $
spread_width_max:     <UNSET>     # spreads only: max distance. Width x 100 is the
                                  # ceiling on what the spread can ever be worth.
dte_min:              <UNSET>     # min days to expiration
dte_max:              <UNSET>     # max days to expiration
delta_min:            <UNSET>     # |delta| floor — roughly the market's implied odds the
delta_max:            <UNSET>     # contract finishes in the money; also how much the
                                  # option moves per $1 of underlying
max_spread_pct:       <UNSET>     # (ask - bid) / mid, as %. This is your round-trip cost
                                  # before the trade does anything. A 0.45/0.55 quote is
                                  # 20% wide.
min_open_interest:    <UNSET>     # contracts outstanding; thin strikes are hard to exit
iv_rank_min:          <UNSET>     # optional: IV percentile floor
iv_rank_max:          <UNSET>     # optional: IV percentile ceiling
```

## Event handling

```yaml
earnings_policy:      "<UNSET>"   # avoid | allow | require
                                  # earnings inside the holding window is the single
                                  # largest source of overnight gap risk, in both
                                  # directions
earnings_buffer_days: <UNSET>     # days around earnings to apply the policy
```

## Exits — required before any entry is proposed

An entry without a predefined exit is an open-ended bet. The workflow will not surface a
candidate unless all four of these are set.

```yaml
profit_target_pct:    <UNSET>     # close at +X% on premium paid
stop_loss_pct:        <UNSET>     # close at -X% on premium paid
time_stop_dte:        <UNSET>     # close when DTE falls to this, regardless of P&L —
                                  # theta decay accelerates into expiry
max_hold_days:        <UNSET>     # hard calendar cap
```

## Cadence

```yaml
scan_interval:        "<UNSET>"   # how often the scan runs, e.g. "30m", "daily 09:45 ET"
scan_sessions:        "<UNSET>"   # regular_hours only, or include extended
pdt_budget:           <UNSET>     # day trades to reserve per rolling 5 business days.
                                  # Under $25k equity the regulatory cap is 3; going over
                                  # flags the account and restricts it.
```
