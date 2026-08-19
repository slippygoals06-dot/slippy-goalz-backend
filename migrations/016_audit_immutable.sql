-- Activity log is append-only. Owner and staff cannot edit or delete rows.

CREATE OR REPLACE FUNCTION public.prevent_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'Audit log cannot be changed';
END;
$$;

DROP TRIGGER IF EXISTS audit_events_no_update ON public.audit_events;
DROP TRIGGER IF EXISTS audit_events_no_delete ON public.audit_events;

CREATE TRIGGER audit_events_no_update
  BEFORE UPDATE ON public.audit_events
  FOR EACH ROW
  EXECUTE PROCEDURE public.prevent_audit_mutation();

CREATE TRIGGER audit_events_no_delete
  BEFORE DELETE ON public.audit_events
  FOR EACH ROW
  EXECUTE PROCEDURE public.prevent_audit_mutation();
