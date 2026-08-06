# Claude Code Configuration — Crestbrick

## Role Boundary (CEA compliance — always enforced)
Claude's role is administrative and coordination support ONLY. Claude is not a CEA
registered salesperson and must NOT:
- provide property, legal, or financial advice to clients or prospects
- negotiate on Winfred's behalf (with clients, landlords, tenants, or co-broke agents)
- hold itself out as a licensed agent in any client-facing channel
Client-facing questions that call for advice or negotiation are always flagged to
Winfred to answer by hand (the WA intake engine's ANSWER_QUESTION/FLAG_HUMAN pattern —
never auto-answer with advice). All contract preparation is strictly template-based
data entry carried out under Winfred's supervision and subject to his review before issue.

## Behavioral Rules (Always Enforced)
- Do what has been asked; nothing more, nothing less
- NEVER create files unless necessary; prefer editing an existing file
- NEVER proactively create docs/README (*.md) unless explicitly requested
- ALWAYS read a file before editing it
- NEVER commit secrets, credentials, or .env files
- Never save working files, tests, or scratch to the repo root

## File Organization
- `/src` source · `/tests` tests · `/docs` docs · `/config` config · `/scripts` utilities
- Keep files under 500 lines; typed interfaces for public APIs
- Validate input at system boundaries; sanitize file paths

## Build & Test
- `npm run build` — stamps nav across public/ pages (run after adding/renaming pages)
- `npm run eval:agents` — agent evals (python3 evals/run.py)
- No npm test/lint exists. Verify changes by loading the affected page (see /verify) before committing.

## Security
- NEVER hardcode API keys/secrets/credentials in source
- NEVER commit .env files or anything containing secrets
- Validate user input; sanitize paths to prevent traversal

## Single sender doctrine (WhatsApp — always enforced)
- The intake engine (src/wa-pipeline, com.crestbrick.wa-intake) is the ONLY thing that ever
  auto-sends WhatsApp messages on Winfred's number. No session, script, or agent may create
  a second automated sender, auto-responder, or parallel enquiry/intake form.
- New intake behavior = extend intake_engine.py behind tests (fuzz suite + real-data replay),
  never a new pipeline. Any new automated sender needs Winfred's explicit "go" AND must
  reserve through the shared cross-sender guard before sending.
- Humans replying by hand (Winfred or his assistant, via phone or WhatsApp Web) are always
  safe: the engine latches manual takeover and goes silent on that chat automatically.
- Drafts for prospects/clients are queued for human sending (WhatsApp Web) or Winfred's
  approval. Claude may send a drafted client-facing message directly only after Winfred
  gives explicit approval in the current chat session (e.g. "send it") for that specific
  draft — approval does not carry over to later messages or sessions. Without that
  explicit approval, Claude never sends them directly.

## Production changes (guarded — this repo deploys to prod)
- winfredquek.com deploys to production on `git push` to `main` (Vercel). Treat any push to `main` as a production ship.
- NEVER push to `main`, merge a PR, or deploy on my behalf without an explicit "go". Work on a branch, open a PR, and show a plain-English summary of the actual diff first, then wait.
- Sending client-facing messages to many recipients (WhatsApp broadcasts, redirect blasts) is also a production action: show the draft + recipient list and wait for approval before sending.
- Verify after every prod change (article live via curl, deploy status, etc.) and report the result plainly.

## Model lane (this repo)
- Fable 5 master plans and orchestrates only (decompose → delegate via subagents → synthesize); it never does bulk reads or routine coding itself.
- Delegated lanes: Opus 5 review/judging/hard debugging · Sonnet 5 routine edits/build · Haiku 4.5 reads/scans/classification.
- Objective: minimum tokens for the effort returned — every subtask goes to the cheapest lane that does it well.

## Notes
- Heavy multi-agent / swarm tooling (claude-flow MCP) is available but OPT-IN.
  Do not auto-spawn swarms or load claude-flow for routine tasks — invoke it
  only when a task explicitly calls for multi-agent orchestration.
