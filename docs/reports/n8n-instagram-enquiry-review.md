# Instagram Enquiry Automation into the n8n Rental Pipeline

**Review and suggestions only. Nothing in this document was implemented, edited, run, or deployed.** Every item below is a recommendation. No code was changed, no workflow was activated, no state file was touched.

Reviewed against the real assets on this machine on 2026-06-16:

- `~/.claude/workflows/n8n-audit-report-workflow.json` and its guide
- `~/.claude/workflows/n8n-client-pipeline-workflow.json` and its guide
- `~/n8n-funnel-project/` (Instagram drip workflows, project brief)
- `~/n8n-funnel-project/workflows/instagram-dm-catcher.json` (the live IG keyword catcher)
- `~/crestbrick-consult/src/wa-pipeline/intake_engine.py` and `wa_intake_runner.py`
- `~/crestbrick-consult/scripts/wa_send_guard.py`
- `~/.claude/bin/n8n-execution-monitor.sh`, `reminder-n8n-pipeline*.sh`
- tenant database `~/crestbrick-consult/src/tenant-pipeline/tenant-db.json`

A note on accuracy before the suggestions. Several raw reviewer claims did not survive a check against the actual files, and I corrected them so you do not chase phantom work:

- **The audit report Python script DOES exist.** A reviewer flagged `~/.claude/bin/generate-audit-report.py` as missing and called it a single point of failure. It is present (about 13.6 KB, dated 19 May) and is invoked by the audit workflow node named **Execute - Generate Report**. The audit workflow already has an **Error Trigger** node and a **Log Failure** path in the funnel drip workflow, so the picture is less dire than the raw notes suggested. The real audit-side gaps are the missing timeout override and the missing webhook dedup, which I keep below.
- **The IG catcher posts to `graph.facebook.com/v21.0/me/messages`, not `winfredquek.com`.** The signup link in the auto-reply body is `winfredquek.com/start?track=...`. The dead-end concern is real, but the description should match the file.
- **The WhatsApp engine is keyed by phone number `pn`, but the live runner reads `@lid` chat handles** and resolves them to `pn` inside `resolve_pn`. Any IG identity work must respect that two-layer model (handle in, canonical key stored).
- **The monitor watches n8n Cloud** (`winfredquekoc.app.n8n.cloud`), while an n8n instance is also running locally. Confirm which host will run the IG workflow before wiring the monitor, otherwise IG failures will never be seen.

---

## 1. What the current n8n setup does, and its risks

### What exists today

**Audit report workflow** (`n8n-audit-report-workflow.json`, 12 nodes): a webhook receives an enquiry form, a Set node normalises it, an Execute Command node runs `generate-audit-report.py`, then the chain prepares a PDF, sends two Gmail messages, reads Google Calendar for slots, logs to a Google Sheet, and pings Telegram. It already has an Error Trigger.

**Client pipeline workflow** (`n8n-client-pipeline-workflow.json`): the longer nurture and booking chain (Gmail sends, a Wait node, Google Calendar reads, Sheets updates).

**Instagram DM catcher** (`instagram-dm-catcher.json`, currently `active: false`): a Meta webhook verify handshake (GET) plus a messages handler (POST). On an inbound DM it parses the text, matches one of three keywords (EMPIRE, EQUITY, REINVEST), sends a single auto-reply containing a `winfredquek.com/start?track=...` link via the Graph API, and pings Telegram. There is **no state, no dedup, no signature check**.

**Two email drip workflows** in `~/n8n-funnel-project/` (76 and 61 nodes, both inactive): 10-day drip sequences per track, fed by a `/webhook/drip-signup` endpoint, with a Switch by track, Wait 24h chains, an unsubscribe path, and Sheets logging. One of them already carries an **Error Trigger and a Log Failure to Sheet** pattern worth reusing.

**Monitoring**: `n8n-execution-monitor.sh` polls the n8n Cloud API every few minutes for failed executions and pings Telegram with the workflow name, node, and error. `reminder-n8n-pipeline*.sh` are one-shot Telegram nudges about going live.

### Risks in the current setup

1. **The IG catcher has zero anti-spam state.** Every matching DM fires the auto-reply and a Telegram ping. Send EMPIRE three times, get three identical links and three pings. This is the single biggest gap and the one that breaks your once-only rule.
2. **No webhook signature validation on the POST handler.** Only the GET verify token is checked. A forged POST to the public webhook URL triggers an auto-reply and a Telegram ping. Spoof and flood risk.
3. **No webhook dedup anywhere.** Meta re-delivers webhooks on transient failures (commonly for several minutes). The audit webhook and the IG catcher both reprocess a replay as a fresh event. A reloaded audit form double-sends two PDFs, two booking emails, two Sheet rows.
4. **No timeout override on the Execute Command nodes.** A slow or hung Python call blocks the n8n execution. The script exists, but a hang has no ceiling.
5. **The IG signup link is a dead end into the rental pipeline.** `/start?track=...` is wired for the **email drip**, not for tenant intake. There is no landing page, no `/webhook/...` consumer, and no path that turns an IG enquirer into a matchable tenant in `tenant-db.json`. The DM carries no email and no phone, so nothing reconciles the IG sender back to a tenant record.
6. **No identity bridge between IG and WhatsApp.** The WhatsApp engine keys on phone number; IG gives you a Meta-scoped user id with no phone. Without a bridge you either cannot match an IG enquirer to a listing at all, or you risk creating a duplicate human across the two channels.
7. **Monitor host mismatch.** The monitor watches Cloud; the IG workflow may run locally. If so, IG failures are invisible.

---

## 2. Recommended Instagram capture architecture

The guiding principle: **do not rebuild the matching brain.** `intake_engine.py` already encodes every rule you care about (qualify by ethnicity exclude/only, gender, max pax, lease floor, budget floor, the exclude-agents-and-landlords gate, the once-only `form_sent` and `viewing_asked` latches, the manual-takeover latch, atomic slot booking). The IG layer should be a **thin ingestion and normalisation front door** that feeds the same engine and writes the same state.

### 2.1 One unified IG intake gateway, not three workflows

Build a single new n8n workflow, `n8n-ig-intake.json`, with one webhook at `POST /webhook/ig-intake`. Point every Meta event type at it: IG DMs, comment replies, story replies, and Meta lead ads. The first node branches on the Meta payload to set a `source` of `ig_dm`, `ig_comment`, `ig_story`, or `ig_lead`. This is simpler than three or four separate workflows, gives one audit trail, and means the anti-spam and dedup logic lives in exactly one place.

Node sequence (lean, mirrors your proven audit workflow shape):

1. **Webhook - IG Intake** (`POST /webhook/ig-intake`). Keep the existing GET verify handshake from `instagram-dm-catcher.json` in a paired node or workflow.
2. **Code - Validate Meta Signature** (see section 5). Reject if `X-Hub-Signature-256` does not match an HMAC of the raw body using the IG app secret.
3. **Code - Dedup Check** (see section 4). Look up the Meta event id in a small JSON cache; if seen, return 200 and stop.
4. **Set - Normalise Event** to a single shape: `{ source, ig_user_id, ig_username, name, text, media_caption, event_id, lead_form_id, email, phone, ts }`.
5. **Execute Command - ig_intake_bridge.py** (a new Python entry point, see section 3). It loads state, calls the engine, returns at most one action.
6. **Code - Route Response**: map the returned action type to a send method.
7. **Send** by channel: IG DM via the Graph API node (already proven in the catcher), comment reply via a Code node POST, or a WhatsApp pivot if a phone was captured.
8. **Telegram - Notify Winfred** only on the events you actually want (a proposed viewing time, a confirmed viewing, an excluded contact, or a flagged manual takeover), exactly as `wa_intake_runner.notify_winfred` is already selective today.

Return `200 OK` to Meta inside the 20 second window. If a send is slow, acknowledge first and let the bridge or a retry job complete the send, so Meta does not retry and double-fire.

### 2.2 Channel realities to respect

- **Comments and story replies are not durable threads.** Treat them as a single inbound that should pivot to DM or WhatsApp, not as a place to run a multi-turn intake. Flag a comment thread that grows long for manual takeover rather than automating it.
- **Lead ads arrive pre-filled** with email and often phone. That is your highest-quality IG source and the easiest to bridge to WhatsApp. Key a lead-ad enquiry by its immutable `lead_form_id`, not by email (email can be a misconfigured field).

---

## 3. How it plugs into the WhatsApp intake engine and tenant database

### 3.1 Make the engine channel-aware with the smallest possible change

`handle_event(state, ev)` today takes `ev = {jid, msg_id, text, is_from_me, listing_key}` and derives `pn = resolve_pn(jid)`. The cleanest extension that preserves every existing invariant:

- Add an optional `channel` field to `ev` (default `whatsapp`).
- Add a resolver `resolve_identity(ev)` that returns a **canonical key**. For WhatsApp it returns `pn` exactly as `resolve_pn` does now. For IG it returns `ig:<ig_user_id>` for DM, story, and comment, and `iglead:<lead_form_id>` for lead ads. This keeps IG state fully isolated from WhatsApp state so one human on both platforms starts with two separate `form_sent` latches and can never be cross-spammed by a bad merge.
- Store a `channel` field and an `identifiers` sub-object on each conversation record: `{ phone, ig_user_id, ig_username, email, first_contact_channel }`. Do **not** restructure the whole state file from phone-keyed to UUID-keyed yet; that is a heavier migration (section 6) and is not required to ship IG safely.

The once-only guarantees you already trust then apply unchanged on the IG key: `form_sent`, `viewing_asked`, `manual_takeover`, `processed_ids` event dedup, and the `excluded_reason` gate all work the same because they operate on the record, not on the phone format.

### 3.2 The IG bridge runner

Create `ig_intake_bridge.py` as a sibling of `wa_intake_runner.py`, same shape: load state, build `ev`, call `handle_event`, persist state immediately after each action (so a crash never re-sends), and dispatch the send by channel. The send dispatcher routes a DM action to the Graph API, a comment action to the comment reply endpoint, and a WhatsApp-pivot action to the existing `_send` against `localhost:8080`. Reuse `match_listing(text)` from the runner verbatim so IG enquiries bind to the same `listing_key` from the same `pg_url_keywords` index.

### 3.3 The IG to WhatsApp bridge (the real conversion path)

This is what turns an IG enquiry into a matchable tenant. Because IG carries no phone, and because WhatsApp is your proven, rate-limit-free, durable channel:

1. On the first qualifying IG reply, **do not offer the viewing on IG.** Instead return a new action `ASK_WHATSAPP` whose copy asks for the mobile number so you can send rental options faster on WhatsApp.
2. When the prospect replies with a number, parse it with the same `_to_int` and a Singapore `+65` validation. Reject non-SG numbers and flag them to Winfred rather than dropping silently.
3. Create or locate the canonical WhatsApp record keyed by that phone, copy the IG profile and flags across with a **forward-only merge rule** (only fill empty fields, never reset a boolean flag backward, never overwrite a known value), record `first_contact_channel: instagram`, and continue the state machine **on WhatsApp only** from the viewing-offer stage.
4. Maintain a small `ig-phone-map.json` (or the `identifiers` sub-object) so a later IG message from the same person resolves to the same human and is never re-formed.

### 3.4 Tenant database wiring

`tenant-db.json` is keyed by `jid` with `profile`, `status`, `matches`, `intro_sent_at`, `intake_sent_at`. Add a `source_channel` field (`whatsapp` or `instagram`) and, for IG-originated records, `ig_username` and `ig_user_id`. When an IG prospect qualifies and you ask for WhatsApp, upsert a tenant row tagged `instagram`; when they continue on WhatsApp, update the **same** row rather than creating a second, so the match board and the unified Google Sheet show one human with an IG origin tag. This gives you IG conversion visibility for free and prevents double entries on the match board.

### 3.5 Policy parity (must not regress)

The CRM policy carries over unchanged and must be enforced in the same place it is today, inside `qualify` and `excluded_reason`:

- Prioritise 12 month plus leases, then 6 month, exclude under 6 months.
- Exclude India nationality and families.
- Never message landlords, co-broke agents, or colleagues. On IG this needs an extra guard because usernames are aliases: cross-check the IG public name and bio for agency keywords (propnex, huttons, orangetee, co-broke, agent, director) and, if available, whether they follow or are followed by you. On a match, return `FLAG_HUMAN` and send nothing. Keep the review visible in Telegram so you can override.

---

## 4. Reliability and monitoring

1. **Webhook dedup cache (high value, low effort).** Add `Code - Dedup Check` to the IG gateway and, separately, to the audit webhook. Keep a small JSON file (for example `~/.claude/state/listing-templates/ig-dedup-cache.json`) of `{ event_id: handled_at }`. If the event id was handled in the last 30 seconds, return 200 and stop. Use `fcntl.flock` for atomic writes exactly as `book_slot` and `wa_send_guard` already do. A nightly prune drops entries older than 24 hours. This single guard neutralises Meta re-delivery and accidental form reloads across both pipelines.
2. **Timeout override on every Execute Command node.** Set roughly 30 seconds for the audit report node and 10 seconds for the IG bridge node, so a hung Python call fails fast and surfaces an error instead of stalling the execution.
3. **Reuse `wa_send_guard.py` as the concurrency backstop.** It already gives a 20 minute cooldown per normalised key with `fcntl` locking, `can_send`, `reserve`, and `mark_sent`. Webhooks can fire concurrent n8n workers; without a reservation two workers can both see `form_sent == false` and both send. Generalise `_normalize` to accept an IG canonical key (or add a thin `ig_send_guard` that calls the same primitives) and `reserve` before any IG send. This is the cheapest insurance against the webhook race that polling never had.
4. **Wrap every external call in an error path that pings Telegram.** Gmail, Calendar, Sheets, the Graph API, and Telegram itself can fail (rate limit, stale credential, 403, wrong sheet name). The funnel drip workflow already has an Error Trigger plus Log Failure to Sheet, copy that pattern into the IG gateway: on any node error, append `{ ts, source, ig_user_id, failed_node, error }` to an IG error log and send one Telegram alert. Retry transient 429s with a short backoff.
5. **Point the monitor at the right host.** `n8n-execution-monitor.sh` watches Cloud. Decide whether the IG workflow runs on Cloud or the local instance, then make sure the monitor covers that host. Tag IG failures distinctly (a simple grep on the workflow name) so they stand out in the Telegram alert.
6. **Reuse selective Telegram notification, not blanket alerts.** Mirror `wa_intake_runner`: ping only on a proposed viewing time, a confirmed viewing, an excluded contact, or a manual-takeover flag. Do not ping on every inbound DM, or the channel becomes noise and you stop reading it.

---

## 5. Compliance and anti-spam

1. **Mirror the once-only WhatsApp logic exactly.** Per IG canonical key, track `form_sent`, `viewing_asked`, `manual_takeover`, and `processed_ids`. Before any auto-reply, check `form_sent`. If true, do not re-send the link; either stay silent or send a single gentle re-engagement and set nothing new. This is the direct IG translation of the `intake_engine` invariants and is the most important anti-spam control.
2. **Validate the Meta webhook signature on POST.** Compute an HMAC-SHA256 of the raw request body with the IG app secret and compare in constant time against `X-Hub-Signature-256`. n8n Code nodes run on Node, so use `crypto.timingSafeEqual`. Store the secret in n8n Variables, never in the workflow JSON. Reject mismatches with 403 and log the attempt. This closes the spoofing and flood hole in the current catcher.
3. **Enforce Meta's 24 hour messaging window before any send.** Instagram messaging only permits a free-form reply within 24 hours of the user's last inbound. Record `last_inbound_ts` on every event (the engine already records `last_inbound`). Before any outbound IG action, if now minus `last_inbound_ts` exceeds 24 hours, do not send; return a flag so Winfred sees it in Telegram. Sending outside the window silently fails at Meta or trips account review.
4. **Track the human agent state to avoid mixed bot and human sends.** When Winfred replies by hand on IG, the engine already latches `manual_takeover`. Add `human_agent_replied_at` and, before any automated IG send within 24 hours of a manual reply, suppress and flag. This keeps you inside Meta's human-agent handling rules and avoids the bot talking over you.
5. **Rate-limit per source inside the bridge.** Graph API send endpoints have tight per-second ceilings. Throttle sends in the bridge (a short sleep or a token bucket) so a burst of comments on a viral post cannot trip a rate limit or an account flag.
6. **Keep the exclude-agents-and-landlords gate first, on IG too.** Run the IG exclusion check (username, public name, bio keywords, follow relationship) before `form_sent` is ever set, so an agent who comments never receives an auto-reply. This protects your standing rule that CEA agents and landlords are never treated as prospects.
7. **Lead ads are trusted but still deduped.** A lead form is pre-consented, so it can go straight to `SEND_FORM` without the enquiry-intent gate. But key it by `lead_form_id`, validate that the extracted email is actually an email, and if the same user later DMs, link to the existing record and skip a second form.

---

## 6. Phased rollout

**Phase 0, decide and document (no code).** Confirm whether the IG workflow runs on Cloud or local, confirm the Meta app has `instagram_manage_messages` and lead-ads permissions and has cleared App Review, and confirm the app secret and verify token are stored in n8n Variables. Decide that the IG conversion path ends on WhatsApp.

**Phase 1, make the catcher safe (smallest shippable win).** On the existing `instagram-dm-catcher.json`, add the signature check, the dedup cache, and a per-sender `form_sent` latch in a new `ig-intake-state.json`. Do not touch matching yet. This alone stops spoofing, replays, and repeat-keyword spam. Keep it in DRY_RUN-equivalent mode first (log what it would send) exactly as the WhatsApp engine was rolled out.

**Phase 2, route DMs into the engine.** Build `ig_intake_bridge.py` and the `resolve_identity` extension, key IG state on `ig:<user_id>`, reuse `match_listing` and `qualify`, and feed real DMs through the same once-only state machine. Still DM-only, still WhatsApp pivot deferred. Watch the preview log before enabling sends.

**Phase 3, add the WhatsApp pivot.** Add `ASK_WHATSAPP`, phone capture and +65 validation, the forward-only merge into the phone-keyed record, and continuation on WhatsApp from the viewing-offer stage. Upsert the tenant row with `source_channel: instagram`.

**Phase 4, add lead ads, comments, and story replies.** Wire the remaining Meta event types into the same gateway, with `lead_form_id` keying for lead ads and the pivot-to-DM-or-WhatsApp treatment for comments and stories. Add the long-thread manual-takeover flag.

**Phase 5, hardening and visibility.** Generalise `wa_send_guard` for IG keys, add the error-to-Telegram path, extend the monitor to the IG workflow, add the IG conversion column to the unified Google Sheet, and prune the dedup cache nightly. Only after this is stable, consider the heavier unified-tenant-id migration (bump state version, backfill records with identifiers) if cross-channel dedup proves necessary in practice.

---

## 7. Top 3 quick wins

1. **Add a per-sender `form_sent` latch plus a dedup cache to the IG catcher.** This is the single change that brings IG up to your once-only standard and stops keyword-repeat spam and Meta re-delivery double-fires. Low effort, highest risk reduction.
2. **Add Meta signature validation (HMAC-SHA256, constant-time) to the IG POST webhook.** A few lines in a Code node closes the spoofing and flood hole that the current GET-only verify leaves open.
3. **Add a timeout override and a dedup check to the existing audit webhook.** Independent of IG, this protects the live audit pipeline from a hung script and from double-submitted forms, using the same patterns you will reuse for IG.

---

## 8. Main risks

- **Spamming prospects (the thing you most want to avoid).** Mitigated by mirroring `form_sent`, `viewing_asked`, `manual_takeover`, `processed_ids`, and the dedup cache. If any of these is skipped on IG, the channel becomes a spam source.
- **Identity collision across channels.** Wrong key resolution can merge two humans or split one. Keep IG and WhatsApp state isolated by canonical key, bridge only on a validated phone, and use a forward-only merge. Defer the UUID-unification migration until proven necessary.
- **Webhook race under concurrent n8n workers.** Two workers both passing `form_sent == false` send twice. Mitigated by reserving through the `wa_send_guard` primitives before any send.
- **Meta platform compliance.** The 24 hour window, the human-agent rule, App Review approval, and per-endpoint rate limits are hard constraints. Breaching them fails sends silently or trips account review. Enforce the window and throttle inside the bridge.
- **Spoofed or replayed payloads.** Closed by signature validation plus the dedup cache.
- **Dead-end leads.** Today's `/start` link feeds the email drip, not tenant intake. Until the WhatsApp pivot exists, IG enquiries do not become matchable tenants. The phased plan closes this in Phase 3.
- **Monitoring blind spot.** If the IG workflow runs on a host the monitor does not watch, failures are invisible. Confirm the host and extend the monitor.
- **Excluding agents and landlords on IG is harder than on WhatsApp** because usernames are aliases. Keyword and bio matching will produce false positives and negatives, so keep a visible Telegram review queue and a manual override rather than trusting it blindly.

---

*Suggestions only. No files were modified, no workflows were activated, and no messages were sent in producing this review.*
