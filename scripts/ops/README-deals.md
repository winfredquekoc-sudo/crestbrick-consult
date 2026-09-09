# Deal logger

Log every close with dollars:

```
scripts/ops/log_deal.py add --type rental --role landlord --address "123 Example Rd #05-01" \
    --price 3200 --gross 1600 --source portal --client "Jane Tan"
```

Why: the business audit on 9 Sep 2026 found the `deals` table in
`~/.claude/state/clients.db` (and the commission views built on top of it)
had zero rows ever — every close so far went untracked. `scripts/ops/monday_brief.py`
already reads this table for the "Closes and commission" section of the
Monday brief; `log_deal.py add` is the missing write path that feeds it.

Also: `list [--year YYYY]` to review logged deals, `undo <id>` to delete a
mistaken entry (backs up the db first either way).
