-- 001_init.sql — initial Postgres schema for Crestbrick client DB
-- Apply: psql "$DATABASE_URL" -f db/migrations/001_init.sql
-- Idempotent: uses IF NOT EXISTS / DO blocks. Safe to re-run.

\i ../schema.sql
