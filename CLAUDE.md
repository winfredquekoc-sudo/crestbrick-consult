# Claude Code Configuration — Crestbrick

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

## Production changes (guarded — this repo deploys to prod)
- winfredquek.com deploys to production on `git push` to `main` (Vercel). Treat any push to `main` as a production ship.
- NEVER push to `main`, merge a PR, or deploy on my behalf without an explicit "go". Work on a branch, open a PR, and show a plain-English summary of the actual diff first, then wait.
- Sending client-facing messages to many recipients (WhatsApp broadcasts, redirect blasts) is also a production action: show the draft + recipient list and wait for approval before sending.
- Verify after every prod change (article live via curl, deploy status, etc.) and report the result plainly.

## Model lane (this repo)
- Routine edits/build: Sonnet 5. Planning/review/judging: Opus 4.8. Top-tier (Fable 5) only for architecture, production debugging, security review, or migrations — never routine coding.

## Notes
- Heavy multi-agent / swarm tooling (claude-flow MCP) is available but OPT-IN.
  Do not auto-spawn swarms or load claude-flow for routine tasks — invoke it
  only when a task explicitly calls for multi-agent orchestration.
