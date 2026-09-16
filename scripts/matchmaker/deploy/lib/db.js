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

// DATABASE_URL is the project's own convention (see api/crm.js's header comment), but
// Vercel's Neon integration injects POSTGRES_URL instead, and some Prisma flavoured
// setups only give POSTGRES_PRISMA_URL — accept whichever one is actually there rather
// than making Winfred rename an env var Vercel set for him.
const URL_ = process.env.DATABASE_URL || process.env.POSTGRES_URL || process.env.POSTGRES_PRISMA_URL || "";
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

-- Deals block of the CRM tab. id is client generated (same pattern as a note/task
-- tempId) so the client can upsert without a round trip; "on delete set null" means
-- a deal survives its linked contact ever being purged, it just loses the link.
create table if not exists crm_deal (
  id                 text primary key,
  key                text references crm_entity(key) on delete set null,
  deal_type          text check (deal_type in ('rental','sale')),
  property           text,
  price              numeric,
  commission_gross   numeric,
  commission_net     numeric,
  cobroke_agent      text,
  cobroke_split_pct  numeric,
  stage              text not null default 'agreed'
                       check (stage in ('agreed','otp','signed','completed','fell_through')),
  otp_date           date,
  completion_date    date,
  notes              text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create index if not exists crm_deal_key_idx   on crm_deal(key);
create index if not exists crm_deal_stage_idx on crm_deal(stage);

-- Added after crm_deal's first ship — "add column if not exists" (not baked into the
-- create table above) so a deployment that already has crm_deal without this column
-- picks it up idempotently instead of needing a one off migration step. This is the
-- date month/year to date totals bucket by (see dealTotals() in app.js) — deliberately
-- separate from completion_date/otp_date, which track the property transaction itself,
-- not when Winfred wants the deal counted.
alter table crm_deal add column if not exists deal_date date;
create index if not exists crm_deal_date_idx on crm_deal(deal_date);

-- CRM pull bridge (Sep 2026). One row per drafted WhatsApp message the app has
-- marked ready to send. Nothing here ever sends anything: crm_pull.py (run on
-- Winfred's own Mac, never on Vercel) is the only thing that reads a queued row,
-- runs it through queue_drafts.py's own checks, and appends it to the real
-- morning dispatch queue file. id is client generated the same way crm_deal.id
-- is, so the app can upsert without a round trip. status starts queued, moves to
-- pulled once crm_pull.py has taken it, sent once the morning dispatch job has
-- actually sent it (nothing in this repo sets that today), or cancelled when a
-- check refuses it or the app unqueues the row.
create table if not exists crm_dispatch (
  id            text primary key,
  tenant_id     text,
  listing_id    text,
  jid           text,
  phone         text,
  text          text,
  viewing_slot  text,
  status        text not null default 'queued'
                  check (status in ('queued','pulled','sent','cancelled')),
  created_at    timestamptz not null default now(),
  pulled_at     timestamptz,
  device        text
);
create index if not exists crm_dispatch_status_idx on crm_dispatch(status);
create index if not exists crm_dispatch_tenant_idx on crm_dispatch(tenant_id);

-- Ambiguous row resolution (PR #132 fifth review). A pulled row crm_pull.py
-- could not confidently resolve from the archive (an archive stamp too close
-- to pulled_at to trust, or the row sitting in both the live queue file and
-- an archive at once — see crm_pull.py's own docstring) is left status
-- 'pulled' but gets this stamped once, via the "ambiguous" op, so it is not
-- a permanent dead end: the app's dispatch drawer surfaces it to Winfred as
-- a Sent / Not sent check instead of crm_pull.py re running the same
-- inconclusive check forever. nextDispatchStatus's transition table is
-- unchanged by this — the "ambiguous" op never writes crm_dispatch.status.
alter table crm_dispatch add column if not exists ambiguous_since timestamptz;

-- Operator confirmation (PR #132 sixth review). Set once, true forever, when
-- Winfred picks Sent on an ambiguous row in the dispatch drawer (the same
-- "dispatch" op that moves the row to status 'sent' — see resolveAmbiguousSent
-- in app.js). A plain crm_pull.py sent guess (the archive based check above)
-- never sets this — only an operator's own click does. Lets crm_pull.py's own
-- cleanup pass (cleanup_resolved) prune that row's queue item exactly like a
-- cancelled one, since an operator confirmed send is just as terminal as a
-- cancel from the queue's own point of view.
alter table crm_dispatch add column if not exists sent_confirmed boolean not null default false;
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
