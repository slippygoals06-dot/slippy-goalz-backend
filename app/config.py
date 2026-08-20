from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY") or ""
ALGORITHM = "HS256"
OWNER_USERNAME = os.getenv("OWNER_USERNAME") or ""
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD") or ""
STAFF_USERNAME = (os.getenv("STAFF_USERNAME") or "").strip()
STAFF_PASSWORD = os.getenv("STAFF_PASSWORD") or ""
if STAFF_USERNAME and STAFF_USERNAME == OWNER_USERNAME:
    STAFF_USERNAME = ""
    STAFF_PASSWORD = ""

_ENV = (os.getenv("ENV") or os.getenv("RAILWAY_ENVIRONMENT") or "").strip().lower()
IS_PRODUCTION = _ENV in ("production", "prod") or bool(os.getenv("RAILWAY_ENVIRONMENT_NAME"))

# Optional — WhatsApp Cloud API (app boots without these; sends no-op with a clear error)
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# Public customer booking page (Vercel)
BOOKING_PAGE_URL = os.getenv(
    "BOOKING_PAGE_URL",
    "https://slippy-goalz-dashboard-blue.vercel.app/book",
).rstrip("/")

# Optional — set REMINDERS_ENABLED=true only after Meta templates are Approved
REMINDERS_ENABLED = os.getenv("REMINDERS_ENABLED", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

# WhatsApp template for admin Confirm (must be Approved in Meta Business Manager)
WHATSAPP_CONFIRM_TEMPLATE = (
    os.getenv("WHATSAPP_CONFIRM_TEMPLATE") or "booking_confirmed"
).strip()
