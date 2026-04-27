-- Crestbrick / Winfred Quek client database — Postgres (Neon) schema
-- Translated from ~/.claude/state/clients.db (SQLite) on 2026-04-28.
-- This file is the canonical schema. Migrations under db/migrations/ apply
-- it incrementally. Apply via: psql "$DATABASE_URL" -f db/migrations/001_init.sql

-- ============================================================
-- ENUM types (replace SQLite CHECK constraints)
-- ============================================================
DO $$ BEGIN
  CREATE TYPE citizenship_t      AS ENUM ('SC','PR','Foreigner');
  CREATE TYPE icp_bucket_t       AS ENUM ('hdb_upgrader','investor','decoupler','family_office','expat','seller','not_fit');
  CREATE TYPE client_stage_t     AS ENUM ('lead','qualified','engaged','transacting','closed','nurture','lost');
  CREATE TYPE channel_t          AS ENUM ('whatsapp','telegram','email','call','meeting','dm','form','other');
  CREATE TYPE direction_t        AS ENUM ('inbound','outbound');
  CREATE TYPE engagement_t       AS ENUM ('opened','replied','ignored','bounced','booked','unknown');
  CREATE TYPE life_event_t       AS ENUM ('birthday','purchase_anniversary','mop_reached','lease_expiry','marriage','child_born','job_change','other');
  CREATE TYPE property_kind_t    AS ENUM ('HDB','EC','condo','landed','commercial','industrial');
  CREATE TYPE property_status_t  AS ENUM ('owned','sold','under_contract','rented');
  CREATE TYPE deal_type_t        AS ENUM ('buy','sell','decouple','rent_out','upgrade','new_launch');
  CREATE TYPE deal_stage_t       AS ENUM ('otp','exercise','completion','keys','closed','dead');
  CREATE TYPE offer_side_t       AS ENUM ('buy','sell');
  CREATE TYPE offer_outcome_t    AS ENUM ('accepted','rejected','countered','withdrawn','expired','pending');
  CREATE TYPE postmortem_cause_t AS ENUM (
    'financing_failed','price_disagreement','client_pulled',
    'cooling_measure','seller_pulled','timing_mismatch',
    'better_alternative_found','co_broke_breakdown','other'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================================
-- clients
-- ============================================================
CREATE TABLE IF NOT EXISTS clients (
  slug                    TEXT PRIMARY KEY,
  display_name            TEXT NOT NULL,
  phone                   TEXT,                 -- E.164 (+countrycode...)
  email                   TEXT,
  citizenship             citizenship_t,
  icp_bucket              icp_bucket_t,
  referral_source         TEXT,
  referred_by_slug        TEXT REFERENCES clients(slug),
  stage                   client_stage_t NOT NULL DEFAULT 'lead',
  health_score            INTEGER,
  first_contact_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_contact_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_stage_change_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ipa_date                DATE,
  ipa_bank                TEXT,
  ipa_amount              INTEGER,
  next_followup_date      DATE,
  household_income        INTEGER,
  is_first_timer          BOOLEAN DEFAULT TRUE,
  lead_score              INTEGER DEFAULT 0,
  score_label             TEXT DEFAULT 'warm',
  score_updated_at        TIMESTAMPTZ,
  preferred_districts     TEXT,
  max_budget_m            NUMERIC(10,3),
  nps_score               INTEGER,
  nps_requested_at        TIMESTAMPTZ,
  marital_status          TEXT,
  ninety9to1_discussed    BOOLEAN DEFAULT FALSE,
  bto_application         TEXT,
  bto_result_date         DATE,
  bto_project             TEXT,
  onboarding_step         INTEGER DEFAULT 0,
  lead_temperature        TEXT DEFAULT 'Cold',
  temperature_updated_at  TIMESTAMPTZ,
  search_criteria         TEXT,
  date_of_birth           DATE,
  aip_expiry_date         DATE,
  income_monthly          INTEGER,
  aip_amount              INTEGER DEFAULT 0,
  lead_source             TEXT DEFAULT 'Unknown',
  referred_by             TEXT,
  cash_savings            INTEGER,
  cpf_oa_balance          INTEGER,
  target_budget           INTEGER,
  aip_bank                TEXT,
  sale_prep_sent          BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_clients_stage          ON clients(stage);
CREATE INDEX IF NOT EXISTS idx_clients_last_contact   ON clients(last_contact_at);
CREATE INDEX IF NOT EXISTS idx_clients_icp            ON clients(icp_bucket);
CREATE INDEX IF NOT EXISTS idx_clients_referred       ON clients(referred_by_slug);
-- new search indexes
CREATE INDEX IF NOT EXISTS idx_clients_phone_e164     ON clients(phone)        WHERE phone IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_clients_email_lower    ON clients(LOWER(email)) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_clients_name_lower     ON clients(LOWER(display_name));

-- ============================================================
-- stage_transitions
-- ============================================================
CREATE TABLE IF NOT EXISTS stage_transitions (
  id               BIGSERIAL PRIMARY KEY,
  client_slug      TEXT NOT NULL REFERENCES clients(slug) ON DELETE CASCADE,
  from_stage       client_stage_t,
  to_stage         client_stage_t NOT NULL,
  transitioned_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  transitioned_by  TEXT,
  reason           TEXT
);
CREATE INDEX IF NOT EXISTS idx_transitions_client ON stage_transitions(client_slug);

-- ============================================================
-- touchpoints
-- ============================================================
CREATE TABLE IF NOT EXISTS touchpoints (
  id                BIGSERIAL PRIMARY KEY,
  client_slug       TEXT NOT NULL REFERENCES clients(slug) ON DELETE CASCADE,
  channel           channel_t,
  direction         direction_t,
  content_summary   TEXT,
  engagement        engagement_t,
  occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  agent_source      TEXT,
  touchpoint_type   TEXT
);
CREATE INDEX IF NOT EXISTS idx_touch_client     ON touchpoints(client_slug, occurred_at);
CREATE INDEX IF NOT EXISTS idx_touch_engagement ON touchpoints(engagement);

-- ============================================================
-- life_events
-- ============================================================
CREATE TABLE IF NOT EXISTS life_events (
  id           BIGSERIAL PRIMARY KEY,
  client_slug  TEXT NOT NULL REFERENCES clients(slug) ON DELETE CASCADE,
  event_type   life_event_t,
  event_date   DATE NOT NULL,
  annual       BOOLEAN NOT NULL DEFAULT TRUE,
  note         TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_life_client ON life_events(client_slug);
CREATE INDEX IF NOT EXISTS idx_life_date   ON life_events(event_date);

-- ============================================================
-- properties
-- ============================================================
CREATE TABLE IF NOT EXISTS properties (
  id                       BIGSERIAL PRIMARY KEY,
  client_slug              TEXT NOT NULL REFERENCES clients(slug) ON DELETE CASCADE,
  kind                     property_kind_t,
  address                  TEXT,
  acquired_year            INTEGER,
  purchase_price           INTEGER,
  outstanding_loan         INTEGER,
  cpf_used                 INTEGER,
  estimated_value          INTEGER,
  estimated_value_at       TIMESTAMPTZ,
  status                   property_status_t,
  lock_in_expiry           DATE,
  mortgage_bank            TEXT,
  fixed_rate               NUMERIC(5,3),
  expected_monthly_rent    INTEGER DEFAULT 0,
  actual_monthly_rent      INTEGER DEFAULT 0,
  is_rented                BOOLEAN DEFAULT FALSE,
  loan_disbursement_date   DATE,
  loan_lock_in_years       INTEGER DEFAULT 2,
  repricing_window_date    DATE,
  mop_date                 DATE,
  property_type            TEXT,
  tenancy_end_date         DATE,
  rental_followup_sent     BOOLEAN DEFAULT FALSE,
  mop_estimated            BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_properties_client ON properties(client_slug);

-- ============================================================
-- deals
-- ============================================================
CREATE TABLE IF NOT EXISTS deals (
  id                            BIGSERIAL PRIMARY KEY,
  client_slug                   TEXT NOT NULL REFERENCES clients(slug) ON DELETE CASCADE,
  deal_type                     deal_type_t,
  property_address              TEXT,
  price                         INTEGER,
  commission_gross              INTEGER,
  commission_net                INTEGER,
  cobroke_agent                 TEXT,
  cobroke_split_pct             INTEGER,
  stage                         deal_stage_t,
  otp_date                      DATE,
  completion_date               DATE,
  notes                         TEXT,
  created_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  absd_remission_purchase_date  DATE,
  absd_disposal_deadline        DATE,
  option_fee_paid               INTEGER DEFAULT 0,
  exercise_deposit_paid         INTEGER DEFAULT 0,
  exercise_date                 DATE,
  caveat_lodged                 BOOLEAN DEFAULT FALSE,
  absd_remission_active         BOOLEAN DEFAULT FALSE,
  absd_old_property_sold        BOOLEAN DEFAULT FALSE,
  expected_completion_date      DATE,
  absd_remission_applicable     BOOLEAN DEFAULT FALSE,
  top_date                      DATE,
  lease_up_advised_at           TIMESTAMPTZ,
  payment_scheme                TEXT,
  pps_next_milestone            TEXT,
  pps_next_amount               INTEGER,
  pps_milestone_date            DATE,
  testimonial_sent              BOOLEAN DEFAULT FALSE,
  key_collection_date           DATE,
  post_move_checklist_sent      BOOLEAN DEFAULT FALSE,
  punch_list_briefed            BOOLEAN DEFAULT FALSE,
  open_house_date               DATE,
  otp_granted_date              DATE,
  otp_exercise_deadline         DATE,
  otp_exercised_date            DATE,
  cobroke_split_confirmed       BOOLEAN DEFAULT FALSE,
  cobroke_paid                  BOOLEAN DEFAULT FALSE,
  finance_approved              BOOLEAN DEFAULT FALSE,
  spa_signed                    BOOLEAN DEFAULT FALSE,
  cobroke_amount                INTEGER DEFAULT 0,
  cobroke_due_date              DATE,
  closed_date                   DATE,
  co_broke_agent                TEXT,
  co_broke_split                TEXT,
  co_broke_confirmed            BOOLEAN DEFAULT FALSE,
  stage_updated_at              TIMESTAMPTZ,
  commission_received           INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_deals_client ON deals(client_slug);
CREATE INDEX IF NOT EXISTS idx_deals_stage  ON deals(stage);

-- ============================================================
-- satisfaction
-- ============================================================
CREATE TABLE IF NOT EXISTS satisfaction (
  id           BIGSERIAL PRIMARY KEY,
  client_slug  TEXT NOT NULL REFERENCES clients(slug) ON DELETE CASCADE,
  score        INTEGER CHECK (score BETWEEN 0 AND 10),
  surveyed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  context      TEXT
);

-- ============================================================
-- offer_history
-- ============================================================
CREATE TABLE IF NOT EXISTS offer_history (
  id                BIGSERIAL PRIMARY KEY,
  client_slug       TEXT REFERENCES clients(slug),
  deal_id           BIGINT REFERENCES deals(id),
  property_address  TEXT NOT NULL,
  side              offer_side_t NOT NULL,
  offer_amount      INTEGER NOT NULL,
  asking_amount     INTEGER,
  counter_amount    INTEGER,
  outcome           offer_outcome_t,
  offered_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  responded_at      TIMESTAMPTZ,
  notes             TEXT
);
CREATE INDEX IF NOT EXISTS idx_offer_property ON offer_history(property_address);
CREATE INDEX IF NOT EXISTS idx_offer_client   ON offer_history(client_slug);

-- ============================================================
-- deal_postmortems
-- ============================================================
CREATE TABLE IF NOT EXISTS deal_postmortems (
  id               BIGSERIAL PRIMARY KEY,
  deal_id          BIGINT REFERENCES deals(id),
  client_slug      TEXT REFERENCES clients(slug),
  died_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  cause            postmortem_cause_t,
  what_we_learned  TEXT NOT NULL,
  what_we_change   TEXT,
  recoverable      BOOLEAN DEFAULT FALSE,
  notes            TEXT,
  prompted_at      TIMESTAMPTZ
);

-- ============================================================
-- property_watchlist
-- ============================================================
CREATE TABLE IF NOT EXISTS property_watchlist (
  id            BIGSERIAL PRIMARY KEY,
  client_slug   TEXT REFERENCES clients(slug),
  project_name  TEXT,
  target_psf    INTEGER,
  target_price  INTEGER,
  added_date    DATE
);

-- ============================================================
-- Triggers (replace SQLite triggers)
-- ============================================================
CREATE OR REPLACE FUNCTION fn_clients_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_clients_updated_at ON clients;
CREATE TRIGGER trg_clients_updated_at
BEFORE UPDATE ON clients
FOR EACH ROW EXECUTE FUNCTION fn_clients_updated_at();

CREATE OR REPLACE FUNCTION fn_clients_stage_change() RETURNS TRIGGER AS $$
BEGIN
  IF OLD.stage IS DISTINCT FROM NEW.stage THEN
    INSERT INTO stage_transitions (client_slug, from_stage, to_stage)
    VALUES (NEW.slug, OLD.stage, NEW.stage);
    NEW.last_stage_change_at := NOW();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_clients_stage_change ON clients;
CREATE TRIGGER trg_clients_stage_change
BEFORE UPDATE OF stage ON clients
FOR EACH ROW EXECUTE FUNCTION fn_clients_stage_change();

-- ============================================================
-- Views
-- ============================================================
CREATE OR REPLACE VIEW v_silent_clients AS
SELECT slug, display_name, stage, last_contact_at,
       EXTRACT(DAY FROM (NOW() - last_contact_at))::INTEGER AS days_silent
FROM clients
WHERE stage IN ('qualified','engaged','nurture')
  AND NOW() - last_contact_at > INTERVAL '30 days'
ORDER BY days_silent DESC;

CREATE OR REPLACE VIEW v_family_office_clients AS
SELECT * FROM clients WHERE icp_bucket = 'family_office';

CREATE OR REPLACE VIEW v_referral_tree AS
SELECT
  c.slug, c.display_name,
  r.display_name AS referred_by,
  COUNT(c2.slug) AS referred_count
FROM clients c
LEFT JOIN clients r  ON c.referred_by_slug = r.slug
LEFT JOIN clients c2 ON c2.referred_by_slug = c.slug
GROUP BY c.slug, c.display_name, r.display_name;

CREATE OR REPLACE VIEW v_commission_attribution AS
SELECT
  d.id AS deal_id,
  d.client_slug,
  c.display_name,
  d.deal_type,
  d.property_address,
  d.price,
  d.commission_gross,
  d.commission_net,
  d.cobroke_agent,
  d.cobroke_split_pct,
  CASE
    WHEN d.cobroke_agent IS NOT NULL THEN
      d.commission_gross * (100 - COALESCE(d.cobroke_split_pct, 50)) / 100
    ELSE d.commission_gross
  END AS crestbrick_share,
  d.completion_date,
  c.referral_source,
  c.referred_by_slug
FROM deals d JOIN clients c ON c.slug = d.client_slug
WHERE d.stage IN ('completion','keys','closed');

CREATE OR REPLACE VIEW v_quarterly_commission AS
SELECT
  TO_CHAR(completion_date, 'YYYY"-Q"Q') AS quarter,
  COUNT(*)                AS deals,
  SUM(crestbrick_share)   AS total_commission,
  AVG(crestbrick_share)   AS avg_commission
FROM v_commission_attribution
WHERE completion_date IS NOT NULL
GROUP BY quarter
ORDER BY quarter DESC;
