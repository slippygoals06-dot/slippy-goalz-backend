from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from app.routes import bookings, slots, leads, chat, auth, invoices, audit, cash_ledger, whatsapp, integrations, parts
from app.config import SUPABASE_URL, SUPABASE_KEY, GROQ_API_KEY

if not SUPABASE_URL or not SUPABASE_KEY or not GROQ_API_KEY:
    raise ValueError("Missing environment variables. Check your .env file.")

app = FastAPI(
    title="Slippy Goalz API",
    description="Backend for Slippy Goalz Dashboard",
    version="1.0.0"
)

# Trust Railway's proxy headers so HTTPS is correctly identified
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Explicit allowlist — no wildcard for the dashboard API.
# "null" covers local file:// widget testing (Downloads / open-in-browser).
# Public chat widget must be opened from an allowed origin, or via
# http://localhost:5173/wefix-widget.html when using the Vite app.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://irepair-dashboard.vercel.app",
        "https://slippy-goalz-dashboard.vercel.app",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5180",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5180",
        "null",  # file:// HTML widget
    ],
    # Localhost + Vercel preview/production for irepair or slippy
    allow_origin_regex=r"https://(irepair-dashboard|slippy-goalz-dashboard)([a-z0-9-]*)?\.vercel\.app$|https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["Auth"])
app.include_router(bookings.router, prefix="/bookings", tags=["Bookings"])
app.include_router(slots.router, prefix="/slots", tags=["Slots"])
app.include_router(leads.router, prefix="/leads", tags=["Leads"])
app.include_router(chat.router, prefix="/chat", tags=["Chat"])
app.include_router(invoices.router, prefix="/invoices", tags=["Invoices"])
app.include_router(audit.router, prefix="/audit-events", tags=["Audit"])
app.include_router(cash_ledger.router, prefix="/cash-ledger", tags=["Cash Ledger"])
app.include_router(whatsapp.router, tags=["WhatsApp"])
app.include_router(integrations.router, prefix="/integrations", tags=["Integrations"])
app.include_router(parts.router, prefix="/parts", tags=["Parts"])

@app.get("/")
def root():
    return {"status": "ok", "message": "iRepair API is running"}