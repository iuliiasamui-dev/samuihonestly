-- ---------------------------------------------------------------------------
-- samuihonestly — D1 raw layer (Phase 3)
-- ---------------------------------------------------------------------------
-- This is the RAW layer: append-only, never updated, never cleaned in place.
-- Nothing here is modelled. Cleaning, typing and business logic all happen
-- later in dbt, against a copy of this data. If a rule turns out to be wrong,
-- the raw table still holds what actually arrived.
--
-- Apply with:
--   wrangler d1 execute samuihonestly-events --remote --file=worker/schema.sql
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_events (
  -- Idempotency key. Generated in the browser, so a retried beacon or a
  -- re-sent batch collapses into one row instead of double-counting.
  event_id      TEXT PRIMARY KEY,

  -- Two clocks, deliberately. received_at is the server's and is trustworthy;
  -- occurred_at is the visitor's device clock and is not. Keeping both is how
  -- you later measure late-arriving events instead of guessing about them.
  received_at   TEXT NOT NULL,
  occurred_at   TEXT,

  -- Extraction watermark. The nightly job pulls WHERE ingest_day >= last_run.
  ingest_day    TEXT NOT NULL,

  event_name    TEXT NOT NULL,

  -- The identity chain: visitor (persists) -> session (30-min idle window).
  -- Stitching these to an email subscriber is the interesting problem later.
  visitor_id    TEXT,
  session_id    TEXT,

  page_path     TEXT,

  -- Attribution, frozen at session start rather than read per event, so a
  -- mid-session navigation cannot overwrite where the visitor came from.
  landing_path  TEXT,
  referrer      TEXT,
  utm_source    TEXT,
  utm_medium    TEXT,
  utm_campaign  TEXT,
  utm_content   TEXT,   -- put the TikTok video id here
  utm_term      TEXT,

  -- Server-side enrichment. Country comes from Cloudflare's edge.
  -- No IP address and no user-agent string is stored: neither is needed for
  -- any question being asked, and storing them raises the GDPR stakes sharply.
  country       TEXT,
  device_type   TEXT,

  -- Event-specific fields as JSON. Anything that is not common to every event
  -- lives here rather than becoming a mostly-null column.
  props         TEXT
);

CREATE INDEX IF NOT EXISTS idx_raw_events_ingest_day ON raw_events (ingest_day);
CREATE INDEX IF NOT EXISTS idx_raw_events_session    ON raw_events (session_id);
CREATE INDEX IF NOT EXISTS idx_raw_events_name       ON raw_events (event_name);

-- ---------------------------------------------------------------------------
-- Rejects. Anything the collector refuses lands here instead of vanishing.
-- Without this table a validation bug looks exactly like a traffic drop.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_event_rejects (
  reject_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  rejected_at TEXT NOT NULL,
  reason      TEXT NOT NULL,
  payload     TEXT
);

CREATE INDEX IF NOT EXISTS idx_rejects_at ON raw_event_rejects (rejected_at);
