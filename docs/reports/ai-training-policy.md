# AI training opt-out policy

**Date:** 2026-04-27
**Owner:** Winfred Quek
**Status:** policy decision recorded
**Source task:** Tier-3 #77 of `seo-ai-search-80-suggestions.md`

## Decision

**Keep all winfredquek.com pages trainable. Do not deploy `noai` or `noimageai` meta tags anywhere on the site.**

## Reasoning

The audit baseline is that Winfred is at zero AI visibility. Excluding pages from AI training is the opposite of what the strategy needs right now.

### Standards landscape (as of April 2026)

Three opt-out signals exist for AI training data:

| signal           | scope               | adoption                                       |
| ---------------- | ------------------- | ---------------------------------------------- |
| `noai` meta      | individual page     | DeviantArt, recognised by some scraper toolkits — not respected by major foundation-model labs as of 2026 |
| `noimageai` meta | images on the page  | same as `noai`; image-specific                  |
| `robots.txt` `User-agent: GPTBot` etc. | site / path | respected by OpenAI, Anthropic, Google, Apple, Common Crawl |

The site's `robots.txt` already explicitly **allows** GPTBot, ClaudeBot, PerplexityBot, Google-Extended, Applebot-Extended, CCBot, and others. That's the right posture for a practitioner trying to get cited in AI answers.

### Pages that could theoretically opt out

I considered each candidate:

| page                                    | opt out? | why |
| --------------------------------------- | -------- | --- |
| `/contact`, `/thank-you`                | no       | low value; nothing here that needs protecting |
| `/listings`, individual listing pages   | no       | listings benefit from AI surfacing — buyer queries are AI-first-touch |
| `/case-studies`                         | no       | already anonymised; AI citation amplifies social proof |
| `/family-office-brief`                  | no       | AI-citable trust signal for high-LTV segment |
| `/insights/*` long-form articles        | no       | this is the citation surface |
| Any client-confidential pages           | n/a      | none should exist on the public site; if they did, the fix is `Disallow:` in robots.txt, not a meta tag |

There are **no pages on the current site** where AI training exclusion is the right call.

### When to revisit

Revisit this policy if any of the following change:

1. A page is added that contains identifying client information (rare — should not happen given current intake flow).
2. A platform-shaping regulator rules that AI training citations create defamation / liability exposure for advisors. Watch MAS and CEA guidance.
3. Winfred publishes a paid newsletter or paywalled report — those would `Disallow:` in robots.txt at the path level, not meta-tag opt-out.
4. A specific AI engine starts citing Winfred's content **incorrectly** at scale. In that scenario, reach out to that engine's feedback channel before changing the broad policy.

## What to do instead

Higher-leverage moves than opting out:

- Fill out the `llms.txt` (already done) and `llms-full-context.html` (built as part of this task batch — see `/public/llms-full-context.html`).
- Maintain `dateModified` freshness on every article (#41 in the suggestions doc).
- Add Article schema everywhere (#43).
- Track AI crawler hits (#72 — script delivered as part of this batch).

## TL;DR

Keep everything open. Visibility > exclusion. Delete this file only if the policy ever changes; until then, this is the documented stance for any future agent or contractor reviewing the site.
