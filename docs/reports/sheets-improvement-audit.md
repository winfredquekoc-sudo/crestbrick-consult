# Rental Sheets Improvement Audit

Prepared 2026-06-16. Internal only. Do not send externally (PDPA: tenant and landlord names appear below).

This report audits the three live rental Google Sheets and proposes ranked, specific improvements. Nothing in the live sheets was changed. More listings arrive by end of week, so items that scale with supply growth are flagged with "matters more as supply grows".

## Sheets in scope

1. Landlord: **Crestbrick Landlord Database** (id 1KvY00UPrfKYIE-LpDRq7LxfdsVD5xZMBX4F6BSa03i4). Two tabs: Sheet1 (29 cols, 31 landlord rows LL001 to LL032) and Completed (mirror of the closed rows). Local source: _templates/landlord-db.json, docs/landlord-database.csv.
2. Prospective tenant: **Crestbrick Prospective Tenants (open + matched)** (id 1r7M3eGh84s4QrgAX1IS3nVLiTtauqpjvzlDXngNluMg). One tab, 18 cols, 250 tenant rows (228 prospective, 22 excluded co-broke or non-tenant). Local source: _templates/tenant-db.json, docs/tenant-database.csv.
3. Tenant Location Map: **Crestbrick Tenant Location Map (district + re-engagement suggestions)** (id 10inrbz3iEGuujcseZ8ikBIQRqCmr7cC2c4r4T4Wbx9M). One tab, 225 re-engage candidates grouped by district, 42 with a live nearby suggestion. Local source: _templates/tenant-location-map.json, docs/tenant-location-map.csv.

---

## QUICK WINS (approve in one go, all three sheets)

These are low risk, high value, and can be applied in a single pass. None of them delete data.

1. Freeze the header row (View, Freeze, 1 row) on all three sheets, and freeze the ID and Name columns (first two columns) so they stay visible when scrolling right. The landlord sheet at 29 columns is unreadable past column J without this.
2. Turn on a Filter view on every sheet so each can be sorted by status, district, budget, or last contact without disturbing the shared view.
3. Status colour coding via conditional formatting. Suggested scheme used consistently across all sheets: green for closed or tenanted, amber for active or viewing-set or profile-received, grey for cold or dormant or stalled or no-response, blue for sale-active or channel. One legend, three sheets.
4. Format the phone column as plain text on all three. Leading plus signs were stripped on upload (for example 6592717571 instead of +6592717571), which breaks click-to-WhatsApp and tel links.
5. Format the budget and rent columns as numbers or currency, and the date columns (last_contact, move_in_date) as real dates, so sorting works. Right now several sort as text.
6. Resolve the duplicate landlord sheet: the older **landlord-tracker-crestbrick** (id 1iJowhQeOzWlqgj341Pf3q32z_A4xBo6_7H6tt_kBr2o, 11 columns, 23 rows, mostly end of May) is superseded by Crestbrick Landlord Database. Rename it to "ARCHIVE landlord-tracker (superseded)" or move it to an archive folder so nobody updates the wrong one.

---

## SHEET 1: Crestbrick Landlord Database

Observed: 31 rows, status mix is 15 active, 5 stalled, 4 closed (tenanted), 2 sale-active, plus dormant, active-verify, tenanted, channel and no-response singletons. Note two different "done" labels exist: "closed (tenanted)" and "tenanted" (LL026) mean the same thing.

### High

1. **Add a District column.** The tenant side now has district and preferred_districts, but the landlord sheet has none. Without it you cannot do the listing to tenant match that the location map is built for. District can be derived from postal code or address. This is the single biggest cross-sheet gap. Matters more as supply grows: with 5 to 10 new listings a week, a district column is what lets you answer "which of my waiting tenants fits this new unit" in seconds. Place it next to Postal.
2. **Normalise the status vocabulary.** Today there are nine distinct status values, some overlapping: "closed (tenanted)" vs "tenanted", "active" vs "active-verify", "stalled" vs "dormant" vs "no-response". Collapse to a small controlled set, for example: Active, Verify, Stalled, Dormant, Closed, Sale-active, Channel. Apply it as a data-validation dropdown so future rows stay clean. This also makes the colour coding and any dashboard reliable.
3. **Add an "Available rooms" or "Vacancy" view or column.** Several rows describe multiple rooms with different availability dates buried in free text (LL008 two common rooms; LL012 Frank POA has master plus CC3 now, CC4 from 1 Jul, Summerdale SM now, SC5 from 1 Aug; LL020 two rooms vacant). A landlord-level row cannot express per-room vacancy. Either add columns Rooms available now / Next available date, or create a derived "Vacancy" tab with one row per available room (see new tab below). This is the data Winfred actually queries when a tenant calls.

### Medium

4. **Split or standardise the free-text rent field.** "Rooms & rent" mixes room type, price, per-pax pricing and conditions in prose (for example "Master $2,300 / Common $1,300 (2pax $1,400)"). Add structured helper columns Rent min and Rent max (numeric) so the sheet is filterable and sortable by price band and matchable against tenant budget. Keep the prose field for nuance.
5. **Fill or flag the stalled and unknown rows.** 8 of 31 rows have no usable rent ("Not stated" or blank): LL021, LL022, LL023, LL024, LL027, LL032 and others. 19 of 31 have no postal code. These are dead weight in any match query. Add a single "Data complete" checkbox column, or use colour coding to grey them out, so they do not pollute live matching.
6. **Last-contact ageing.** Add a conditional format that highlights any active or sale-active landlord whose Last contact is older than say 10 days, so stalled-but-live deals do not go cold. LL019, LL020, LL029 are already a week-plus out.

### Low

7. The Completed tab is a manual mirror and can drift from Sheet1. Either drive it by filter view off the Status column instead, or document that it is regenerated by the refresh script so nobody hand-edits it.
8. ID gap: LL031 is missing (jumps LL030 to LL032). Harmless, but worth a note so it is not mistaken for a deleted row.
9. Phone for LL006 (Ken Ho, 85291243199) is a Hong Kong number and LL017 notes the landlord is overseas in Vancouver. A "Landlord based overseas" flag would set expectations on viewing logistics.

---

## SHEET 2: Crestbrick Prospective Tenants

Observed: 250 rows, status mix 95 rejected, 64 cold, 37 profile-received, 20 viewing-set, 16 form-sent, 13 viewed, 4 tenanted, 1 deposit-pending. No duplicate phones, no duplicate ids, no stale open rows (good). But: 58 rows have a blank name, 142 have no district, 133 have no preferred_location, 68 have no budget.

### High

1. **The 22 excluded co-broke or non-tenant records should not sit in the same view as live prospects.** The JSON tracks excluded plus exclude_reason, but the live sheet has 250 rows where 22 are agents or non-tenants. Per the standing rule never to treat CEA agents as prospects, move these to a separate "Excluded" tab or add an Excluded column with a filter that hides them by default. This prevents an agent ever being messaged as a tenant.
2. **Surface the match columns that exist in the data but not in the sheet.** The JSON has rich per-tenant matches (listing_key, verdict MATCH or FAIL, failed_or_gap reason) and a district and preferred_districts, but the live Sheet1 header stops at suggested_new and omits district. Add District and Preferred districts columns, and a Match count or Best match column, so you can filter "open tenants with at least one active MATCH" directly in the sheet. The build already computes open_with_active_match = 74; that number should be a one-click filter, not a buried JSON field. Matters more as supply grows: every new listing changes who has a match.
3. **Status hygiene for re-engagement.** 95 rejected plus 64 cold = 159 dormant tenants. These are exactly the re-engagement pool the location map targets. Add a "Re-engage by" date or a "Last re-engaged" column so the same cold tenant is not pinged repeatedly. Without it, the location map suggestions cannot be actioned safely.

### Medium

4. **Blank names (58 rows).** Many are phone-only enquiries. Add a rule or colour flag so blank-name rows are visibly "identity unknown" and are not surfaced in any outbound message that uses a name field. At minimum, never auto-greet by name when name is blank.
5. **Budget as a number, with band buckets.** 68 rows have no budget; the rest are stored as text in places. Convert to numeric and add a Budget band helper (for example under 1000, 1000 to 1500, 1500 to 2000, 2000 plus) so demand can be summarised against listing prices.
6. **Move-in date ageing.** Flag tenants whose move_in_date is already in the past or within 14 days and who are still "open" status; those are the hottest and should sort to the top.

### Low

7. preferred_location is free text ("Circle Line Caldecott to Buona Vista", "Near ESSEC Business School"). The district derivation already exists in the build; surface preferred_districts in the sheet so the free text does not have to be re-parsed by eye.
8. Consider a single "Stage" ordering on status (form-sent then profile-received then viewing-set then viewed then deposit-pending then tenanted) so a sort by stage reflects pipeline progression rather than alphabetical.

---

## SHEET 3: Tenant Location Map

Observed: 225 re-engage candidates grouped by district; only 42 have at least one live nearby suggestion, so 183 rows currently show zero suggestions. 56 rows have a blank name. Status spread of the 225: 95 rejected, 64 cold, 37 profile-received, 16 form-sent, 13 viewed. District spread is concentrated: D22 (30), D5 (23), D18 (22), D8 (14), D16 (14), D23 (13).

Note: the live header lists a column "top_suggestions" while the CSV source column is "suggestions" (full list). Pick one name and one scope so the sheet and source agree.

### High

1. **The 183 zero-suggestion rows make the sheet look empty and bury the 42 actionable ones.** Add a filter view or a dedicated "Actionable now" tab that shows only rows with suggested_count greater than 0. That is the list Winfred actually works from. Matters more as supply grows: as new listings land, rows flip from zero to actionable, so this filter is where new supply turns into re-engagement messages.
2. **Add a District summary or demand tab.** The JSON already computes district_spread. Put it in the sheet as a small table: District, number of waiting tenants, number of active listings in that district, gap. This instantly shows where demand outstrips supply (D22 has 30 waiting tenants; how many D22 listings exist?). This is the highest-leverage new view for deciding which areas to source listings in.

### Medium

3. **Full 225-row tenant-by-district tab.** Right now the sheet is suggestion-led. Add a companion tab sorted by district then budget that lists all 225 candidates even when they have no current suggestion, so that the moment a new listing appears in, say, D8, you can scroll to the D8 block and see all 14 waiting tenants. This pairs with the district summary above.
4. **Re-engagement tracking columns.** Add "Suggested on", "Contacted on", and "Outcome" columns so the same cold tenant is not re-pitched the same listing twice. This is the operational gap that stops the map from being used.
5. **Blank names (56 rows).** Same issue as the tenant sheet; flag identity-unknown rows so no nameless auto-message goes out.

### Low

6. The suggestions field is a long semicolon-delimited string in one cell. For the actionable rows, consider capping the displayed suggestions to the top 3 by priority (the data already has a priority field) and keeping the full list in the source JSON, to keep the cell readable.
7. Add a count of distinct listings suggested across all tenants, so you can see which listings are being recommended most and prioritise filling those.

---

## CROSS-SHEET CONSISTENCY

1. **District is the join key and it is missing on the landlord side.** Tenant and location-map sheets carry district; the landlord sheet does not. Adding it (High item on Sheet 1) is the prerequisite for any reliable listing-to-tenant match view. Do this first.
2. **Listing identity is inconsistent.** Tenants reference listings as keys (caspian, tampines-855, rivervale-185c) and also as prose ("Pine Grove Room S$1,588/mo"); landlords have no listing_key at all, only a landlord ID and an address. Introduce a stable Listing key on the landlord sheet (one key per available room) so all three sheets speak the same listing vocabulary. This is what unlocks an automatic match view.
3. **A unified "Vacancy to tenant match" view.** Once landlords carry district and listing keys, a single tab can show: available room, district, rent, then the waiting tenants whose district and budget fit. The data to build this already exists across the three sheets; today it cannot join because of items 1 and 2.
4. **Status vocabularies differ per sheet.** Landlord uses active or stalled or closed; tenant uses rejected or cold or viewing-set. That is fine because they describe different things, but the colour legend should be shared so green always means "done", grey always means "dead", amber always means "in progress".

---

## NEW TABS WORTH ADDING (ranked)

1. **High. Vacancy board (landlord sheet, new tab):** one row per available room, columns Listing key, Landlord, District, Room type, Rent, Available from, Status. Derived from the multi-room landlords. This is the single most useful new artefact and becomes essential the moment supply grows past what fits in your head.
2. **High. District demand summary (location map sheet, new tab):** District, waiting tenants, active listings, gap. Drives sourcing decisions.
3. **Medium. Full tenant-by-district tab (location map sheet):** all 225 candidates sorted by district then budget, so new listings can be matched against the full waiting pool, not only the 42 with current suggestions.
4. **Medium. Match view (cross-sheet, can live on the tenant sheet):** open tenants with at least one active MATCH, one row each, showing the matched listing keys. The build already counts 74 of these.
5. **Low. Landlord availability or viewing calendar view:** several landlords already store viewing windows in free text (LL002 "20 Jun 8am to 11am; 21 Jun all day", LL008 "daily 3pm to 8pm until 24 Jun", LL011 windows). A tab that parses these into Date plus Listing plus Window would let you batch viewings. Lower priority because the viewing-consolidation skill partly covers this, but it would make that skill's input cleaner.

---

## SUPPLY-GROWTH FLAG (what matters more by end of week)

As new listings arrive, prioritise in this order, because these are the items whose value scales directly with listing count:

1. District column on the landlord sheet (cross-sheet High 1).
2. Stable Listing key per available room (cross-sheet 2) and the Vacancy board (new tab 1).
3. Location-map "Actionable now" filter and District demand summary (Sheet 3 High 1 and 2), since each new listing flips dormant tenants into re-engageable ones.
4. Tenant sheet Match-count or Best-match filter (Sheet 2 High 2).

The pure hygiene and usability items (freeze rows, filter views, colour coding, phone and date formatting, archiving the duplicate landlord sheet) are worth doing immediately regardless of supply, and are bundled in Quick Wins above.
