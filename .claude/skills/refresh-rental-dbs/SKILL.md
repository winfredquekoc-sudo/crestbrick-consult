---
name: refresh-rental-dbs
description: >
  Refreshes Winfred Quek's landlord database AND prospective tenant database with
  any NEW or CHANGED rental requirements pulled from WhatsApp, then syncs JSON, CSV
  and Google Sheets. Skips landlord units that are already closed (tenanted).
  Discovers new landlords both by saved contact label and by reading new chat
  content, so landlords Winfred has not relabelled yet are still captured. Runs
  nightly at midnight (launchd) and is also invokable on demand. Also maintains a third
  database, a tenant location map: it groups every prospective tenant by Singapore
  district and, for rejected or cold tenants, suggests active listings near their
  preferred area to re-engage them or tell them a new rental opened nearby. Use this
  skill whenever Winfred says "update my landlord database", "refresh the landlord and
  tenant databases", "pull new landlord requirements", "update prospective tenants",
  "refresh rental databases", "map tenants by district", "who can I re-engage",
  "suggest tenants a nearby rental", or any variation of syncing landlord or tenant
  requirements from WhatsApp into the databases.
---

# Refresh Rental Databases (landlords + prospective tenants)

You are doing a SILENT data update for Winfred Quek (CEA R073319H). You read recent
WhatsApp activity and fold any new or changed rental requirements into two databases.
You NEVER message any landlord or tenant from this skill. This is data only.

## Golden rules (do not break)

- **Never message anyone.** This skill only reads chats and writes data files.
- **Never delete a record and never blank an existing field.** Only ADD new records
  or UPDATE a field when there is genuinely new information for it. If a chat says
  nothing new about a field, leave that field exactly as it was.
- **Skip closed landlord units.** Any landlord whose `status` starts with `closed`
  (e.g. `closed (tenanted)`) is done. Do not refresh it, do not re-message, do not
  reopen it. The only allowed change to a closed record is none.
- **Exclude non-tenants from the tenant DB.** Never add a contact saved with
  "landlord" in the name, a co-broke agent (PropNex, Huttons, ERA, OrangeTee, "I take
  my own com", "co-broke", "my commission"), or a colleague/personal contact
  (Wanni, Shaw, Madeleine, Darren, Amanda) as a prospective tenant.
- **No hyphens, em dashes or en dashes** in any text you write into the data.
- **PDPA:** client phone numbers and PII live only in the JSON, CSV and Google Sheets
  (these are not committed to git). Do not paste full client lists into any external
  surface beyond those. The Telegram digest is COUNTS ONLY: numbers of records
  scanned, updated, added, closed, and slots captured, plus at most a few listing
  slugs. NEVER paste a client name, a phone number, a tenant list, or any landlord PII
  into the Telegram digest or any other surface.

## Clock and timestamps (READ FIRST, do not skip)

- **Never invent, round, or guess a time.** Every timestamp you write MUST come from the
  real system clock by shelling out to bash, not from your own idea of what time it is.
  A value like `00:30:00` or any round midnight time written during a run that did not
  actually start at midnight is a BUG. Do not produce one.
- Get the real values ONCE at the start of the run and reuse them:
  ```bash
  NOW_SGT=$(TZ=Asia/Singapore date "+%Y-%m-%dT%H:%M:%S%z")   # e.g. 2026-06-16T04:58:47+0800
  TODAY_SGT=$(TZ=Asia/Singapore date "+%Y-%m-%d")            # e.g. 2026-06-16
  ```
  If the runner prompt also states "The real current time is ...", that value and a
  fresh `date` call must agree; if they disagree, trust the fresh `date` call.
- Use `NOW_SGT` (the real wall clock, not a rounded value) for: the marker `ran_at`,
  the landlord DB `last_updated`, and the tenant DB `last_updated`. The `+0800` offset
  may be written as `+08:00`; both are fine, just never change the actual time digits.
- Use `TODAY_SGT` (a plain `YYYY-MM-DD` date) for: the marker `date`, each record's
  `last_refreshed`, `viewing_availability_updated`, and any "today" you record.
- `last_contact` is the DATE of the latest genuine INBOUND message for that contact,
  taken from `messages.db`, NOT today's date and NOT a time. Write it as `YYYY-MM-DD`.
  See the timezone note under Files before reading `messages.db`.
- Do not copy an old timestamp forward as if it were now. If a field was already correct
  and nothing changed, leave it; do not rewrite it with a fabricated newer value.

## Files

- Landlord JSON: `~/crestbrick-consult/_templates/landlord-db.json`
- Landlord CSV: `~/crestbrick-consult/docs/landlord-database.csv`
- Tenant JSON: `~/crestbrick-consult/_templates/tenant-db.json`
- Tenant CSV: `~/crestbrick-consult/docs/tenant-database.csv`
- Listing match index (closed enums): `~/.claude/state/listing-templates/listing-index.json`
- WhatsApp messages DB: `~/whatsapp-mcp/whatsapp-bridge/store/messages.db`
- WhatsApp contacts + lid map DB: `~/whatsapp-mcp/whatsapp-bridge/store/whatsapp.db`
- Run marker (idempotency): `~/.claude/state/refresh-rental-dbs-last.json`

Both Google Sheets: use these PINNED ids with the google-workspace MCP (account name
`winfred`) — do NOT search Drive by title (title search silently skipped the landlord
push for 5+ consecutive nightly runs in late Jun 2026). Tenant sheet id is
`1r7M3eGh84s4QrgAX1IS3nVLiTtauqpjvzlDXngNluMg` ("Crestbrick Prospective Tenants ...").
Landlord sheet id is `1KvY00UPrfKYIE-LpDRq7LxfdsVD5xZMBX4F6BSa03i4` (also recorded as
`google_sheet_id` inside landlord-db.json — trust that field if these ever diverge).
If a sheet write fails, say so in the digest with the error; never mark it synced.

**Timezone of `messages.db`:** timestamps are stored ALREADY in Asia/Singapore with an
explicit offset, formatted like `2026-06-16 04:52:33+08:00` (a space separates the date
and time, not a `T`). They are NOT UTC. Do not subtract or add 8 hours, and do not
convert anything. When you build the SQL `:since` bound, pass a plain `YYYY-MM-DD` SGT
date string (e.g. the record's `last_contact` minus 1 day). String comparison on the
date prefix is correct, so `timestamp >= '2026-06-14'` selects everything from 14 Jun
SGT onward. The date you derive for `last_contact` is the SGT date prefix of the latest
inbound row, so there is no skew between `last_contact` (SGT date) and these rows.

## Step 0: Idempotency

Read the run marker `~/.claude/state/refresh-rental-dbs-last.json`. If it has
`"ok": true` and `"date"` equal to today (Asia/Singapore, the `TODAY_SGT` value from
the Clock section) AND this is the nightly job (the runner prompt says "scheduled
nightly midnight run"), exit early with `ALREADY_RAN_TODAY`. A manual or user requested
invocation ALWAYS runs, ignore the marker. (The marker schema is defined in Step Z and
the nightly runner greps it for `"date"` and `"ok": true`, so write it exactly as
specified.)

A marker with `"ok": false`, or with no marker at all, means the previous attempt did
NOT complete, so this run proceeds (it does not exit early). Because every write in this
skill is additive and idempotent (never blank a field, never duplicate a slot, never
reopen a closed unit), re-running after a partial failure is safe and must not double
count: only count records you actually change on THIS run.

## Part A: Landlord database

1. Read `landlord-db.json`. Note `total` and the highest existing `id` (LLxxx).
2. Build the work list: every landlord whose `status` does NOT start with `closed`.
   (Include `active`, `dormant`, `sale-active`. Exclude anything `closed ...`.)
3. For each landlord in the work list, pull their recent messages. Prefer a direct
   SQLite read keyed by their `chat_jid` (fast, deterministic):
   ```sql
   SELECT timestamp, is_from_me, content
   FROM messages
   WHERE chat_jid = :chat_jid AND timestamp >= :since
   ORDER BY timestamp;
   ```
   Use `:since` = the record's `last_contact` minus 1 day, or 14 days ago if missing.
   If `chat_jid` is empty, resolve it from `phone` via the lid map / contacts in
   `whatsapp.db` (whatsmeow_lid_map maps lid to pn; whatsmeow_contacts has names).
4. Read the messages and extract ONLY genuinely new or changed details for that unit:
   rent or room price changes, which rooms are still available, lease minimum or
   flexibility, gender / ethnicity / occupation / pax preferences, cooking, pets,
   smoking, utilities, visitors, owner on site, availability or move in date,
   commission terms, address or unit corrections.
   - Merge each new detail into the matching field under `requirements` (or
     `rooms_and_rent`, `full_address`, `property_type`, `follow_up`). Append, refine
     or correct; never wipe.
   - NEVER record "URA quota" (or "URA EIP", "ethnic quota") as the reason for any rental
     requirement. URA's ethnic integration quota applies to HDB/EC PURCHASE and whole flat
     letting, NOT room rentals, so it is factually wrong for these listings. If a landlord
     states an ethnicity restriction (even if the landlord themselves cites a "URA quota"),
     record it plainly as the landlord's own preference, e.g. `ethnicity: "No Indian
     (landlord preference)"`, never attributed to a quota. Keep the preference (it is used
     for matching); only the false quota justification is forbidden.
   - If the landlord clearly says the unit is now rented out or taken, set
     `status` to `closed (tenanted)` and `follow_up` to `Rented out; closed`. ALSO set
     every open slot for this landlord's listing_key in `viewing-availability.json` to
     `status: "closed"`, so a stale slot for a rented unit is never offered. For a
     multi-listing landlord, close ONLY the slots of the listing that is actually
     rented out, not their other still-active listing.
   - Never REOPEN a closed unit or a closed slot. A later chat message (even one that
     mentions a viewing time) does NOT reopen a `closed (tenanted)` landlord or flip a
     slot whose `status` is `closed` or `full` back to `open`. Closed is terminal for
     this skill. If a landlord genuinely re-lists, that is a manual decision for Winfred,
     not something this silent refresh does. Count a close in `landlords_closed` only when
     YOU changed the status this run from non-closed to closed; do not count units that
     were already closed before the run.
   - Update `last_contact` to the latest inbound date and add/refresh
     `last_refreshed` with today's date. Keep `contact_label_source`.
4b. CAPTURE THIS LANDLORD'S VIEWING AVAILABILITY (their next viewing time slot).
   `fixed_viewing` rules are RETIRED (Winfred, 10 Jul 2026): the intake engine now ASKS
   each tenant when they are free to view, and Winfred confirms times by hand. NEVER add,
   restore, or re-create a `fixed_viewing` key on any listing in `listing-index.json` —
   not from chat history, not from old backups, not from examples you remember. (A nightly
   run on 12 Jul resurrected them from this very instruction; that must not recur.)
   From the same recent messages, detect any viewing availability the landlord offers
   ("can view this Sat 3 to 5pm", "weekday evenings after 7", "only Sunday this week",
   "keys with me, anytime"). When you find a concrete date or window:
   - Map this landlord to their `listing_key`. Each entry in `viewing-availability.json`
     carries a `landlord_id`; `listing-index.json` also maps landlord_id to listing_key.
     If the landlord owns more than one listing (e.g. LL012 maps to BOTH caspian and
     summerdale), attach the slot to the listing the message is clearly about. Decide
     which listing by a concrete signal in the message: the property name, block, unit,
     room, or a portal link that matches one listing's `pg_url_keywords`. If the message
     does not clearly name one of this landlord's listings, it is AMBIGUOUS: attach to
     NONE, do NOT pick the first listing or guess, and note "ambiguous slot for LLxxx,
     could be <listingA> or <listingB>" in the digest. NEVER attach availability to a
     closed listing, and never attach to a listing whose `status` in `listing-index.json`
     is `hold` or whose `landlord_id` is null.
   - Upsert a slot into `viewing-availability.json` under that listing_key's `slots`
     array, as: `{ "slot_id": "<listingkey>-YYYY-MM-DD-HHMM", "date": "YYYY-MM-DD",
     "start": "HH:MM", "end": "HH:MM", "label": "<human readable, no hyphens e.g. Sat 21
     Jun, 3 to 5pm>", "capacity": <4, or 1 for a single room viewing>, "booked": 0,
     "status": "open" }`. Do NOT duplicate a slot that already exists (same listing_key
     + date + start). Leave past slots alone (the engine auto hides them).
   - Also write a short `viewing_availability` field on the landlord record (e.g.
     "Sat 21 Jun 3 to 5pm; prefers weekday evenings") plus `viewing_availability_updated`
     = `TODAY_SGT` (the real date from bash `date`, never invented). Never invent a slot
     the landlord did not actually give.
   - `slots_captured` is the count of slots you ACTUALLY upserted as NEW this run. Do not
     count a slot that already existed (same listing_key + date + start), do not count a
     slot you decided was ambiguous and skipped, and do not assume one slot per landlord.
     Most landlords give no new slot on most nights, so a normal run captures 0 to a few.
     If you upserted zero new slots, write `slots_captured: 0`. Never pad this number.
   - Idempotency on retry: re-running must not duplicate or re-add a slot. A slot is
     identified by `slot_id` and by (listing_key + date + start). If a matching slot is
     already present, leave it as is (do not bump `booked`, do not reset `status`) and do
     NOT count it again in `slots_captured`.
5. Discover NEW landlords. First run `PRAGMA table_info(whatsmeow_contacts)` to learn
   the real column names, then match a saved name containing "landlord" (case
   insensitive) across whichever name columns exist (e.g. `full_name`, `first_name`,
   `push_name`). From `whatsapp.db`, list such contacts whose phone is NOT already in
   the DB.
   For each, read their recent chat, extract address + unit + requirements, and add a
   new `LLxxx` record (next id) with `status: active` and
   `contact_label_source: "saved WhatsApp contact (name contains 'landlord')"`.
   If a supposed new landlord has no usable rental details, add them with
   `status: dormant` and a `follow_up` noting what is missing. Never invent details.
5b. Discover NEW landlords by CONTENT, for contacts NOT yet labelled (added 20 Aug
   2026: ~10 landlord side chats from 13 to 19 Aug reached neither DB because the
   contacts were not relabelled yet; step 5 only sees saved names). Run AFTER step 5.
   This pass is READ ONLY on WhatsApp and ADD ONLY on the DB: it never messages
   anyone, never updates, deletes, or reopens an existing record.
   - Candidates = direct chats whose FIRST EVER message is within the last 14 days,
     across BOTH message tables (recent rows rotate into the archive, so reading only
     `messages` misses chats):
     ```sql
     SELECT chat_jid, MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts
     FROM (SELECT chat_jid, timestamp FROM messages
           UNION ALL
           SELECT chat_jid, timestamp FROM messages_archive)
     WHERE chat_jid NOT LIKE '%@g.us' AND chat_jid NOT LIKE '%@broadcast'
       AND chat_jid NOT LIKE '%@newsletter'
     GROUP BY chat_jid
     HAVING MIN(timestamp) >= :window_start;
     ```
     `:window_start` = `TODAY_SGT` minus 14 days as a plain `YYYY-MM-DD` string
     (timestamps are already SGT; string comparison is correct, see the timezone
     note). Direct chats only (`...@lid` or `...@s.whatsapp.net`); never groups,
     broadcasts, newsletters, or `status@broadcast`.
   - Resolve identity and DEDUPE before reading any content, so most nights only a
     handful of chats get read:
     - Resolve `...@lid` to the real phone via `whatsmeow_lid_map` (`lid` to `pn`) in
       `whatsapp.db`; an `...@s.whatsapp.net` jid carries the phone directly. Pull
       the saved/push name from `whatsmeow_contacts` as a name hint.
     - SKIP silently any chat whose phone or chat_jid is already in
       `landlord-db.json` (ANY status, INCLUDING closed) or `tenant-db.json`. A
       closed or dropped landlord is NEVER re added by this pass, whatever the chat
       says (e.g. LL113 stays dropped).
     - SKIP exclusion list matches: the Golden rules colleagues (Wanni, Shaw,
       Madeleine, Darren, Amanda), Don Chuang (colleague, never a lead; his number
       is in the exclusion file below), and every `non_clients[].phone` in
       `~/.claude/state/dm-followup-exclude.json` (match full digits OR last 8, per
       that file's convention).
   - Classify each surviving chat by reading its first 15 and last 15 messages
     (case insensitive matching). LANDLORD SIDE if ANY of:
     - AUTHORITATIVE (Winfred, 20 Aug 2026): an OUTBOUND (`is_from_me = 1`) message
       containing his landlord intake questionnaire, e.g. "screen tenants before
       bringing them to you", "could you help me answer these questions", "Sole
       owner or co-owners", "I see that you would like to rent out your". If we sent
       the landlord enquiry, it is a landlord side chat.
     - INBOUND (`is_from_me = 0`) offering a unit: "I have a room for rent", "rent
       out my room", "rent out my unit", "I am the owner", "owner of", or a room or
       unit description with an asking rent.
     - The contact ANSWERS the intake questionnaire (describes THEIR unit: address,
       room type, asking rent, tenant preferences, when it can be viewed), even with
       no marker phrase hit (a real 20 Aug 2026 case: a landlord answered the
       questionnaire without any marker phrase matching).
     - Carousell mentioned in a rental context, either direction: Winfred sources
       landlords on Carousell; tenants arrive via PropertyGuru / 99.co.
     NOT landlords, add NOTHING for:
     - Co-broke / other agents: agency names (PropNex, Huttons, ERA, OrangeTee) or
       agent phrasing from the counterparty ("co-broke", "my commission", "I take my
       own com"). Agents are never prospects.
     - Tenants (looking FOR a room): Part B already discovers them; a chat this pass
       classifies as landlord side is EXCLUDED from Part B new tenant adds.
     - Sale side owners (selling, not renting out): a sale seller in the rental DB
       is Winfred's manual call (20 Aug 2026 precedent: a seller saved as Owner was
       correctly NOT added to the rental DB).
     - AMBIGUOUS: when you cannot tell which side, add nothing and count it in the
       digest; the 14 day window re examines it nightly as the chat develops. When
       unsure, this is the safe bucket.
   - Add each confirmed landlord as a new `LLxxx` record exactly per step 5
     conventions (`status: active` with usable details, else `status: dormant` with
     a `follow_up` naming what is missing; never invent details), EXCEPT:
     - `contact_label_source: "content sweep (contact not yet labelled)"`
     - Append to `follow_up`: "Save this contact with Landlord in the name so the
       label based pass tracks them." (no hyphens or dashes in data text)
     - `phone` = the resolved real phone; `chat_jid` = the actual jid (lid form is
       fine); `landlord_name` from the chat or push name, else "Unknown (not yet
       labelled)".
   - Count these adds inside the existing `landlords_added` (Step Z marker keys are
     UNCHANGED). Add ONE digest line, counts only, e.g. "Content sweep: 12 new chats
     checked, 2 unlabelled landlords added, 1 ambiguous skipped." NEVER a name,
     phone, or jid in the digest (PDPA).
6. Recompute the top level counts (`total`, `rental_landlords`, etc. as best they map)
   and set `last_updated` to `NOW_SGT` from the Clock section (the real wall clock you
   read with bash `date`). NEVER write a rounded or invented time here.
7. Write `landlord-db.json` ATOMICALLY (temp file then move into place). Immediately after
   the JSON write, run the deterministic sanitizer so no false "URA quota" rental reason
   can persist (a backstop to the rule in Step 4):
   ```bash
   python3 ~/crestbrick-consult/scripts/sanitize_landlord_db.py --apply
   ```
   It strips "URA quota" / "URA EIP" / "ethnic quota" wording from landlord requirements,
   rewriting it as "(landlord preference)", and is idempotent (a clean DB reports 0).
   THEN reconcile the LIVE matcher's gates from the (now sanitized) landlord DB, so a
   landlord changing their terms actually reaches the gates the intake bot enforces:
   ```bash
   python3 ~/crestbrick-consult/scripts/sync_listing_index_from_landlords.py --apply
   ```
   It derives `listing-index.json` gates (gender + couple flags, ethnicity_rule, lease
   min/max, min_age) from `landlord-db.json` for every active landlord-bound listing,
   preserving the hand-curated gate where the landlord text is silent or ambiguous (it
   never auto-changes `max_pax` or `budget_floor`, and it scopes a property-specific
   requirement like "No Indian at Caspian" to that listing only). Idempotent (in-sync
   reports 0). It marks any change that WEAKENS a restriction (e.g. `female_only -> any`,
   lease lowered) with ⚠️ in its output; if it printed any change, note the count (and any
   ⚠️ weakenings) in the Telegram digest so Winfred can eyeball them. This must run BEFORE
   Part B so tenant matching uses the refreshed gates.
   THEN regenerate `docs/landlord-database.csv` from the sanitized JSON:
   FIRST read the existing CSV header row and keep its
   exact column order; only add a trailing column if a genuinely new field appeared
   (e.g. `viewing_availability`). One row per landlord, requirements expanded into their
   own columns. Then push the same rows to the landlord Google Sheet: find it by exact
   title (titles containing "Landlord" under the `winfred` workspace account). If zero
   or more than one match, SKIP the sheet push, keep the JSON and CSV, and note
   "landlord sheet not synced (title ambiguous)" in the digest. Never guess a sheet.
   Keep any "Completed" / closed tab convention already in the sheet. A CSV or Sheet
   failure is NON FATAL: it must never corrupt or block the JSON write.

## Part B: Prospective tenant database

Part A must be FULLY written and saved (Step 7 done: JSON, CSV, and the viewing
availability upserts) before you begin Part B, so a timeout during tenant work never
loses completed landlord updates.

### Lifecycle fields and anti-spam (maintain on every tenant)

Each tenant carries: `data_collected` (the SGT date `TODAY_SGT` the tenant was FIRST
added; set it once on creation and never change it), `contact_state` (one of `active`,
`found_place`, `not_interested`, `do_not_contact`), and `contact_state_updated` (the SGT
date the state last changed). Rules:
- On a NEW tenant, set `data_collected` = `TODAY_SGT`, `contact_state` = `active` (or
  `do_not_contact` if excluded as a landlord, agent, or colleague).
- Read their recent messages for lifecycle signals and update `contact_state` +
  `contact_state_updated` = `TODAY_SGT` when found: "found a place / already rented /
  no longer looking" -> `found_place`; "not interested / not anymore / please stop /
  unsubscribe / remove me" -> `not_interested` or `do_not_contact`. A tenant whose
  `status` is `tenanted` is `found_place`.
- ANTI-SPAM: a tenant whose `contact_state` is `found_place`, `not_interested`, or
  `do_not_contact` must NEVER be messaged or re-engaged again, by this skill, the
  intake engine, or the re-engagement map. Never flip them back to `active` from an old
  message; only a clear fresh "I am looking again" does that.

1. Read `tenant-db.json`. Note `columns`, `total_records`, and the highest `TNxxx` id.
2. Update existing OPEN prospects. For each tenant whose `status` is open (not
   `closed`, `rejected`, `tenanted`, `excluded`), pull recent messages by `jid`
   (same SQL as above, keyed by `chat_jid = jid`) since `last_contact`. Merge any
   changed requirement: budget, move in date, lease term, no of pax, gender,
   ethnicity, nationality, pass type, occupation, preferred location, listing
   enquired. Never blank a known field. Update `last_contact`.
   Do NOT hand derive the pipeline `status` (form-sent / profile-received / rejected)
   here: Part C step 0 runs `derive_tenant_status_from_wa.py`, which reconciles `status`
   against `messages.db` deterministically (it catches forms Winfred sent manually from
   his phone, profiles typed back, and self rejections that the state files miss). Leave
   an existing tenant's `status` as is in Part B and let that script correct it.
3. Add NEW prospective tenants. Find genuine tenant enquiries from the last 14 days
   in `messages.db` whose phone is NOT already in the tenant DB and is NOT excluded
   (see Golden rules: not a landlord, agent, or colleague; nor a chat that Part A
   step 5b classified as landlord side this run). A genuine enquiry asks
   about renting / viewing / a room (interested, still available, can I view, looking
   for a room, budget, move in). For each, create a `TNxxx` record mirroring the
   existing schema (id, name, phone, jid, gender, ethnicity, nationality, pass_type,
   occupation, no_of_pax, move_in_date, lease_term_months, budget, preferred_location,
   listing_enquired, status `open`, last_contact, excluded false, matches, suggested_new).
   Leave unknown fields null rather than guessing.
4. Re-run matching for every new or updated tenant against
   `listing-index.json` using the closed enum gates (gender female_only/male_only/
   *_pref/any; ethnicity_rule exclude/only/prefer/any; budget_floor; lease_min/max;
   max_pax; min_age). Write the `matches` array (listing_key, verdict MATCH/FAIL,
   failed_or_gap) and a short `matches_summary`. Honour the matching caveat: coded
   gates only, human nuances in landlord notes are not enforced, so do not treat a
   MATCH as an instruction to message anyone.
5. Recompute counts (`total_records`, `prospective_tenants`, `open`, `closed`,
   `open_with_active_match`) and set `last_updated` to `NOW_SGT` (the real wall clock
   from bash `date`, never invented or rounded). Tenant `last_contact` is the SGT date
   of the prospect's latest genuine inbound message from `messages.db`, written as
   `YYYY-MM-DD`, never today's date and never a time.
6. Write `tenant-db.json` ATOMICALLY (temp then move), regenerate
   `docs/tenant-database.csv` (preserve the existing header column order, append only
   for new fields), and push to the tenant Google Sheet (id above), keeping its open +
   matched view convention. CSV or Sheet failure is NON FATAL and must not block or
   corrupt the JSON write.

## Part C: Tenant location map and re-engagement suggestions

After Part B has fully written `tenant-db.json`, build the third database: a district
map of every prospective tenant plus, for tenants who were rejected or went cold (or are
mid funnel), a list of ACTIVE listings near their preferred area to introduce them to.
This is what lets Winfred re-approach a rejected tenant or tell them a new rental opened
where they were looking.

0. FIRST reconcile each tenant's `status` against the WhatsApp message DB (the
   authoritative record), THEN set lifecycle. Run them in THIS order:
   ```bash
   python3 ~/crestbrick-consult/scripts/derive_tenant_status_from_wa.py --apply
   python3 ~/crestbrick-consult/scripts/apply_tenant_lifecycle.py
   ```
   `derive_tenant_status_from_wa.py` fixes the stale status problem: the pipeline state
   files only know about forms the AUTOMATION sent, so a form Winfred sent MANUALLY from
   his phone, a profile a tenant typed back, or a tenant who self rejected leaves the
   record stuck at `open`/`cold`. The script reads each tenant's real chat and, reusing
   the same form detection as `pg-enquiry-sop.sh` (conversation-state.json primary, then
   an `ALREADY_SENT_RE` message scan), upgrades status to at least `form-sent` when the
   form was sent, to `profile-received` when the tenant typed a profile back, and to
   `rejected` when the tenant's LAST inbound is a rejection ("over budget", "found a
   place", "not interested"). It NEVER downgrades, NEVER touches a deal stage record
   (viewing-set, viewed, deposit-pending, tenanted), NEVER touches an already `rejected`
   or `excluded` record, and is idempotent. On a rejection it also tightens
   `contact_state` (an "over budget" rejection stays `active` so the re-engagement map
   can still serve them a cheaper unit; "found a place"/"not interested" goes to
   `found_place`/`not_interested` so the anti-spam gate takes them off the list). It must
   run BEFORE `apply_tenant_lifecycle.py` so the corrected status and contact_state flow
   into `match_status` and the match board / area demand / re-engagement builders. It
   writes `tenant-db.json` atomically and prints a one line summary of corrections; fold
   that count into the digest if non zero.

   Then `apply_tenant_lifecycle.py` sets `match_status`: `found` (has a place, archived off the active list),
   `do_not_contact`, `in_deal` (deposit pending), `short_lease` (under 6 months, FILTERED
   out of matching since landlords want 12 months), `below_target` (6 to 11 months, kept
   and matchable but flagged), `active` (12 months or more, or unknown). Aim is 12 months
   minimum. A tenant detected in Part B as having found a place or opted out must already
   have its `contact_state` set, which this step turns into `found` / `do_not_contact`.
1. Run the deterministic builder (districts are mapped from a fixed keyword table, NOT
   guessed, so nothing is fabricated):
   ```bash
   python3 ~/crestbrick-consult/scripts/build_tenant_location_map.py --apply
   ```
   This adds `district` and `preferred_districts` columns to every tenant in
   `tenant-db.json`, and writes `_templates/tenant-location-map.json` plus
   `docs/tenant-location-map.csv`. A suggestion is only made when an active listing sits
   in a tenant's preferred district, is not the listing they already enquired on, was not
   already a FAIL in their `matches`, and fits their budget if both are known.
2. If a NEW listing was added in Part A, also add it to the `LISTING_DISTRICT` table at
   the top of `build_tenant_location_map.py` (listing_key to area and district), so the
   new rental can be matched to tenants who want that district. This is the one manual
   touch point; without it a brand new listing will not generate suggestions.
3. Sync the third Google Sheet "Crestbrick Tenant Location Map"
   (id `10inrbz3iEGuujcseZ8ikBIQRqCmr7cC2c4r4T4Wbx9M`): write the rows that have at least
   one suggestion (sorted by `suggested_count` descending) with the columns id, name,
   status, preferred_location, preferred_districts, budget, no_of_pax, listing_enquired,
   suggested_count, top_suggestions. Sheet failure is NON FATAL. PDPA still applies: this
   sheet holds names so it stays in Google Sheets only, never pasted into Telegram.
4. Read `tenant-location-map.json` for the digest counts (`with_suggestion` etc.). Never
   message any tenant from this skill; these are suggestions FOR Winfred to action.
5. Build the fourth list, AREA DEMAND (landlord sourcing): run
   ```bash
   python3 ~/crestbrick-consult/scripts/build_area_demand.py --apply
   ```
   It writes `_templates/area-demand.json` + `docs/area-demand.csv`, ranking each district
   by UNMET demand (waiting tenants with no fitting active listing, `unmatched_waiting`,
   NOT just whether any listing exists) versus supply, so Winfred knows where to source
   landlords. Counts only tenants who are `active`, still re-engageable, and not short
   lease.
5b. Build the fifth list, the LANDLORD to TENANT match board (the linking engine):
   ```bash
   python3 ~/crestbrick-consult/scripts/build_match_board.py --apply
   ```
   It writes `_templates/match-board.json` + `docs/match-board.csv`: for every active
   listing, the active looking tenants (lease 6 months or more) who want that district AND
   pass the qualify() gates (gender, ethnicity, pax, lease, budget). This is what links a
   NEW landlord to waiting tenants and a NEW tenant to fitting listings.
6. Refresh the UNIFIED master sheet "Crestbrick Rental Master"
   (id `1ujZJZE-LkGC4wsTsIfrI7Uzl6rfISMSA7A1Vp7cPT5w`). SIX tabs joined by the shared
   `district` key: Landlords (district + rent_min/rent_max), Prospective Tenants (ACTIVE
   list only, found tenants removed, with match_status), Re-engagement Map, Area Demand
   (sourcing, by unmatched_waiting), Landlord Shortlists (the match board), Archive (found
   and opted out, kept for record), and Dashboard. Rebuild each tab from the just written
   JSON files. When a tab shrank, CLEAR the old range before writing so no stale rows
   remain. SORT the Prospective Tenants tab and each listing's shortlist by `priority`
   (1 = 12 months or more first, then 2 = 6 to 11 months, then 3 = lease unknown), so the
   longest lease tenants are served first. Sheet failure is NON FATAL.

## Part D: Stale prospect sweep + Client Database live sync (added 4 Jul 2026)

Run AFTER Part C, before Step Z. Deterministic scripts do the data work; you only push.

1. Auto-close stale prospects (Winfred's rule: silent for more than 30 days = undesirable,
   close them). Then re-run the lifecycle so `match_status` reflects the closures:
   ```bash
   python3 ~/crestbrick-consult/scripts/close_stale_prospects.py --apply
   python3 ~/crestbrick-consult/scripts/apply_tenant_lifecycle.py
   ```
   The sweep is idempotent, never messages anyone, and never touches rejected / tenanted /
   found_place / deposit-pending / excluded / already-closed records. Closed records get
   `status = "closed (stale)"` with `prev_status` preserved.

2. Rebuild the export arrays:
   ```bash
   python3 ~/crestbrick-consult/scripts/export_client_db_tabs.py
   ```
   This writes `~/.claude/state/client-db-export/{landlords_active,landlords_closed,tenants}.json`
   (each a 2D array, header row first) plus `meta.json` with the row counts.

3. Push all three arrays to the PINNED spreadsheet "Crestbrick Client Database"
   id `1WdCMc0ktexARRVtHqMUoeYrPdS_HFYAk8pnPFobSeik` (google-workspace MCP, account
   `winfred`) — one tab per file:
   - `landlords_active.json` -> tab `Landlords Active`, write at `A1`
   - `landlords_closed.json` -> tab `Landlords Closed`, write at `A1`
   - `tenants.json`          -> tab `Tenants`, write at `A1` (28 columns, A to AB)
   Push the file contents EXACTLY as written by the script; never edit, filter, or reorder
   rows yourself. For a large tab (Tenants), write in chunks of about 100 rows. After
   writing each tab, if the new row count (from `meta.json`) is smaller than the previous
   sync's, CLEAR the leftover range below the new data (e.g. `Tenants!A320:AB1000`) so no
   stale rows remain. Sheet failure here is NON FATAL: keep the JSON exports, say
   "client-db sheet not synced (<error>)" in the digest, and never mark it synced.

4. Add one line to the Telegram digest: "Client DB sheet synced: X active / Y closed
   landlords, Z tenants; N stale prospects auto closed." (counts only, PDPA).

## Step Z: Report

Write the run marker `~/.claude/state/refresh-rental-dbs-last.json` ATOMICALLY (temp
then move) as a single JSON object with EXACTLY these keys, so the nightly runner can
detect a completed run (it greps for `"date"` and `"ok": true`):

```
{ "date": "<TODAY_SGT YYYY-MM-DD>", "ok": true, "ran_at": "<NOW_SGT, e.g. 2026-06-16T04:58:47+0800>",
  "landlords_scanned": N, "landlords_updated": N, "landlords_added": N,
  "landlords_closed": N, "slots_captured": N, "tenants_updated": N, "tenants_added": N,
  "tenants_with_suggestion": N }
```

`tenants_with_suggestion` is the `with_suggestion` value from `tenant-location-map.json`
(Part C). Add one line to the Telegram digest, for example: "Re-engagement: 42 tenants
have an active listing near their preferred district to introduce." Counts only, no names.

`date` MUST be `TODAY_SGT` and `ran_at` MUST be `NOW_SGT`, both read from the real bash
`date` command (see the Clock section). NEVER write a rounded or midnight `ran_at` such
as `00:30:00`; that is the exact bug this skill must not reproduce. Every count is the
honest number of records you actually changed on this run (0 is a valid and common
value), not an estimate and not one-per-landlord.

Set `"ok": false` instead of true if a hard failure stopped the run partway (this lets
the wrapper retry). Even on `"ok": false`, still write `date` and `ran_at` from the real
clock and fill the counts with what you DID finish, and add a short `"error"` string
saying what failed. A partial run that finished Part A but failed in Part B should record
the Part A counts truthfully and set `ok:false`. Then send Winfred a short Telegram digest
(`bash ~/.claude/bin/telegram_send.sh 540127870`, message on stdin), for example:

```
Nightly DB refresh done.
Landlords: 3 updated, 1 new, 1 closed (skipped 5 closed units).
Viewing slots captured: 2 (caspian Sat 21 Jun, bayshore Sun 22 Jun).
Tenants: 6 updated, 4 new enquiries added, matched against active listings.
No messages were sent to anyone.
Winfred Quek | CEA R073319H
```

The digest is COUNTS ONLY (PDPA). You may name a listing slug and a date for a captured
slot (e.g. "caspian Sat 21 Jun"), but NEVER put a client name, a phone number, a tenant
list, or a landlord's PII in the digest. The slot count in the digest must equal
`slots_captured` in the marker; do not claim slots you did not actually upsert.

If nothing changed, say so in one line (for example "Nightly DB refresh done. No new or
changed requirements. No messages were sent to anyone. Winfred Quek | CEA R073319H").
Never pad with filler and never inflate a count to look busier. On any hard failure,
write the marker with `"ok": false`, the real-clock `ran_at`, the truthful partial
counts, and a short `error`, then let the wrapper alert. Never leave a half written JSON
file (always temp then move).

## Note for the headless run

This skill runs unattended via `claude -p`, so there is no one to clarify with. When an
instruction here could be read two ways, choose the SAFER reading: do less, change less,
and flag it in the digest rather than guessing. The safe defaults are: never message
anyone, never blank a field, never reopen a closed unit or slot, never attach an
ambiguous slot, never fabricate a timestamp or a count, and always write files atomically
(temp then move) so an interrupted run cannot corrupt a database.
