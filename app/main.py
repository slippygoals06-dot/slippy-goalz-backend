from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import (
    SUPABASE_URL,
    SUPABASE_KEY,
    GROQ_API_KEY,
    SECRET_KEY,
    OWNER_USERNAME,
    OWNER_PASSWORD,
    IS_PRODUCTION,
)

if not SUPABASE_URL or not SUPABASE_KEY or not GROQ_API_KEY:
    raise ValueError("Missing environment variables. Check your .env file.")
if len(SECRET_KEY) < 32:
    raise ValueError("SECRET_KEY must be at least 32 characters.")
if not OWNER_USERNAME or not OWNER_PASSWORD:
    raise ValueError("OWNER_USERNAME and OWNER_PASSWORD are required.")

from app.routes import bookings, slots, leads, chat, auth, invoices, audit, cash_ledger, whatsapp, integrations, parts


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response


app = FastAPI(
    title="Slippy Goalz Arena API",
    description="Backend for Slippy Goalz Arena",
    version="1.0.0",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

_origins = [
    "https://slippy-goalz-dashboard.vercel.app",
    "https://slippy-goalz-dashboard-blue.vercel.app",
    "https://irepair-dashboard.vercel.app",
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5180",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:5180",
]
if not IS_PRODUCTION:
    _origins.append("null")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=r"https://(irepair-dashboard|slippy-goalz-dashboard)[A-Za-z0-9.-]*\.vercel\.app$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
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
    return {"status": "ok", "message": "Slippy Goalz Arena API is running"}
