# AI Social Media Agent

Generate a month of social media content for a business (restaurants, salons, clinics, gyms) with one click: describe the brand voice, pick a model, get back a dated content calendar plus a Hootsuite bulk-upload CSV.

- **Backend:** FastAPI (Python), LiteLLM (OpenAI / Anthropic / Gemini / GLM / local Ollama)
- **Frontend:** Next.js (React)
- **Database:** SQLite (default) or PostgreSQL via `DATABASE_URL`

## Quick start (local)

Requirements: Python 3.11+, Node 18+.

```bash
# 1. Configure — copy the example and fill in real values
cp .env.example .env
# At minimum set: API_KEY (long random secret) and one LLM provider key.

# 2. Backend
pip install -r backend/requirements.txt
uvicorn backend.server:app --host 0.0.0.0 --port 8008

# 3. Frontend (new terminal)
cd frontend
npm install
npm run dev
# Open http://localhost:3000 — enter your API_KEY in the "Backend API Key" field.
```

Check it works: `curl http://localhost:8008/health` → `{"status":"ok",...}`

Smoke test (boots its own server, uses dummy keys only):

```bash
python scripts/smoke_test.py
```

## API

**Auth:** `GET /health` is public. Every `/api/*` endpoint requires the header
`X-API-Key: <API_KEY>` (the `API_KEY` value from your `.env`). Wrong/missing key → `401`.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Liveness probe (public, no auth) |
| POST | `/api/execute` | Queue a content-generation job → `202 {"status":"accepted","task_id":...}`. Optional per-request provider keys via `X-OpenAI-Key`, `X-Anthropic-Key`, `X-Gemini-Key`, `X-GLM-Key` headers. |
| GET | `/api/tasks/{task_id}` | Job status + calendar JSON + Hootsuite CSV |
| POST | `/api/settings/keys` | Store provider keys server-side (per workspace) |

**Validation:** `brand_voice` 10–4000 chars, `post_count` 1–60, `model_id` must start with `gpt`, `claude`, `gemini`, `zhipu` or `ollama` (e.g. `gpt-4o`, `claude-3-5-sonnet-20240620`, `gemini/gemini-1.5-pro`, `zhipu/glm-4`, `ollama/llama3`).
**Errors:** always structured JSON (`{"error": ..., "message": ...}`) — stack traces never reach clients. Bad input → `422`, bad key → `401`, unknown task → `404`.

Example:

```bash
export API_KEY=<your secret>
curl -X POST http://localhost:8008/api/execute \
  -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d '{"brand_voice":"Friendly neighbourhood cafe, playful tone, daily specials and latte art","post_count":7,"model_id":"gpt-4o"}'
# -> {"status":"accepted","task_id":"..."}
curl -H "X-API-Key: $API_KEY" http://localhost:8008/api/tasks/<task_id>
```

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `API_KEY` | **yes** | — | Shared secret; every `/api/*` request must send it as `X-API-Key`. Without it the server refuses all API calls. Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `OPENAI_API_KEY` | one provider key needed | dummy placeholder | OpenAI (models starting `gpt`) |
| `ANTHROPIC_API_KEY` | — | dummy placeholder | Anthropic (models starting `claude`) |
| `GEMINI_API_KEY` | — | dummy placeholder | Google (models starting `gemini`) |
| `ZHIPUAI_API_KEY` | — | dummy placeholder | Zhipu (models starting `zhipu`) |
| `DATABASE_URL` | no | `sqlite+aiosqlite:///./social.db` | Async DB URL. Postgres: `postgresql+asyncpg://user:pass@host/db` (add `asyncpg` to `backend/requirements.txt`) |
| `OLLAMA_BASE_URL` | no | `http://localhost:11434` | Base URL for `ollama/...` models |
| `CORS_ORIGINS` | no | (empty = no browser CORS) | Comma-separated allowed origins, e.g. `https://app.yourdomain.com`. Defaults to **no CORS** (secure); only add your real frontend origin |
| `PORT` | no | `8008` | Server port (Render sets this automatically) |
| `NEXT_PUBLIC_API_URL` | no (frontend) | `http://localhost:8008` | Backend URL the browser calls |
| `NEXT_PUBLIC_API_KEY` | no (frontend) | — | Prefills the UI's API-key field; same value as `API_KEY` |

## Deploy on Render (free tier)

**Backend (Web Service):**
1. New → Web Service → connect this repo.
2. Build command: `pip install -r backend/requirements.txt`
3. Start command: `uvicorn backend.server:app --host 0.0.0.0 --port $PORT`
4. Environment variables: set `API_KEY` (generate a random secret), one LLM provider key, and optionally `DATABASE_URL` (or attach Render Postgres). Leave `CORS_ORIGINS` empty until the frontend is live, then add its URL.
5. Note: SQLite on Render's free tier is ephemeral — use Render Postgres (`DATABASE_URL`) if job history must survive restarts.

**Frontend (Static Site / Cloudflare Pages):**
1. Point at `frontend/`, build command `npm run build`, publish dir `frontend/out` (or deploy to Cloudflare Pages).
2. Set build env: `NEXT_PUBLIC_API_URL=https://<your-backend>.onrender.com` and `NEXT_PUBLIC_API_KEY=<same API_KEY>`.
3. Add the frontend URL to the backend's `CORS_ORIGINS`.

**Docker:**
```bash
docker build -t social-media-agent .
docker run -p 8008:8008 --env-file .env social-media-agent
# curl http://localhost:8008/health
```
The image ships a `HEALTHCHECK` against `/health`.

## Security notes

- No secrets are hardcoded anywhere — everything comes from env vars or request headers.
- CORS is closed by default; add only your real frontend origin.
- Per-request LLM keys via headers are never stored server-side.
- Keys stored via `/api/settings/keys` live in the app database — use a Postgres instance with access controls in production.
- Errors are structured JSON; stack traces are logged server-side only.

## Selling notes (for operators)

- Human review is part of the offer: the agent drafts the calendar, the operator approves before uploading the CSV to Hootsuite/Buffer/Meta Business Suite.
- Retainer fit: restaurants, salons, clinics, gyms posting daily. Suggested: ₹8–12k/month per client.
- Needs a real LLM provider key to generate content; without one, jobs fail with status `error` (visible via `/api/tasks/{id}`).
