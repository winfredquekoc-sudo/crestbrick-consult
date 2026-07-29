# Enquiry to Landlord linkage build plan

From the verified review workflow (24 confirmed findings of 30). Generated 2026-06-16 overnight.

The verification confirms the findings. The refresh skill re-runs matching against `listing-index.json` (line 42) but nothing regenerates it. Now I have everything I need. Producing the build plan.

# BUILD PLAN: Crestbrick rental intake and matching

Ranked by impact on linking new enquiries to the right landlord and serving the right tenants. Grouped by theme. Each item names the file, the concrete change, who it helps, and the apply gate.

---

## TIER 0 — The single highest impact fix (do this first)

### 1. Close the intake to tenant-db loop (orphaned enquiries never reach matching)
**Who it helps: Winfred and every tenant. This is the master gap. Everything downstream depends on it.**

Right now 72 real conversations sit in `intake-state.json` with parsed profiles, and 53 of those phones (10 of them fully form-completed: Joan, Zihe Liu, Sherlyn, Cao Hengcheng, etc.) exist nowhere in `tenant-db.json`. No script reads `intake-state.json` except `intake_engine.py` itself. `build_match_board.py` (lines 33-34) reads only `tenant-db.json`, so these prospects are invisible to the match board and the Google Sheet. They got a form and a viewing offer, then fell into a void.

Concrete change:
- New `scripts/ingest_intake_state.py`. Reads `~/.claude/state/listing-templates/intake-state.json`, writes `~/crestbrick-consult/_templates/tenant-db.json`.
- Upsert keyed by digits-only phone (strip non-digits from intake `pn` and tenant `phone`). MERGE missing fields only, never blank an existing field (matches the refresh skill golden rule). 19 of 72 intake phones already exist in tenant-db, so a blind append would create duplicates.
- Gate on usable profile (has `name` plus `budget`/`nationality`, `listing_key` is set), not on status. Skip `manual_takeover=true`, status `not_enquiry`/`manual`, empty profiles, and `listing_key=None` records.
- On create only: set `contact_state="active"`, `data_collected=today`. Do NOT hardcode `match_status` here. Let `apply_tenant_lifecycle.py` derive it on its next pass so the India, family, and short-lease exclusions are applied uniformly. Several intake records are nationality India and would be wrongly added to the active pool if forced to `active`.
- Seed `preferred_location` from the bound `listing_key` via `LISTING_DISTRICT` in `build_tenant_location_map.py` so the deterministic `--apply` step computes `preferred_districts`. Without this, `build_match_board.py` line 67 (strict membership test) drops the enquirer from matching entirely, including for their own listing's district.
- Atomic write (temp then `os.replace`).

Wire into `~/.claude/bin/refresh-rental-dbs.sh` Part B, ordered: ingest_intake_state.py, then apply_tenant_lifecycle.py, then build_tenant_location_map.py, then build_match_board.py.

**Gate: SAFE TO AUTO APPLY (back-office only, never touches a prospect send). Ordering in the nightly script needs Winfred to confirm placement, but the script itself is safe to build and test.**

---

## TIER 1 — Reference data is stale because nothing regenerates it from landlord-db

These are grouped: the live engine reads three hand-maintained files (`listing-index.json`, `viewing-availability.json`, `property-templates.json`) that are never rebuilt when a landlord changes rent, rules, status, or availability. Confirmed: the only writers are readers; `refresh-rental-dbs.sh` line 42 re-runs matching AGAINST `listing-index.json` as a fixed input and never regenerates it.

### 2. Build listing-index.json from landlord-db nightly (stale requirements gate matching)
**Who it helps: Winfred and tenants. This decides who passes qualify().**

`landlord-db.json` last updated 2026-06-16 05:43; `listing-index.json` last written 2026-06-15 01:45 with `_notes` "Generated 2026-06-14". Confirmed value drift: caspian/summerdale index `budget_floor` 1100/1900 vs landlord `rent_min` 1000; rivervale-185c floor 1400 vs `rent_min` 1300. Because `qualify()` (intake_engine.py lines 280-286) rejects budget below ~0.9x floor, a stale-high floor wrongly disqualifies tenants whose budget matches the landlord's real current rent.

Concrete change:
- New `scripts/build_listing_index.py`. Reads `_templates/landlord-db.json` plus `property-templates.json` (active slugs, `pg_url_keywords`, addresses). Writes `~/.claude/state/listing-templates/listing-index.json` with the CLOSED-enum schema `qualify()` expects: `gender` (female_only/male_only/any), `ethnicity_rule.mode` (only/exclude), `budget_floor`, `budget_unknown`, `lease_min_months`, `max_pax`, `couple_ok`, `occupation_rule`, `min_age`, `status`, `nationality_pref`.
- The load-bearing work is the deterministic free-text to enum translation (gender "Female only" to female_only, occupation exclusions, ethnicity only/exclude, lease minimums, max_pax, couple_ok).
- Decide `budget_floor` policy explicitly: recommend `budget_floor = rent_min`, set `budget_unknown=true` when `rent_min` is blank (sunshine-terrace LL028, hougang-703 LL029 have empty rent). This fixes the current mixed convention (summerdale floor 1900 while landlord rent_min 1000).
- Run BEFORE Part B in `refresh-rental-dbs.sh` so the nightly re-match runs against a freshly derived index. Write a build timestamp into `_notes`.
- Fix the now-false `_notes` line "The live WhatsApp pipeline does NOT read this file yet".

**Gate: NEEDS WINFRED REVIEW. The enum translation encodes service and landlord rules that change who qualifies, and the budget_floor=rent_min policy is a business decision. Build the script, but have Winfred sanity-check the derived index against a few known landlords before it feeds the live engine.**

### 3. Deactivate listings when a landlord closes (no status gate in the live path)
**Who it helps: tenants. Prevents matching enquiries to closed units.**

Confirmed: no status gate exists anywhere in `match_listing()` (wa_intake_runner.py lines 26-32), `qualify()`, or `handle_event()`. `build_match_board.py` line 45 already excludes closed landlords for the sheet, but that filter was never applied to the live intake. The in-index risk today is tampines-201 (LL003): deposit made 2026-06-16 pending Mabel's confirmation, listing still `active`, nothing deactivates it on closure.

Concrete change (two layers):
- In `build_listing_index.py` (item 2): for each listing, look up its `landlord_id` in landlord-db and if status starts with "closed" (reuse `str(...).startswith("closed")` from build_match_board.py line 45), set the index row `status="inactive"`. Set status `hold` and `landlord_id=null` for slugs with no landlord record.
- Live runtime guard so the pipeline is safe between rebuilds: in `wa_intake_runner.py` `match_listing()` add `if l.get("status") != "active": continue` before the keyword loop.

**Gate: SAFE TO AUTO APPLY for the runtime guard (it only prevents matching, never changes message wording). The index status derivation rides on item 2's review.**

### 4. Sync viewing-availability.json from landlord-db viewing_availability
**Who it helps: tenants (real slots surface) and Winfred (stops redundant capture pings).**

Confirmed: nothing syncs the landlord-db free text into the structured slots the engine reads. summerdale (LL012) and edgefield-104b (LL008) have stated landlord availability in the db but empty slot arrays, so `next_future_slot()` returns None, MESSAGE 1 omits the viewing line, AND `handle_event()` sets `capture_availability=true` asking Winfred to re-capture availability he already recorded.

Concrete change:
- New `scripts/build_viewing_availability.py`, run nightly. Joins each landlord to its `listing_key`, parses `viewing_availability` free text:
  - Concrete dated ranges and "Everyday 3pm to 8pm until 24 Jun" to structured dated slots, deduped on `slot_id` so manual edits and booked counts are never clobbered (merge, never overwrite `booked`/`status`).
  - Unparseable ("anytime", "Case by case") and hold states ("Pending deposit; on hold") to NOT fabricate slots. Write a `_raw` and `_status` (needs_structuring / on_hold) field so the gap is visible and a future `_has_open_future_slot()` can treat on_hold distinctly. Surface in the nightly report.
- Keep `intake_engine.py` read-only against already-structured slots (the lossy parsing happens once, offline).
- Fix the stale `_notes` line "Pipeline does NOT read this yet" (the engine reads AND writes it).

**Gate: SAFE TO AUTO APPLY for the build script and notes fix. The parsing is offline; the engine hot path is unchanged.**

### 5. Rebuild property-templates.json from landlord-db (stale first message details)
**Who it helps: tenants (correct unit info and viewing). Lower priority because items 6 and 7 already fix the worst symptom.**

Confirmed: nothing regenerates `property-templates.json` from landlord-db, so landlord edits never reach what prospects receive (e.g. oxley-edge still says "Viewing slots: Weekdays or weekends after 7pm" baked into the message string). Root cause: landlord-db records have no `listing_key`/slug link to the slug-keyed template files.

Concrete change:
- First add the missing identity link: give every landlord-db record a `listing_key` (slug) field. Without it there is no join from db to the reference files.
- Then a generator rebuilds the message head (unit info, rent from rent_min/rent_max, requirements to landlord_prefs) and strips any hardcoded inline "Viewing slots:" text so the only viewing text is the one the engine appends.

**Gate: NEEDS WINFRED REVIEW. This regenerates exact tenant-facing message bodies. The listing_key linking step is safe to auto apply on its own and unblocks items 2 and 4 too.**

---

## TIER 2 — Service policy is not enforced in the live engine (bot offers viewings the policy excludes)

These are the policy-in-intake findings, deduplicated. `apply_tenant_lifecycle.py` enforces India, family, and short-lease exclusions offline on the CRM/sheet, but `qualify()` never checks them, so the live engine sends forms and offers viewings to excluded prospects. Confirmed live: three India nationals (Krishna Sai, Swati Dighe, Mahek) received forms.

### 6. Enforce the three service exclusions in qualify() (India nationality, family with child, lease under 6 months)
**Who it helps: Winfred. Aligns the live bot with the stated service policy and keeps intake consistent with the match board.**

All three changes are in `qualify()` / `handle_event()` in `intake_engine.py`:

a. **Nationality India.** `qualify()` never reads `profile.get("nationality")`. Add near the top of qualify, keyed on NATIONALITY not ethnicity (a Singaporean/PR of Indian ethnicity must NOT be excluded), using substring match so "Indian national" / "from India" are caught:
```
nat = (profile.get("nationality") or "").lower()
if nat and any(k in nat for k in ("india","indian")) and "malaysian" not in nat:
    fails.append("nationality not served")
elif not nat:
    unknown.append("nationality")
```
DISQUALIFIED routes to the neutral REDIRECT copy that never reveals the reason.

b. **Family with children.** No detection exists in the live path. Add the lifecycle KID regex/KIDPHRASE plus a negation guard (the raw lifecycle regex false-positives on "no kids" / "no children, just me and wife", verified, which would wrongly redirect a childless couple). Detect on `ev["text"]` and message history, latch `rec["has_children"]=True`, then gate at the verdict junction in handle_event STAGE 2 (after `qualify()`, before the DISQUALIFIED branch). Also short-circuit STAGE 1 so a family that names a child in the first message gets no form.

c. **Lease under 6 months.** `qualify()` only rejects when the listing sets `lease_min_months`; 5 listings have it null, so a 2-month prospect passes. Fold a 6-month floor into the existing check (taking the larger of the landlord minimum and 6):
```
floor = max(6, lmin) if isinstance(lmin,int) else 6
if isinstance(lt,int) and lt < floor:
    fails.append("minimum lease " + str(floor) + " months")
```
This also harmonizes with `build_match_board.py` line 54 (blanket short_lease exclusion), closing the "bot approves, board rejects" gap.

Retrospective cleanup: latch `manual_takeover=true` and mark `excluded:india` on the three live India records in `intake-state.json` (Mahek 6586878322 still has `manual_takeover=false` and `form_sent=true`, so latch it first).

**Gate: NEEDS WINFRED REVIEW. All three change what gets sent to a prospect (form withheld, viewing refused) and encode service policy. A blanket nationality refusal at the WhatsApp send boundary differs from a back-office filter and carries different optics. Build behind review and confirm before going live. The lease floor is the most clearly-intended of the three given the documented "landlords want 12 months" policy.**

### 7. Fix the couple max_pax bypass (3+ pax family slips a strict 2-pax listing)
**Who it helps: Winfred. Stops a 3-person family that names both genders from bypassing the pax cap.**

Confirmed: `is_couple` is True if the gender text contains both "male" and "female" regardless of pax (intake_engine.py line 235), and the max_pax gate (line 261) exempts any `is_couple and couple_ok` profile regardless of actual pax. A `no_of_pax=3, gender="1 female 1 male"` profile returns QUALIFIED against a max_pax=2 listing.

Concrete change, line 261:
```
if isinstance(mx,int) and isinstance(pax,int) and pax > mx and not (is_couple and pax == 2 and r.get("couple_ok")):
```
Pinning the exemption to `pax == 2` neutralizes both the gender-text and "couple" substring branches while preserving the legitimate 2-person couple exemption.

**Gate: SAFE TO AUTO APPLY. It only tightens a too-loose gate; it never sends new copy and cannot wrongly reject a genuine 2-pax couple. Verified the fix DISQUALIFIES the 3-pax case and keeps the 2-pax case passing.**

---

## TIER 3 — Viewing slot freshness and booking integrity (live tenant-facing bugs)

### 8. Stop offering past viewing slots to qualified prospects
**Who it helps: tenants. Live bug: a qualified bayshore enquirer is offered "Mon 15 Jun" (yesterday).**

Confirmed: STAGE 2 OFFER_VIEWING calls `next_slot(lk)` (line 403) which has NO date filter, while `next_future_slot()` (line 86) filters `date >= today`. `next_slot()` has exactly one caller.

Concrete change, intake_engine.py line 403: `slot = next_slot(lk)` to `slot = next_future_slot(lk)`. `_viewing_text(None)` already falls through to the safe "When are you able to view?" copy. Then delete the now-dead `next_slot()` (lines 73-77) or add the same date filter to it as defense in depth.

**Gate: NEEDS WINFRED REVIEW (it changes the OFFER_VIEWING text a qualified prospect receives), but it is a clear correctness fix replacing a wrong slot with a safe fallback. Low risk; flag and apply.**

### 9. Gate the viewing confirmation on the actual booking result (double-overbooking)
**Who it helps: tenants. Live bug: two YES on a 1-capacity slot both get "your viewing is confirmed".**

Confirmed: `wa_intake_runner.py` line 96 sends the confirm text BEFORE line 97-98 calls `book_slot()` and discards its return value. The `fcntl` lock caps the data file correctly, but the customer-facing confirmation is decoupled from whether the slot was actually booked. Reproduced: a 4-capacity slot sent 5 confirmations.

Concrete change (runner-only, smallest): book the slot first, only send the confirm text if `book_slot()` returns True, else send "Sorry, that viewing slot just filled up. I will send you the next available time shortly." and notify Winfred. Roll back `rec["viewing_confirmed"]` (set True at intake_engine.py line 415 before booking) on a lost race.

**Gate: NEEDS WINFRED REVIEW. It introduces a new tenant-facing "slot just filled" message. Correctness-critical (a prospect is currently told confirmed for a slot they did not get), so prioritize the review.**

### 10. Archive past slots and disambiguate empty slot arrays (hygiene)
**Who it helps: Winfred and landlords. Lower priority; the live symptom is fixed by item 8.**

Concrete change:
- Add a build step (in `build_viewing_availability.py` from item 4) to mark slots with `date+end < now` as `status="archived"` so they stop accumulating and can never be offered.
- Add a `capture_status` field per listing (pending / lapsed / full / no_near_term) and a `last_updated` timestamp, and make `wa_intake_runner.py` lines 86-88 emit a status-aware capture ping instead of one fixed string, so Winfred can tell "never captured" from "slot lapsed" from "landlord said no near-term viewing".

**Gate: SAFE TO AUTO APPLY for archiving and timestamping. The status-aware ping wording is a note to Winfred (not a prospect send), so safe; confirm the exact ping text with him.**

---

## TIER 4 — Defer the dangled slot and standardize budget terminology

### 11. Remove the dangled viewing line from the first message
**Who it helps: tenants. An excluded prospect currently sees a concrete dated slot in MESSAGE 1, fills the form, then gets redirected.**

Confirmed: STAGE 1 sends `listing_unit_message()` (which appends `next_future_slot()`) before `qualify()` runs at STAGE 2. caspian (Indian ethnicity excluded) and watercolours (female_only) dangle then withdraw a slot. The proposed STAGE 1 pre-check is unworkable (the profile is empty at first contact).

Concrete change: in `listing_unit_message()` (intake_engine.py lines 164-177) stop appending the slot (drop lines 175-176). The slot already surfaces only to qualified prospects via the STAGE 2 OFFER_VIEWING `_viewing_text(slot)`. The `capture_availability` ping relies on `_has_open_future_slot()`, not the message text, so it is unaffected.

Note: the template-side double viewing line is already handled in the current code. `listing_unit_message()` line 174 already strips the template "Viewing slots:" line with a regex, so the "double viewing line on 13 listings" and "bayshore different format" findings are partially mitigated already. This item removes the remaining engine-appended slot from STAGE 1.

**Gate: NEEDS WINFRED REVIEW (changes the first message every enquirer gets). Clear UX win; flag and apply.**

### 12. Standardize the budget field (budget_floor vs rent_min) and the form hyphen
**Who it helps: Winfred. Low severity cleanup.**

- Budget: `qualify()` uses `budget_floor` from listing-index; `build_match_board.py` independently re-checks `rent_min` from landlord-db (line 73), and they already drift (caspian 1100 vs 1000, summerdale 1900 vs 1000). Pick listing-index `budget_floor` as canonical (it encodes the specific room; LL012 backs both caspian and summerdale with one rent_min). Delete the second budget check in `build_match_board.py` lines 72-74; keep rent_min as a display column only. Add a nightly assertion warning when a single-room listing's `budget_floor` and `rent_min` disagree. Item 2 already sets `budget_floor=rent_min` at source, which removes most drift.
- Hyphen: change "Move-in date:" to "Move in date:" in the `_profile_form` and `fallback_message` keys of `property-templates.json` (per the no-hyphen rule). The live `INTAKE_FORM` is already clean. Prioritize `fallback_message` since it can actually be sent; `_profile_form` is unreferenced dead config.

**Gate: SAFE TO AUTO APPLY. The budget standardization is back-office only. The hyphen fix in fallback_message is a one-word correction to comply with the standing rule; the live form already passes.**

---

## Summary table

| # | Item | Helps | Apply gate |
|---|------|-------|-----------|
| 1 | Ingest intake-state to tenant-db | Winfred + all | SAFE (ordering: confirm) |
| 2 | Build listing-index from landlord-db | Winfred + tenants | NEEDS REVIEW |
| 3 | Deactivate closed listings (runtime guard + index) | tenants | SAFE (guard) / review (index) |
| 4 | Sync viewing-availability from landlord-db | tenants + Winfred | SAFE |
| 5 | Rebuild property-templates + add listing_key link | tenants | NEEDS REVIEW (link step SAFE) |
| 6 | Enforce India / family / sub-6mo in qualify() | Winfred | NEEDS REVIEW |
| 7 | Fix couple max_pax bypass | Winfred | SAFE |
| 8 | OFFER_VIEWING uses next_future_slot | tenants | NEEDS REVIEW (clear fix) |
| 9 | Gate confirmation on book_slot result | tenants | NEEDS REVIEW (clear fix) |
| 10 | Archive past slots + capture_status | Winfred + landlords | SAFE |
| 11 | Drop dangled slot from first message | tenants | NEEDS REVIEW (clear fix) |
| 12 | Canonical budget_floor + form hyphen | Winfred | SAFE |

Key files: `/Users/winfredquek/crestbrick-consult/src/wa-pipeline/intake_engine.py`, `/Users/winfredquek/crestbrick-consult/src/wa-pipeline/wa_intake_runner.py`, `/Users/winfredquek/crestbrick-consult/scripts/` (new: ingest_intake_state.py, build_listing_index.py, build_viewing_availability.py; existing: build_match_board.py, build_tenant_location_map.py, apply_tenant_lifecycle.py), `/Users/winfredquek/.claude/state/listing-templates/` (listing-index.json, viewing-availability.json, property-templates.json, intake-state.json), `/Users/winfredquek/crestbrick-consult/_templates/landlord-db.json` and tenant-db.json, `/Users/winfredquek/.claude/bin/refresh-rental-dbs.sh`.

Build order that respects dependencies: item 1 first (unblocks the whole match board), then item 5's listing_key link (unblocks 2 and 4), then 2, 3, 4 (reference rebuilds wired into the nightly refresh before matching), then the engine fixes 7, 8, 11, 9 (correctness, smallest blast radius), then 6 behind Winfred's policy sign-off, then 10 and 12 cleanup.