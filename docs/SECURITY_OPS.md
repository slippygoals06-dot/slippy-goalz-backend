# Security ops — Slippy Goalz Arena

## Required SQL (run in order in Supabase)
1. `021_atomic_slot_booking.sql`
2. `022_security_lockdown.sql`
3. `023_auth_sessions_2fa.sql`

## Secrets rotation
| Secret | Where | How to rotate |
|--------|--------|----------------|
| `SECRET_KEY` | Railway | Generate 64+ random chars. **Re-encrypt** WA tokens (re-save in Settings) and re-enable TOTP after change — Fernet key is derived from SECRET_KEY. |
| `SUPABASE_KEY` (service_role) | Railway | Rotate in Supabase → Project Settings → API; update Railway; restart. |
| `OWNER_PASSWORD` | Railway | Change env; restart. Forces new logins (refresh cookies expire in 7d or revoke via staff disable / logout). |
| WhatsApp token | Settings UI | Re-connect WhatsApp — stored Fernet-encrypted. |
| `GROQ_API_KEY` | Railway | Rotate at Groq; update env. |

Never commit `.env`. Prefer Railway variables only.

## Sessions
- Access cookie: 30 minutes (httpOnly, Secure in prod, SameSite=None cross-site)
- Refresh cookie: 7 days; revoked on logout and when staff is disabled
- Owner TOTP: Settings/Security → enable after running migration 023

## Staging
Use a separate Railway service + Supabase project with different `SECRET_KEY` and Meta app. Point a `*-staging.vercel.app` origin at it and add that origin to CORS.

## WAF / Cloudflare
Put Cloudflare (or similar) in front of the Railway public URL: enable Bot Fight / WAF rate limits on `/auth/login` and `/bookings/`. Not configured in-repo — DNS cutover only.

## After deploy checklist
- [ ] Migrations 021–023 applied
- [ ] Re-save WhatsApp credentials
- [ ] Log in once; confirm cookie `slippy_access` on API host
- [ ] Enable owner 2FA and store recovery codes offline (secret shown once at setup)
- [ ] Confirm Parts/Verify hidden from nav
