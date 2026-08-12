# Slippy Goalz Backend
#
# Local run:
#   python -m venv .venv
#   .venv\Scripts\activate
#   pip install -r requirements.txt
#   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
#
# Then in slippy-goalz-dashboard\.env.local:
#   VITE_API_URL=http://127.0.0.1:8000
#
# Until you deploy this backend to Railway, the dashboard uses the shared
# production API by default (see src/config.js).

Slippy Goalz API — based on the iRepair backend template.
