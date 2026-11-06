-- ============================================================
-- CityLens CMI — Supabase Schema
-- Run this ONCE in your Supabase SQL Editor:
-- https://supabase.com/dashboard/project/wuqkasfqcksgayyamvma/editor
-- ============================================================

-- ── Users Profile Table ──────────────────────────────────────
-- Stores citizen and admin profiles.
-- Uses TEXT id (not UUID) so it matches the browser-generated ids
-- like 'cit-abc123', 'admin-demo', etc.
CREATE TABLE IF NOT EXISTS public.cmi_users (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  email       TEXT NOT NULL UNIQUE,
  role        TEXT NOT NULL DEFAULT 'citizen'
                CHECK (role IN ('citizen', 'admin')),
  status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'pending', 'rejected')),
  employee_id TEXT,
  -- PBKDF2 hash/salt kept here so browser-side auth fallback works
  salt        TEXT,
  hash        TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- ── Incidents Table ──────────────────────────────────────────
-- Stores every reported road hazard, waterlogging, or roadblock.
CREATE TABLE IF NOT EXISTS public.cmi_incidents (
  id           TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  cat          TEXT NOT NULL,                 -- Pothole | Roadblock | Flooding | Traffic
  sev          TEXT NOT NULL,                 -- critical | high | medium
  loc          TEXT,
  lat          DOUBLE PRECISION NOT NULL,
  lng          DOUBLE PRECISION NOT NULL,
  source       TEXT NOT NULL DEFAULT 'citizen',  -- citizen | ai | official
  reporter_id  TEXT REFERENCES public.cmi_users(id) ON DELETE SET NULL,
  description  TEXT,
  photo_url    TEXT,
  confidence   DOUBLE PRECISION,              -- AI confidence score (0.0-1.0)
  status       TEXT NOT NULL DEFAULT 'pending',
  verified_by  TEXT[] DEFAULT '{}',           -- array of user ids who confirmed
  history      JSONB DEFAULT '[]',            -- [{at, by, to, note}]
  reported_at  TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now()
);

-- Auto-update updated_at on every change
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

DROP TRIGGER IF EXISTS trg_incidents_updated_at ON public.cmi_incidents;
CREATE TRIGGER trg_incidents_updated_at
  BEFORE UPDATE ON public.cmi_incidents
  FOR EACH ROW EXECUTE PROCEDURE update_updated_at();

-- ── Credit Ledger Table ──────────────────────────────────────
-- Append-only. event_id prevents double-crediting.
CREATE TABLE IF NOT EXISTS public.cmi_ledger (
  event_id    TEXT PRIMARY KEY,
  user_id     TEXT REFERENCES public.cmi_users(id) ON DELETE CASCADE,
  amount      INT NOT NULL DEFAULT 0,
  reason      TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- ── RLS: Disable for now (open read/write with anon key) ─────
-- You can add policies later once auth is fully connected.
ALTER TABLE public.cmi_users     DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.cmi_incidents DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.cmi_ledger    DISABLE ROW LEVEL SECURITY;

-- ── Indexes for fast queries ─────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_incidents_status    ON public.cmi_incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_location  ON public.cmi_incidents(lat, lng);
CREATE INDEX IF NOT EXISTS idx_incidents_reported  ON public.cmi_incidents(reported_at DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_user         ON public.cmi_ledger(user_id);

-- ============================================================
-- After running this SQL, go back to the app:
-- http://localhost:8080/citylens.html
-- The first page load will auto-seed 7 demo incidents into the
-- Supabase table. All new reports will then be stored live.
-- ============================================================
