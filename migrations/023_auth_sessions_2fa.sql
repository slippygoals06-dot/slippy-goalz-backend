-- Slippy Goalz Arena — auth sessions, 2FA, expanded audit actions
-- Run in Supabase → SQL Editor after 022.

-- ── Refresh / access session registry (revoke on staff disable / logout) ─────
CREATE TABLE IF NOT EXISTS public.auth_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username TEXT NOT NULL,
  refresh_jti TEXT NOT NULL UNIQUE,
  access_jti TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  user_agent TEXT,
  ip TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_username
  ON public.auth_sessions (username);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_refresh_jti
  ON public.auth_sessions (refresh_jti);

ALTER TABLE public.auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.auth_sessions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.auth_sessions FROM anon, authenticated;

-- ── Owner TOTP 2FA fields ────────────────────────────────────────────────────
ALTER TABLE public.owner_settings
  ADD COLUMN IF NOT EXISTS totp_secret TEXT;
ALTER TABLE public.owner_settings
  ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.owner_settings.totp_secret IS
  'Fernet-encrypted TOTP secret (enc:v1:…)';

-- ── Expand audit actions for security events ─────────────────────────────────
DO $$
DECLARE
  cname TEXT;
BEGIN
  SELECT conname INTO cname
  FROM pg_constraint
  WHERE conrelid = 'public.audit_events'::regclass
    AND contype = 'c'
    AND pg_get_constraintdef(oid) ILIKE '%confirmed%';
  IF cname IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.audit_events DROP CONSTRAINT %I', cname);
  END IF;

  ALTER TABLE public.audit_events
    ADD CONSTRAINT audit_events_action_check
    CHECK (action IN (
      'confirmed',
      'rejected',
      'deleted',
      'payment_changed',
      'completed_invoiced',
      'invoice_status_changed',
      'no_show',
      'login_ok',
      'login_fail',
      'logout',
      'session_revoked',
      'wa_connected',
      'attachment_uploaded',
      'totp_enabled',
      'totp_disabled'
    ));
EXCEPTION
  WHEN undefined_table THEN NULL;
  WHEN duplicate_object THEN NULL;
END $$;

NOTIFY pgrst, 'reload schema';
