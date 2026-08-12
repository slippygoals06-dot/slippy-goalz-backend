from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
OWNER_USERNAME = os.getenv("OWNER_USERNAME")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD")

# Optional — WhatsApp Cloud API (app boots without these; sends no-op with a clear error)
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

# Public customer booking page (Vercel)
BOOKING_PAGE_URL = os.getenv(
    "BOOKING_PAGE_URL",
    "https://irepair-dashboard.vercel.app/book",
).rstrip("/")

# Optional — set REMINDERS_ENABLED=true only after Meta templates are Approved
REMINDERS_ENABLED = os.getenv("REMINDERS_ENABLED", "").strip().lower() in (
    "1",
    "true",
    "yes",
)
