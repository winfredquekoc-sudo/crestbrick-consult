# WA intake: send caps and anti-spam guarantees

One sender only: everything below runs inside `wa_intake_runner.py`'s single 60s tick
(tenant flow, category 2 auto replies, the owner loop) and every real send goes through
the same `_send()` (bridge POST) gated by the same `_guard_reserve()` (shared cross-sender
cooldown, `scripts/wa_send_guard.py`). No second sender, no new launchd job.

## Per tenant

| Send type | Cap | Notes |
|---|---|---|
| Everything not listed below | `DAILY_SEND_CAP` = 2 touches / SGT day | Held reply force-notifies Winfred (coalesced, see below) |
| `CONFIRM_VIEWING`, `OFFER_VIEWING`, `ASK_ONE` | Unconditionally exempt | Direct reply to the prospect's own message in the booking flow |
| `VIEWING_TIME_PROPOSED`, `ASK_TENANT_TIME` | 1 extra touch / SGT day (own date stamp) | Held reply is silent, same as before this bound existed |
| `REDIRECT` | 1 extra touch / SGT day (own date stamp) | One-shot closure; bounded, not fully exempt |
| `LEASE_NOTE` | 1 extra touch / SGT day (own date stamp) | One-shot note; bounded, not fully exempt |
| Category 2 auto reply (availability, pax, facts, follow up chaser, ...) | 1 fire / request type / chat | Counts toward `DAILY_SEND_CAP` like any other touch |

Decision lives in `wa_intake_send.daily_cap_should_skip()`.

## Per landlord (owner loop, `wa_intake_owner.py`)

- 1 consolidated message per landlord per day (`DAILY_CAP_PER_LANDLORD`), up to 3 questions
  batched into it (`MAX_QUESTIONS_PER_MESSAGE`).
- 09:00–21:00 SGT only (`ASK_WINDOW_START_MIN` / `ASK_WINDOW_END_MIN`).
- Each question: 1 initial ask + 1 chase after 48h unanswered, then expires — never a third
  message (`ANSWER_WINDOW_SEC`, `CHASE_GRACE_SEC`).
- Factual/VIP rules re-checked at send time too, not just enqueue time
  (`_question_is_safe`): never commission/price/rent/deposit/dispute/legal/protected
  attribute, never a tenant's name/phone/nationality/budget.

## Global circuit breaker

Last line of defense across every prospect at once, on top of both caps above: more than
`CIRCUIT_MAX_SENDS` = 40 prospect-facing sends in a rolling `CIRCUIT_WINDOW_SEC` = 60 minute
window stops ALL prospect sends until the window clears. Logs `CIRCUIT_OPEN`, notifies
Winfred once per open period (not once per blocked send). Never counts an owner/landlord
send, a `DRY_RUN` preview, or a guard-skip. `wa_intake_send.circuit_breaker_gate()`.

## Telegram notify coalescing

Routine FLAG_HUMAN style pings (redirect/house_gate declines, held-by-cap replies) for the
SAME chat are coalesced to one immediate ping per 30 minute window, with any more rolled
into one digest when the window ends. A dispute, protected attribute, or legal threat
marker — or a time-critical ping (`VIEWING_TIME_PROPOSED`, `CONFIRM_VIEWING`,
`COPILOT_VERDICT`, ...) — always goes out immediately, uncoalesced.
`wa_intake_notify.notify_winfred_coalesced()`.

## Tests

`tests/wa-pipeline/test_time_reply_cap.py`, `test_runner_guards.py`,
`test_attack_hardening_sep9_cycle3.py` (per-tenant cap), `test_owner_loop.py` (per-landlord
cap), `test_circuit_breaker.py` (global breaker), `test_notify_coalescing.py` (Telegram
coalescing).
