# new-launch-reel skill review and improvements

Date: 2026-06-16
File edited: `.claude/skills/new-launch-reel/SKILL.md` (only file changed)
Quality bar referenced: `docs/content/lentor-gardens-*` and the test run `docs/content/thomson-reserve-*` (not edited).

The whole report obeys the absolute no dash rule (no hyphens, em dashes or en dashes) in prose. Dashes appear only in file paths, slugs and markdown structure, which are exempt.

## Part 1: prioritized feedback applied

1. **(HIGH) Mandatory enforcement gate in Phase 4.** Added a "Mandatory enforcement gate (run BEFORE saving)" block with three sweeps: a dash sweep, a CEA and claims sweep, and a figure sweep. The dash sweep requires actually scanning and fixing every hit in BOTH files before saving, not self attesting that it passed (the Thomson Reserve run had a Phase 4 that claimed "no hyphens PASS" while leaving the contradiction unresolved). Markdown structure, URLs and slugs are named exempt. The gate loops until clean, then saves.

2. **(HIGH) Internal hyphen contradiction resolved.** Rewrote the Golden rule to "No hyphens, em dashes or en dashes in human readable copy and headings", with an explicit EXEMPT list (URLs, source links, file slugs, file paths, markdown structure, literal code). Added the line "The rule governs PROSE, not plumbing." The skill requires source URLs which contain hyphens, so this removes the self contradiction the test run exposed.

3. **(MEDIUM) Conflicting facts handling.** Added a Golden rule: when sources disagree, state the lean value, show the competing value in parentheses, tag verify, and cite both sources. Gave a worked pattern ("District 20 (one guide says 11), verify. Sources: ..."). Banned silently dropping a figure or averaging into a made up middle. Phase 1 now points back to this rule for every disputed fact.

4. **(MEDIUM) Project selection.** Added a "Project selection procedure" under Inputs with: exclude any project that already has a `<slug>-viral-script.md` in `docs/content` (and names the two existing slugs, `lentor-gardens` and `thomson-reserve`); a recency tie breaker ranking by imminence first, then prominence; and a fallback that picks the most marketed upcoming launch and says so plainly when nothing is imminently balloting.

5. **(LOW) Phase 2 social intelligence.** Rewrote Phase 2 to demand the same evidentiary spine as Phase 1. Named concrete fallback sources: public YouTube creator titles and view counts (public, so quotable, unlike gated Instagram or TikTok metrics), EdgeProp and Stacked Homes launch coverage, and public TikTok discover and search. Required at least 2 named creators or videos analysed, with cited URLs.

Also handled the three side requests:
- **Hyphenated paths in the Step Z digest are fine.** Step Z now states the no dash rule applies to the digest sentences, but real file paths and slugs keep their dashes by design.
- **Where to record the chosen project rationale.** Phase 1 now requires a brief header (front matter or first paragraph) recording project, date, a confidence note, and the one line selection rationale when auto picked. Step Z echoes the rationale into the digest.
- **Verify checklist copied into the script file.** New "Self contained filming file" subsection in Phase 4 requires copying the verify before filming checklist into the top of the script, plus noting the companion brief path, so the filming day file stands alone.

## Part 2: additional improvements from my own why / what / how cycles

1. **Hook quality bar (why: the hook is the whole reel; the reference scripts had strong hooks but the skill never defined the standard).** Added a "Hook quality bar" in Phase 3: every hook and its 5 split test variants must create a curiosity gap, contrarian claim, specific number, or named pain in the first line; must work on mute via the on screen text; must pay off the promise (no clickbait); and must carry no guaranteed return, fake scarcity or unverified number stated as fact.

2. **Platform differences across Reels, TikTok and Shorts (why: the reference scripts said "post the same cut to all three", which leaves reach on the table).** Added a "Platform differences" block: film once vertical 9 by 16, then adapt. Reels leads with on screen text and uses bio link plus pinned comment because Reels blocks live caption links; TikTok wants a rawer faster hook and comment bait; Shorts rewards searchable framing with the project name in the first line because viewers arrive from search. Plus a rule to strip platform wording that does not apply to the cut being exported.

3. **Determinism and structure (why: the two reference scripts used different layouts and section labels; an unstable filming file is harder to use on set).** Added a "Determinism and structure" instruction: stable variant order, stable two column table (voiceover left, captions and shot direction right), each on screen number held large and alone, one idea per caption, same look every run.

4. **Three variants must be genuinely distinct (why: a real failure mode is three reworded versions of one idea).** Phase 3 now states the three variants must not be the same idea reworded; each needs its own promise and its own emotion.

5. **Fan out reconciliation closes the compliance loophole (why: a sub agent can reintroduce dashes or claims after the main agent's check).** The fan out section now requires running the Phase 4 enforcement gate on the merged result, because a sub agent's copy may bring back violations the gate must catch before saving.

6. **Figure tagging discipline made explicit in Phase 1 (why: consistent CONFIRMED vs VERIFY tagging is what makes the brief trustworthy and the script safe).** Phase 1 now instructs tagging every fact CONFIRMED with a source URL or VERIFY, matching the convention the Thomson Reserve brief used well.

## Part 3: residual suggestions (not applied, for a future pass)

1. **Programmatic dash linter.** The enforcement gate is a manual sweep. A tiny helper script (for example `scripts/dash-lint.mjs`) that flags em dashes, en dashes and prose hyphens while excluding URLs, code spans and markdown structure would make the gate deterministic and reviewable. I used exactly this logic to verify my own edits; packaging it would let the skill self check rather than self attest.

2. **Expiry and freshness metadata.** The Lentor brief used `generated_date`, `data_as_of` and `expires_date` front matter; the Thomson brief did not. Standardising an expiry field would let a future run detect a stale brief and flag a refresh, which matters because launch dates and PSF move fast. Worth folding into the Phase 1 brief header spec.

3. **Hashtag hygiene.** Hashtags are prose for the dash rule, but multi word hashtags are written closed (for example `#NewLaunchSG`). A one line note that hashtags must be closed compounds, never dashed, would prevent an edge case the dash sweep could otherwise trip on.

4. **CTA keyword consistency.** Each script picks a DM keyword (LENTOR, THOMSON). A note to derive the keyword deterministically from the project and keep it identical across all three variants and the pinned comment would tighten the funnel.

5. **Cross reference the cea-compliance-checker output.** The skill offers the checker as optional. Recording its verdict (pass or the fixes made) in the script header would create an audit trail, useful given CEA exposure.

Winfred Quek | CEA R073319H
