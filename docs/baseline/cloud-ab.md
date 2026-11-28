# Bot and Build measurement of rco pack

Card 1313 (`repo-context-optimizer/rco-cloud-agent-guide`). Does `rco pack`
save work on the mill's own desks? First Grok Bot, then Grok Build, with
the same method. This is the record: the method and the table to fill. It
launches nothing.

Cloud-agent results: none yet.

## what is measured

Per task, with and without `rco pack`:

- tool calls and file reads to the first useful edit
- wall time to that edit

Both are labelled as such. They are not tokens. Tokens join the table only
when the card `run-token-self-report` (1322) lands, or sooner on Build if a
session's usage can be harvested. `tools/phase3_ab.py` refuses a savings
claim unless every log has `TOKENS source=platform` or `source=tokenizer`;
the same rule applies here.

## attribution

- the events log (`.rco/events.jsonl`, card 1314): when each `rco pack`
  ran, at which HEAD, for which paths
- the tw run-to-wake link (card 1321): which wake started which run

A repo without `.rco/` is outside the window. Start with the-mill and
repo-context-optimizer; add md2xarticle and grokbot-wake-relay once each has
a store.

## who runs the arms

Desks doing real cards on those repos (a natural experiment read from events
and tw), plus at least one staged paired task per repo for a clean
comparison. No Cursor cloud-agent two-arm run.

## phases

1. Grok Bot. It exposes no per-run token count, so tool calls, file reads
   and wall time only.
2. Grok Build, same method and table. Build's session usage
   (`grok usage SESSION`, `signals.json`) may allow tokens earlier; whether
   desks persist it is unchecked.

## table

| repo | agent | window | tasks | calls without | calls with | reads without | reads with | status |
|---|---|---|---|---|---|---|---|---|
| the-mill | Grok Bot | not run | 0 | | | | | not measured |
| repo-context-optimizer | Grok Bot | not run | 0 | | | | | not measured |

A row is added only for a named window, with the commands that produced it.
The row that proves or disproves a saving is what the guide's "unmeasured"
waits for; the office table is not a cloud-agent claim.

## not done (by design)

- No agent is launched and no counting run happens in this slice.
- Dependencies for the measure: 1314 (landed), 1321, and for tokens 1322.
