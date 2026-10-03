-- The Manager — deterministic findings for the single-business MVP.
-- Singleton business_id: 00000000-0000-0000-0000-000000000001
-- Run in Supabase SQL Editor after deploying the Manager backend.

CREATE TABLE IF NOT EXISTS public.findings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL
    CHECK (business_id = '00000000-0000-0000-0000-000000000001'::uuid),
  type TEXT NOT NULL,
  severity TEXT NOT NULL
    CHECK (severity IN ('red', 'yellow', 'green')),
  title TEXT NOT NULL,
  evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  confidence TEXT NOT NULL
    CHECK (confidence IN ('high', 'medium', 'low')),
  cause_known BOOLEAN NOT NULL DEFAULT FALSE,
  rs_impact NUMERIC NOT NULL DEFAULT 0,
  expires_at TIMESTAMPTZ,
  suggested_action TEXT,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'approved', 'dismissed', 'done')),
  owner_decision TEXT,
  decided_at TIMESTAMPTZ,
  verified_result JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  dedupe_key TEXT NOT NULL CHECK (btrim(dedupe_key) <> '')
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_findings_open_business_dedupe
  ON public.findings (business_id, dedupe_key)
  WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_findings_business_status_created
  ON public.findings (business_id, status, created_at DESC);

ALTER TABLE public.findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.findings FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.findings FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.findings TO service_role;

COMMENT ON TABLE public.findings IS
  'The Manager deterministic findings; business_id is the single-business MVP singleton UUID.';

CREATE OR REPLACE FUNCTION public.manager_upsert_open_finding(
  p_business_id UUID,
  p_type TEXT,
  p_severity TEXT,
  p_title TEXT,
  p_evidence_json JSONB,
  p_confidence TEXT,
  p_cause_known BOOLEAN,
  p_rs_impact NUMERIC,
  p_expires_at TIMESTAMPTZ,
  p_suggested_action TEXT,
  p_dedupe_key TEXT
)
RETURNS SETOF public.findings
LANGUAGE SQL
SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp
AS $$
  INSERT INTO public.findings (
    business_id,
    type,
    severity,
    title,
    evidence_json,
    confidence,
    cause_known,
    rs_impact,
    expires_at,
    suggested_action,
    status,
    dedupe_key
  )
  VALUES (
    p_business_id,
    p_type,
    p_severity,
    p_title,
    p_evidence_json,
    p_confidence,
    p_cause_known,
    p_rs_impact,
    p_expires_at,
    p_suggested_action,
    'open',
    p_dedupe_key
  )
  ON CONFLICT (business_id, dedupe_key) WHERE status = 'open'
  DO UPDATE SET
    type = EXCLUDED.type,
    severity = EXCLUDED.severity,
    title = EXCLUDED.title,
    evidence_json = EXCLUDED.evidence_json,
    confidence = EXCLUDED.confidence,
    cause_known = EXCLUDED.cause_known,
    rs_impact = EXCLUDED.rs_impact,
    expires_at = EXCLUDED.expires_at,
    suggested_action = EXCLUDED.suggested_action
  RETURNING *;
$$;

REVOKE ALL ON FUNCTION public.manager_upsert_open_finding(
  UUID, TEXT, TEXT, TEXT, JSONB, TEXT, BOOLEAN, NUMERIC, TIMESTAMPTZ, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.manager_upsert_open_finding(
  UUID, TEXT, TEXT, TEXT, JSONB, TEXT, BOOLEAN, NUMERIC, TIMESTAMPTZ, TEXT, TEXT
) TO service_role;

NOTIFY pgrst, 'reload schema';
