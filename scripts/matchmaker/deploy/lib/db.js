// Postgres access for the Matchmaker CRM layer.
//
// Deliberately driver-portable: plain `pg` against DATABASE_URL, so the same code
// runs on Neon, Supabase, or Vercel Postgres. Whichever gets provisioned, use the
// POOLED/pgbouncer connection string — a serverless function opens a connection per
// cold start and a direct (unpooled) endpoint runs out of slots under any real use.
//
// When DATABASE_URL is unset the API answers 501 rather than throwing. That is not a
// degenerate case: it is the app's supported local mode, where the client keeps every
// CRM write in localStorage exactly as it did before the backend existed.
import pg from "pg";

const URL_ = process.env.DATABASE_URL || "";
export const configured = !!URL_;

let pool = null;
export function db() {
  if (!configured) throw new Error("DATABASE_URL is not set");
  if (!pool) {
    pool = new pg.Pool({
      connectionString: URL_,
      // Managed Postgres providers all terminate TLS with their own chain. Verifying it
      // from a lambda needs the provider CA bundled, which breaks the moment the provider
      // changes — and the connection string itself is the secret here.
      ssl: { rejectUnauthorized: false },
      max: 1,                       // one socket per lambda instance, not per request
      idleTimeoutMillis: 10_000,
      connectionTimeoutMillis: 8_000,
    });
    pool.on("error", () => {});     // a dropped idle socket must not kill the process
  }
  return pool;
}

const SCHEMA = `
create table if not exists crm_entity (
  key           text primary key,
  kind          text not null,
  ref_id        text,
  name          text,
  phone         text,
  stage         text not null default 'new',
  next_action   text,
  next_due      date,
  flagged       boolean not null default false,
  contacted_on  date,
  archived      boolean not null default false,
  updated_at    timestamptz not null default now()
);
create index if not exists crm_entity_phone_idx on crm_entity(phone);
create index if not exists crm_entity_due_idx   on crm_entity(next_due) where next_due is not null;

create table if not exists crm_note (
  id         bigserial primary key,
  key        text not null references crm_entity(key) on delete cascade,
  body       text not null,
  created_at timestamptz not null default now()
);
create index if not exists crm_note_key_idx on crm_note(key, created_at desc);

create table if not exists crm_task (
  id         bigserial primary key,
  key        text references crm_entity(key) on delete set null,
  title      text not null,
  due        date,
  done       boolean not null default false,
  done_at    timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists crm_task_open_idx on crm_task(due) where done = false;

create table if not exists crm_match_status (
  listing_id text not null,
  tenant_id  text not null,
  status     text not null,
  updated_at timestamptz not null default now(),
  primary key (listing_id, tenant_id)
);

create table if not exists crm_activity (
  id     bigserial primary key,
  key    text,
  verb   text not null,
  detail text,
  at     timestamptz not null default now()
);
create index if not exists crm_activity_at_idx  on crm_activity(at desc);
create index if not exists crm_activity_key_idx on crm_activity(key, at desc);
`;

// Idempotent, and run at most once per lambda instance rather than per request.
// The promise is cached (not a boolean) so concurrent first requests await the same
// migration instead of racing three CREATE TABLE statements against each other.
let ready = null;
export function ensureSchema() {
  if (!ready) {
    ready = db().query(SCHEMA).catch((e) => { ready = null; throw e; });
  }
  return ready;
}
