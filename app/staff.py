"""Staff accounts stored in Supabase. Env owner/staff still work without this table."""
from typing import List, Optional

from supabase import create_client

from app.config import STAFF_USERNAME, SUPABASE_KEY, SUPABASE_URL

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

MISSING_TABLE_HINTS = (
    "PGRST205",
    "does not exist",
    "Could not find the table",
    'relation "staff_users" does not exist',
)

ASSIGNABLE_PERMISSIONS = (
    "dashboard",
    "bookings",
    "invoices",
    "cash",
    "slots",
    "leads",
    "waitlist",
    "chats",
    "analytics",
)

DEFAULT_STAFF_PERMISSIONS = [
    "dashboard",
    "bookings",
    "slots",
    "leads",
    "waitlist",
    "chats",
]

SETUP_SQL = """CREATE TABLE IF NOT EXISTS staff_users (
  username TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'staff' CHECK (role = 'staff'),
  is_active BOOLEAN NOT NULL DEFAULT true,
  permissions JSONB NOT NULL DEFAULT '["dashboard","bookings","slots","leads","waitlist","chats"]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.staff_users
  ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL DEFAULT '["dashboard","bookings","slots","leads","waitlist","chats"]'::jsonb;

ALTER TABLE public.staff_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.staff_users FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.staff_users FROM anon;
REVOKE ALL ON TABLE public.staff_users FROM authenticated;

NOTIFY pgrst, 'reload schema';
"""

_SELECT = "username,password_hash,role,is_active,created_at,permissions"
_SELECT_BASIC = "username,password_hash,role,is_active,created_at"


def table_missing(err: Exception) -> bool:
    msg = str(err).lower()
    return any(h.lower() in msg for h in MISSING_TABLE_HINTS)


def _column_missing(err: Exception) -> bool:
    msg = str(err).lower()
    return "permissions" in msg and (
        "pgrst204" in msg or "could not find" in msg or "schema cache" in msg or "column" in msg
    )


def normalize_permissions(raw) -> List[str]:
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(",") if p.strip()]
    if not isinstance(raw, list):
        return list(DEFAULT_STAFF_PERMISSIONS)
    seen = []
    for item in raw:
        key = str(item or "").strip().lower()
        if key in ASSIGNABLE_PERMISSIONS and key not in seen:
            seen.append(key)
    return seen


def _row_permissions(row: Optional[dict]) -> List[str]:
    if not row:
        return list(DEFAULT_STAFF_PERMISSIONS)
    return normalize_permissions(row.get("permissions"))


def public_member(row: dict, *, source: str = "database", can_disable: bool = True) -> dict:
    return {
        "username": row.get("username"),
        "role": "staff",
        "is_active": bool(row.get("is_active", True)),
        "created_at": row.get("created_at"),
        "source": source,
        "can_disable": can_disable,
        "can_edit_permissions": source == "database",
        "permissions": _row_permissions(row),
    }


def env_staff_permissions() -> List[str]:
    return list(DEFAULT_STAFF_PERMISSIONS)


def permissions_for_username(username: str, role: str) -> List[str]:
    if role == "owner":
        return list(ASSIGNABLE_PERMISSIONS)
    if STAFF_USERNAME and username == STAFF_USERNAME:
        return env_staff_permissions()
    return _row_permissions(get_staff_row(username))


def get_staff_row(username: str) -> Optional[dict]:
    try:
        res = (
            supabase.table("staff_users")
            .select(_SELECT)
            .eq("username", username)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        return res.data[0]
    except Exception as e:
        if table_missing(e):
            return None
        if _column_missing(e):
            try:
                res = (
                    supabase.table("staff_users")
                    .select(_SELECT_BASIC)
                    .eq("username", username)
                    .limit(1)
                    .execute()
                )
                return res.data[0] if res.data else None
            except Exception as inner:
                if table_missing(inner):
                    return None
                raise
        raise


def staff_is_active(username: str) -> bool:
    row = get_staff_row(username)
    return bool(row and row.get("is_active"))


def list_staff_rows() -> list:
    try:
        res = (
            supabase.table("staff_users")
            .select("username,role,is_active,created_at,permissions")
            .order("created_at")
            .execute()
        )
        return res.data or []
    except Exception as e:
        if table_missing(e):
            return []
        if _column_missing(e):
            res = (
                supabase.table("staff_users")
                .select("username,role,is_active,created_at")
                .order("created_at")
                .execute()
            )
            return res.data or []
        raise


def create_staff_row(username: str, password_hash: str, permissions=None) -> dict:
    perms = normalize_permissions(permissions)
    payload = {
        "username": username,
        "password_hash": password_hash,
        "role": "staff",
        "is_active": True,
        "permissions": perms,
    }
    try:
        res = supabase.table("staff_users").insert(payload).execute()
    except Exception as e:
        if _column_missing(e):
            payload.pop("permissions", None)
            res = supabase.table("staff_users").insert(payload).execute()
        else:
            raise
    if not res.data:
        raise RuntimeError("Staff insert returned no row")
    return public_member(res.data[0], source="database", can_disable=True)


def set_staff_active(username: str, is_active: bool) -> Optional[dict]:
    res = (
        supabase.table("staff_users")
        .update({"is_active": is_active})
        .eq("username", username)
        .execute()
    )
    if not res.data:
        return None
    return public_member(res.data[0], source="database", can_disable=True)


def set_staff_permissions(username: str, permissions) -> Optional[dict]:
    perms = normalize_permissions(permissions)
    try:
        res = (
            supabase.table("staff_users")
            .update({"permissions": perms})
            .eq("username", username)
            .execute()
        )
    except Exception as e:
        if _column_missing(e):
            raise
        raise
    if not res.data:
        return None
    return public_member(res.data[0], source="database", can_disable=True)
