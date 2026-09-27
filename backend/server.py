"""AI Social Media Agent — FastAPI backend.

Production notes:
- Every secret/URL comes from env vars (see .env.example).
- /health is public. Every /api/* endpoint requires the X-API-Key header
  (shared secret from the API_KEY env var).
- All client-facing errors are structured JSON; stack traces never leave the server.
"""
import io
import csv
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

import litellm
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from backend.database import Base, SessionLocal, engine, get_db
from backend.models import ExecutionLog, Setting

load_dotenv()

logger = logging.getLogger("social-media-agent")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Configuration (all from environment)
# ---------------------------------------------------------------------------

API_KEY = os.getenv("API_KEY", "")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Model allowlist: model_id must start with one of these prefixes so the
# provider -> API-key mapping below stays correct and injection is impossible.
MODEL_PREFIXES = ("gpt", "claude", "gemini", "zhipu", "ollama")

TASK_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")

# ---------------------------------------------------------------------------
# Auth (shared-secret header on all /api/* endpoints)
# ---------------------------------------------------------------------------

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(provided: str | None = Depends(api_key_header)) -> None:
    """Dependency: 401 unless X-API-Key matches the API_KEY env var."""
    if not API_KEY:
        # Misconfigured server — refuse everything rather than run open.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "server_misconfigured", "message": "API_KEY is not set"},
        )
    if not provided or provided != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "Invalid or missing X-API-Key header"},
        )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready")
    yield


app = FastAPI(title="AI Social Media Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,          # no wildcard default: set CORS_ORIGINS in env
    allow_credentials=bool(CORS_ORIGINS),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key", "X-OpenAI-Key", "X-Anthropic-Key", "X-Gemini-Key", "X-GLM-Key"],
)


# ---------------------------------------------------------------------------
# Structured error responses (never leak stack traces to clients)
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": "http_error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=detail)
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "Something went wrong. Please try again."},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "Request validation failed",
            "fields": [
                {"field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]}
                for err in exc.errors()
            ],
        },
    )


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Liveness probe — public, no auth."""
    return {"status": "ok", "service": "social-media-agent", "time": datetime.utcnow().isoformat() + "Z"}


# ---------------------------------------------------------------------------
# Job processing
# ---------------------------------------------------------------------------

def generate_hootsuite_csv(posts: list) -> str:
    """Converts the JSON array of posts into Hootsuite bulk-upload CSV format."""
    output = io.StringIO()
    writer = csv.writer(output)
    for post in posts:
        writer.writerow([post.get("date", ""), post.get("message", ""), post.get("url", "")])
    return output.getvalue()


def resolve_provider_creds(model_id: str, api_keys: dict) -> tuple[str | None, str | None]:
    """Map a model_id prefix to (api_key, api_base). Raises ValueError when unknown."""
    if model_id.startswith("gpt"):
        return api_keys.get("openai") or os.getenv("OPENAI_API_KEY"), None
    if model_id.startswith("claude"):
        return api_keys.get("anthropic") or os.getenv("ANTHROPIC_API_KEY"), None
    if model_id.startswith("gemini"):
        return api_keys.get("gemini") or os.getenv("GEMINI_API_KEY"), None
    if model_id.startswith("zhipu"):
        return api_keys.get("glm") or os.getenv("ZHIPUAI_API_KEY"), None
    if model_id.startswith("ollama"):
        return None, OLLAMA_BASE_URL
    raise ValueError(f"Unsupported model_id: {model_id}")


async def process_social_job(task_id: str, brand_voice: str, post_count: int, model_id: str, api_keys: dict):
    async with SessionLocal() as db:
        try:
            result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
            log = result.scalar_one()
            log.status = "running"
            await db.commit()

            api_key, api_base = resolve_provider_creds(model_id, api_keys)
            if not api_key and not api_base:
                raise RuntimeError(f"No API key provided for {model_id}")

            start_date = datetime.now() + timedelta(days=1)
            date_example = start_date.strftime("%m/%d/%Y %H:%M")

            system_prompt = (
                "You are an expert Social Media Manager AI. "
                "Output ONLY a raw JSON array of objects representing social media posts. "
                "No markdown fences (e.g., no ```json). Just the raw array `[{...}]`.\n"
                "JSON format per post:\n"
                "{\n"
                f'  "date": "{date_example}",\n'
                '  "message": "Your highly engaging post copy goes here with #hashtags and emojis.",\n'
                '  "url": "https://example.com/optional-link"\n'
                "}\n"
                "RULES:\n"
                f"1. You MUST generate exactly {post_count} posts.\n"
                "2. The 'date' MUST be in exactly 'mm/dd/yyyy hh:mm' format.\n"
                "3. Space the posts out logically (e.g. 1 per day or a few per week)."
            )
            user_prompt = (
                f"Brand Voice / Target Audience / Strategy:\n{brand_voice}\n\n"
                f"Please generate the {post_count} posts."
            )

            response = await litellm.acompletion(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                api_key=api_key,
                api_base=api_base,
                max_tokens=4000,
            )

            raw_text = response.choices[0].message.content.strip()
            for fence in ("```json", "```"):
                if raw_text.startswith(fence):
                    raw_text = raw_text[len(fence):]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]

            calendar_data = json.loads(raw_text)

            log.calendar_json = json.dumps(calendar_data)
            log.csv_output = generate_hootsuite_csv(calendar_data)
            log.status = "success"
            await db.commit()
            logger.info("Task %s finished successfully (%d posts)", task_id, post_count)

        except Exception:
            logger.exception("Task %s failed", task_id)
            try:
                result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
                log = result.scalar_one_or_none()
                if log:
                    log.status = "error"
                    log.calendar_json = json.dumps({"error": "content generation failed"})
                    await db.commit()
            except Exception:
                logger.exception("Failed to record error status for task %s", task_id)


# ---------------------------------------------------------------------------
# Authenticated API
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    brand_voice: str = Field(min_length=10, max_length=4000)
    post_count: int = Field(ge=1, le=60)
    model_id: str = Field(min_length=2, max_length=80)


class ApiKeysUpdate(BaseModel):
    openai_api_key: str | None = Field(default=None, max_length=200)
    anthropic_api_key: str | None = Field(default=None, max_length=200)
    gemini_api_key: str | None = Field(default=None, max_length=200)
    glm_api_key: str | None = Field(default=None, max_length=200)


@app.post("/api/execute", status_code=202, dependencies=[Depends(require_api_key)])
async def enqueue_task(req: ExecuteRequest, request: Request, db: AsyncSession = Depends(get_db)):
    if not any(req.model_id.startswith(p) for p in MODEL_PREFIXES):
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": f"model_id must start with one of {MODEL_PREFIXES}"},
        )
    task_id = str(uuid.uuid4())

    log = ExecutionLog(
        task_id=task_id,
        brand_voice=req.brand_voice,
        post_count=req.post_count,
        model_provider=req.model_id,
        status="pending",
    )
    db.add(log)
    await db.commit()

    # Per-request provider keys may come via headers (never stored server-side).
    api_keys = {
        "openai": request.headers.get("X-OpenAI-Key"),
        "anthropic": request.headers.get("X-Anthropic-Key"),
        "gemini": request.headers.get("X-Gemini-Key"),
        "glm": request.headers.get("X-GLM-Key"),
    }

    # Run the generation inline-friendly background step without blocking.
    async def _runner():
        await process_social_job(task_id, req.brand_voice, req.post_count, req.model_id, api_keys)

    import asyncio
    asyncio.create_task(_runner())

    return {"status": "accepted", "task_id": task_id}


@app.get("/api/tasks/{task_id}", dependencies=[Depends(require_api_key)])
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    if not TASK_ID_RE.match(task_id):
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_task_id", "message": "task_id must be a UUID"},
        )
    result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Task not found"},
        )

    calendar_data = None
    if log.calendar_json:
        try:
            calendar_data = json.loads(log.calendar_json)
        except json.JSONDecodeError:
            calendar_data = None

    return {"status": log.status, "calendar_data": calendar_data, "csv_output": log.csv_output}


@app.post("/api/settings/keys", dependencies=[Depends(require_api_key)])
async def update_keys(req: ApiKeysUpdate, db: AsyncSession = Depends(get_db)):
    keys = {
        "openai_api_key": req.openai_api_key,
        "anthropic_api_key": req.anthropic_api_key,
        "gemini_api_key": req.gemini_api_key,
        "glm_api_key": req.glm_api_key,
    }
    for k, v in keys.items():
        if v:
            res = await db.execute(select(Setting).where(Setting.key == k))
            setting = res.scalar_one_or_none()
            if setting:
                setting.value = v
            else:
                db.add(Setting(key=k, value=v))
    await db.commit()
    return {"status": "success"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8008")))
