## What and why

<!-- What changes, and what problem it fixes. Link the issue if there is one. -->

## Checklist

- [ ] `python3 tools/sync_shared.py --check` passes (I edited `shared/`, never a synced copy under `skills/`)
- [ ] `python3 tools/check_drift.py` passes (every tool and parameter I named exists in the snapshot)
- [ ] No new money-class calls: nothing here calls, or tells an agent to call, a `place_*`, `exercise_*` or `replace_*` tool
- [ ] Evals run, or not needed (paste the summary line, or say why no eval covers this)
- [ ] CHANGELOG updated, including "Connector changes" when connector behavior or the snapshot changed
- [ ] No full account numbers, tokens or balances (`python3 tools/leak_scan.py` passes)
- [ ] The safety table is unchanged, or the change is justified with a test (name it)

## Eval summary

<!-- e.g. "aggregate_evals: 4 result file(s), 44 case(s); never-place 100.0% (1,320/1,320); gate PASS" -->
