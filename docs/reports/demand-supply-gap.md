# Rental Demand vs Supply and Data Quality Report

Generated 2026-06-16. Read only analysis. Nothing live was changed, no one was messaged.

Sources: `_templates/tenant-db.json` (via `_templates/tenant-location-map.json`, generated 2026-06-16 05:15), `.claude/state/listing-templates/listing-index.json`, district logic in `scripts/build_tenant_location_map.py`.

PDPA note: tenant names below are for Winfred's internal sourcing use only. Do not forward this file externally.

## Method

- Waiting tenant pool = the 225 re-engage candidates in the location map (statuses rejected, cold, form-sent, profile-received, viewed). Closed and won tenancies are already excluded by the build script.
- Demand per district counts every district a tenant listed in `preferred_districts`. A tenant who wants three districts counts once in each.
- Weighting: rejected and cold tenants weight 1.0 (genuinely unplaced, highest re-engage value), mid funnel form-sent and profile-received weight 0.7, viewed weight 0.5. The weighted column is what the shortlist is ranked on.
- Supply = the 17 active index listings mapped through the LISTING_DISTRICT table. A listing only counts as real supply if its status is `active` (listings on `hold` do not count).

## 1. Demand vs Supply by District

Active supply exists in only 5 districts: D16, D18, D19, D20, D22. Every other district has demand but zero active listings.

| District | Waiting tenants | Weighted demand | Active listings | Verdict |
|----------|----------------:|----------------:|----------------:|---------|
| D22 Jurong, Boon Lay, Lakeside | 26 | 23.1 | 2 (caspian, summerdale) | Covered |
| D18 Tampines, Pasir Ris | 21 | 17.7 | 3 active (+2 on hold) | Covered |
| **D5 Clementi, Buona Vista, NUS, Dover** | **20** | **17.5** | **0** | **GAP** |
| **D8 Lavender, Jellicoe, Farrer Park** | **13** | **11.6** | **0 (farrer-park on hold)** | **GAP** |
| D16 Bedok, Bayshore | 13 | 11.3 | 2 (bedok-north-522, bayshore) | Covered |
| **D23 Bukit Batok, Choa Chu Kang** | **11** | **9.8** | **0** | **GAP** |
| **D15 East Coast, Katong, Marine Parade** | **8** | **7.4** | **0** | **GAP** |
| D19 Hougang, Sengkang, Punggol | 7 | 5.9 | 5 | Oversupplied |
| **D9 Orchard, River Valley** | **5** | **4.7** | **0 (oxley-edge on hold)** | **GAP** |
| D3 Tiong Bahru, Queenstown, Redhill | 4 | 4.0 | 0 | Thin gap |
| D10 Holland, Bukit Timah | 4 | 4.0 | 0 | Thin gap |
| D11 Newton, Novena, Thomson | 4 | 4.0 | 0 | Thin gap |
| D14, D25, D27, D12, D17, D6, D7 | 2 to 3 each | low | mostly 0 | Long tail |
| D20 Bishan, Ang Mo Kio | 2 | 1.5 | 1 (sunshine-terrace) | Covered |

Key imbalance: D19 has 5 active listings for 7 waiting tenants (oversupplied), while D5 has 20 waiting tenants and zero supply. Sourcing effort is currently pointed at the wrong end of the island.

## Sourcing Shortlist (where to get a listing this week)

Ranked by weighted unplaced demand against zero or held supply.

### 1. D5 Clementi, Buona Vista, Dover, NUS area. 20 waiting tenants (weighted 17.5)

Budget band: median 1500, range 1000 to 3300, most clustered 1500 to 1800 for a room. Demand is dominated by NUS and NTU students and Buona Vista or Dover professionals. 18 of the 20 list D5 as their first choice district, so this is real targeted demand, not spillover.

The single biggest signal in the whole database: 25 tenants enquired about Pine Grove (a Dover or D5 condo) which is NOT in the active index. If you can secure one Pine Grove room or any Clementi or Dover room around 1500 to 1700, you unlock the largest waiting queue you have.

Top waiting tenants (internal): TN061 Fang Yu (1800, viewed), TN050 Vera (1800, rejected), TN218 Atharv Gupta (1700), TN210 Vera and Chenyu (2000), TN119 Akanksha Mathur (1600), TN221 Umara (1500), TN116 Yaobo Liu (1500), TN026 Devanshi Jain (1500), TN156 Prabhu (1500), TN205 Ramya (1000).

### 2. D8 Lavender, Jellicoe Road, Farrer Park. 13 waiting tenants (weighted 11.6)

Budget band: median 1200, range 1100 to 1400 for a room. Tightly clustered, easy to fill. 11 of 13 list D8 first choice.

farrer-park is in your index but on `hold` with no landlord record. Even bigger: 28 tenants enquired about 813 Jellicoe Road, which is NOT in the active index. That is the second largest phantom queue. Activating farrer-park (verify the landlord) or sourcing one Jellicoe or Lavender room near 1200 clears most of this district.

Top waiting tenants: TN100 Ann and Kai (1400), TN179 Alwin Pious (1400), TN126 Thazin Win (1400), TN081 Jerry Low (1200), TN229 Tzy Wei (1200), TN104 Sim Tian Rong (1200), TN128 Phang Zi Jian (1200), TN233 (1200, form-sent), TN168 Jada (1100, viewed). Four cold tenants here have blank budgets (TN080, TN155, TN012, TN239) so qualify them before viewing.

### 3. D23 Bukit Batok, Choa Chu Kang. 11 waiting tenants (weighted 9.8)

Budget band: median 1500, range 1100 to 1800 (one outlier at 3300 wanting a whole unit). Mixed first choice and west side spillover. Phantom listings here: The Warren (3 tenants) and 450C Bukit Batok West (4 tenants), neither in the active index.

Top waiting tenants: TN201 Ms Kuo (1500), TN180 Xiangyu (1500), TN222 Hannah George (1500), TN142 (1500, rejected), TN069 (1250, The Warren), TN211 Jinghan Qiu (1400), TN118 Loh Zhang Zhan (1400).

### Runner up: D15 East Coast, Katong, Marine Parade. 8 waiting tenants (weighted 7.4)

Mostly east side spillover (only 1 first choice), budget 900 to 2500. Lower priority than the three above because most of these tenants also accept D16 or D18 where you already have active stock. Worth a listing only if an east coast unit lands easily.

## Phantom Demand Summary (enquired listings not in your active index)

These are properties tenants asked about that you cannot currently service. They are the clearest possible sourcing brief because the tenant already raised their hand for that exact building.

| Phantom listing | District | Tenants waiting | Budget range |
|-----------------|----------|----------------:|--------------|
| 813 Jellicoe Road | D8 | 28 | 1000 to 1500 |
| Pine Grove | D5 | 25 | 1500 to 2500 |
| Treasure at Tampines | D18 | 8 | 3000 to 3300 |
| Bayshore Park | D16 | 4 | 1200 to 2000 |
| 450C Bukit Batok West Ave 6 | D23 | 4 | 1500 to 1800 |
| The Warren | D23 | 3 | 1200 to 1400 |

Treasure at Tampines is notable: 8 tenants at 3000 to 3300 for a whole unit, a higher value bracket than the rest of the book. One Treasure whole unit listing serves a budget tier nothing else in your stock reaches.

## 2. Data Quality

### 2a. Unmapped preferred_location (8 tenants, keyword table needs new entries)

These tenants typed a location that the TOWN_DISTRICT keyword table did not recognise, so they fell out of the district demand counts. Exact phrases and the fix:

| Tenant | Exact phrase | Proposed keyword add |
|--------|-------------|----------------------|
| TN071, TN134 | `Pine Grove` | `"pine grove":["D5"]` |
| TN203 | `Anywhere in Singapore` | leave unmapped (genuinely no preference) |
| TN093 | `near City Square Mall` | `"city square":["D8"]` (City Square Mall is in Farrer Park, D8) |
| TN096 | `104B Edgefield Plains` | `"edgefield":["D19"]` |
| TN213 | `near MBS, walking to MRT` | `"mbs":["D1"]`, `"marina bay sands":["D1"]` |
| TN219 | `near Haw Par Villa` | `"haw par villa":["D5"]`, `"pasir panjang":["D5"]` (already have pasir panjang) |
| TN125 | `Within 40 min commute of Embassy of Portugal Singapore` | leave unmapped (embassy is on Tanglin, but a 40 min commute is too broad to assign one district) |

Net keyword additions to make: `pine grove` D5, `city square` D8, `edgefield` D19, `mbs` and `marina bay sands` D1, `haw par villa` D5. Adding `pine grove` alone reclassifies the largest phantom queue into D5 and would lift D5 demand even higher.

### 2b. Blank names (56 tenants)

56 of 225 records have no name (id list in the appendix data). These are mostly WhatsApp enquiries captured before the contact was saved. They are not errors to delete, but any blank name with a blank budget should be qualified before you spend a viewing slot on it. The blank name plus blank budget cold tenants are the lowest quality rows in the book.

### 2c. Likely duplicate records

- Confirmed duplicate name: **Dakshesh Gusain** appears twice, TN206 (profile-received, budget 1400, Caspian) and TN057 (rejected, budget 1300, Caspian). Same person, two records, conflicting status and budget. Merge to one record, keep the more recent profile-received state.
- The enquiry plus budget plus pax signature scan surfaced many same value clusters, but almost all are distinct people sharing a common Caspian room price band (1000, 1100, 1200), not true duplicates. Do not auto merge those.

### 2d. Listings not in the LISTING_DISTRICT table

None. All 17 active index listings are present in LISTING_DISTRICT and all 17 LISTING_DISTRICT keys exist in the index. The two tables are in sync. No fix needed here.

The data gap is the reverse: tenants are enquiring about buildings that have no listing record at all (section 2a phantom listings). Those are a sourcing gap, not a table error.

## 3. Quick Fixes vs Later

### Quick fixes (one line data edits, do now, no new listing required)

1. Add 5 keyword entries to TOWN_DISTRICT in `build_tenant_location_map.py`: `pine grove` D5, `city square` D8, `edgefield` D19, `mbs` plus `marina bay sands` D1, `haw par villa` D5. Re-run the build to remap 6 of the 8 unmapped tenants.
2. Merge the Dakshesh Gusain duplicate (TN206 and TN057) into one record.
3. Verify and activate `farrer-park` (D8) and `oxley-edge` (D9). Both are in your index on `hold` with null landlord records. Activating farrer-park alone gives D8 (13 waiting) its first real supply. These are verification tasks, not new sourcing.

### Later (needs new listings sourced this week)

1. **D5 Clementi or Dover room around 1500 to 1700**, ideally Pine Grove. Unlocks roughly 20 waiting tenants, the single largest queue.
2. **D8 Lavender or Jellicoe room around 1200**, or activate farrer-park. Unlocks roughly 13 waiting tenants.
3. **D23 Bukit Batok or Choa Chu Kang room around 1500**. Unlocks roughly 11 waiting tenants.
4. Optional higher value play: **one Treasure at Tampines whole unit at 3000 to 3300** to serve the 8 tenant whole unit queue that nothing else in your book reaches.
