# Classify daily-quota exhaustion vs burst rate-limit for faster failover

| Field | Value |
|---|---|
| Status | Ready |
| Assignee | unassigned |
| Priority | Medium |
| Type | Reliability |
| Created | 2026-09-27 |

## Summary

The failover ladder works but wastes time: when a model's *daily* free
quota is exhausted (gemini-3.8-flash 20 req/day free tier), the retry
loop still burns ~10 minutes of exponential backoff per call before
rotating to sibling models/keys. A 429 "rate limit" (recover in
seconds) and a 429/403 "quota exhausted until midnight Pacific" need
different handling - the latter should rotate immediately.

Observed in both the take2 run and sts2-review-v3: dead-quota 3.8
retries dominated wait time until the chain fell to flash-lite; the
watcher pattern (probe keys, rerun on reset) already proves daily-reset
semantics are knowable.

## Acceptance criteria

- [ ] Parse error bodies / Retry-After headers for quota-reset
      signatures ("quota", "daily", long retry delays).
- [ ] Daily-quota signature -> skip remaining local retries, rotate
      model/key immediately (mark model as quota-dead for a TTL).
- [ ] Burst 429s keep the current backoff path.
- [ ] Optional: persist quota-dead markers in run_state so resumed runs
      don't re-probe dead models.

## Work log

- 2026-09-27: filed; follow-up noted in take2 project log as well.
