# Crestbrick client database — Postgres (Neon) migration

This directory holds the canonical Postgres schema for the Winfred Quek /
Crestbrick client CRM, currently mirrored from `~/.claude/state/clients.db`
(SQLite, ~107 jobs depend on it).

## Why migrate

- SQLite lives on a single laptop; Vercel functions and other agents need
  network access to the same canonical store.
- Real client data is in markdown at `~/real-estate-agent/clients/`. The
  SQLite DB has rich schema but currently 0 rows. Postgres lets us ingest
  the markdown and keep both stores in sync via a dual-write window.

## Provisioning (one-time, manual via Vercel UI)

Neon is provisioned through Vercel Marketplace so billing rolls into the
existing Vercel project.

1. Vercel dashboard → project `crestbrick-consult` → **Storage** tab.
2. **Create Database** → **Marketplace** → **Neon** → Free tier, region
   `Singapore (ap-southeast-1)` (closest to local agents and us-clients).
3. Name: `crestbrick-clients`. Branch: `main`.
4. Connect to project → Vercel injects these env vars automatically into
   Preview + Production:
   - `DATABASE_URL` (pooled connection — use this for serverless)
   - `DATABASE_URL_UNPOOLED` (direct — use for migrations)
   - `PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`
5. Pull env to local: `vercel env pull .env.local`

## Apply the schema

```bash
# from repo root, with DATABASE_URL_UNPOOLED in env
psql "$DATABASE_URL_UNPOOLED" -f db/migrations/001_init.sql

# verify
psql "$DATABASE_URL_UNPOOLED" -c "\dt"
psql "$DATABASE_URL_UNPOOLED" -c "\dT"   # confirm enum types
```

## Ingest existing markdown clients

```bash
# 1. Generate CSV (DRY-RUN; no DB writes)
python3 scripts/ingest-markdown-clients.py
# → /tmp/client-ingest.csv

# 2. Inspect before loading
head /tmp/client-ingest.csv

# 3. Load into Postgres
psql "$DATABASE_URL_UNPOOLED" <<'SQL'
\copy clients(slug,display_name,phone,email,citizenship,icp_bucket,referral_source,stage,lead_temperature,lead_source,first_contact_at) FROM '/tmp/client-ingest.csv' WITH (FORMAT csv, HEADER true)
SQL
```

Rows that fail enum validation will reject the entire `\copy`. Fix the CSV
and re-run — the ingest script is idempotent (dedupes on E.164 phone).

## Dual-write strategy (transition period)

Until all 107 automation jobs are pointed at Postgres, run **dual-write**:

1. **Authoritative reads from Postgres.** Vercel + agents read here.
2. **Writes go to BOTH stores** through a thin wrapper:
   - `~/.claude/bin/client-write.sh` (to be built) does
     `INSERT ... ON CONFLICT ...` against Postgres, then mirrors to local
     SQLite via the existing scripts.
   - On any divergence, Postgres wins. SQLite re-syncs nightly via cron.
3. **Cutover** when the last cron job has been migrated. Then SQLite is
   demoted to read-only backup, removed from the write path entirely.

Rollback: keep SQLite live for 30 days post-cutover. If Postgres has an
incident, point env to a local SQLite-backed shim and continue.

## Schema source of truth

- `db/schema.sql` — canonical DDL (this is what gets reviewed in PRs)
- `db/migrations/NNN_*.sql` — sequential applied migrations; never edit
  a migration after it has been run against prod. Add new ones.

## Files

| Path | Purpose |
|---|---|
| `db/schema.sql` | Canonical Postgres DDL (idempotent) |
| `db/migrations/001_init.sql` | First migration; sources `schema.sql` |
| `scripts/ingest-markdown-clients.py` | Markdown → CSV (DRY-RUN by default) |
| `db/README.md` | This file |
