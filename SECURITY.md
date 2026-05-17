# Security

## Gitleaks pre-commit hook

This repo uses [gitleaks](https://github.com/gitleaks/gitleaks) as a Git
`pre-commit` hook to prevent secrets (API keys, tokens, private keys, etc.)
from being committed. The hook lives at `.git/hooks/pre-commit` and runs:

```bash
gitleaks protect --staged --redact --verbose --no-banner
```

`--redact` ensures the actual secret value is never echoed to your terminal
or shell history; only the rule, file, line, and fingerprint are shown.

If gitleaks finds a match, the commit is aborted with a non-zero exit code.

### Setup on a new clone

The hook lives inside `.git/`, which is not version-controlled, so it must
be re-installed per clone:

1. Install gitleaks: `brew install gitleaks`
2. Copy the hook from a clone that already has it, or recreate it with the
   contents documented in this file's git history.
3. `chmod +x .git/hooks/pre-commit`

### What it catches

Gitleaks ships with rules for AWS keys, GCP keys, Slack tokens, GitHub PATs,
Stripe keys, generic high-entropy strings, RSA/SSH private keys, and many
more. Run `gitleaks detect --no-banner` against the working tree to scan
the full history.

### Updating

```bash
brew upgrade gitleaks
```

Run periodic full-history scans:

```bash
gitleaks detect --no-banner --redact
```

### Handling false positives

If a finding is a confirmed false positive (for example, a placeholder
string in a test fixture), add its fingerprint to `.gitleaksignore` at the
repo root. The fingerprint format is shown in the gitleaks output, e.g.:

```
path/to/file.ts:generic-api-key:42
```

Add a comment next to each entry explaining why it is safe.

### Bypassing the hook (discouraged)

`git commit --no-verify` will skip the hook entirely. Do this only after
manually verifying that the staged diff contains no real secret. Bypassing
once and pushing a real key forces an immediate rotation across every
downstream consumer — far more painful than fixing the false positive
properly via `.gitleaksignore`.

### Incident: a real secret was committed

1. Rotate the credential immediately at its source (do not wait for
   history rewrite).
2. Remove the secret from history with `git filter-repo` or BFG, then
   force-push (only on a branch you control, never directly to `main`
   without coordinating).
3. Update every downstream consumer (env files, MCP configs, deployed
   services) with the new credential.
4. Document the incident at
   `~/real-estate-agent/security/incidents/[YYYY-MM-DD-slug].md`.

## Reporting a vulnerability

Email winfredquekoc@gmail.com. Do not open a public GitHub issue for
security problems.
