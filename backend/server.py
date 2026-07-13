import os
import uuid
import json
import asyncio
import io
import csv
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from dotenv import load_dotenv
from pydantic import BaseModel
import litellm

from database import engine, Base, SessionLocal, get_db
from models import Setting, ExecutionLog

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def generate_hootsuite_csv(posts: list) -> str:
    """Converts the JSON array of posts into a Hootsuite bulk upload CSV format"""
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Hootsuite format: Date (mm/dd/yyyy hh:mm), Message, URL
    for post in posts:
        date_str = post.get("date", "")
        message = post.get("message", "")
        url = post.get("url", "")
        writer.writerow([date_str, message, url])
            
    return output.getvalue()

async def process_social_job(task_id: str, brand_voice: str, post_count: int, model_id: str, api_keys: dict):
    async with SessionLocal() as db:
        try:
            result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
            log = result.scalar_one()
            log.status = "running"
            await db.commit()
            
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
            
            user_prompt = f"Brand Voice / Target Audience / Strategy:\n{brand_voice}\n\nPlease generate the {post_count} posts."
            
            api_key = None
            api_base = None
            
            if model_id.startswith("gpt"):
                api_key = api_keys.get("openai") or os.getenv("OPENAI_API_KEY")
            elif model_id.startswith("claude"):
                api_key = api_keys.get("anthropic") or os.getenv("ANTHROPIC_API_KEY")
            elif model_id.startswith("gemini"):
                api_key = api_keys.get("gemini") or os.getenv("GEMINI_API_KEY")
            elif model_id.startswith("zhipu"):
                api_key = api_keys.get("glm") or os.getenv("ZHIPUAI_API_KEY")
            elif model_id.startswith("ollama"):
                api_base = "http://localhost:11434"
                
            if not api_key and not api_base:
                raise Exception(f"No API key provided for {model_id}")

            response = await litellm.acompletion(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                api_key=api_key,
                api_base=api_base,
                max_tokens=4000
            )
            
            raw_text = response.choices[0].message.content.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
                
            calendar_data = json.loads(raw_text)
            csv_output = generate_hootsuite_csv(calendar_data)
            
            log.calendar_json = json.dumps(calendar_data)
            log.csv_output = csv_output
            log.status = "success"
            await db.commit()
            
        except Exception as e:
            print(f"Error processing Social job: {e}")
            result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
            log = result.scalar_one_or_none()
            if log:
                log.status = "error"
                log.calendar_json = json.dumps({"error": str(e)})
                await db.commit()

class ExecuteRequest(BaseModel):
    brand_voice: str
    post_count: int
    model_id: str

@app.post("/api/execute")
async def enqueue_task(req: ExecuteRequest, background_tasks: BackgroundTasks, request: Request, db: AsyncSession = Depends(get_db)):
    task_id = str(uuid.uuid4())
    
    log = ExecutionLog(
        task_id=task_id,
        brand_voice=req.brand_voice,
        post_count=req.post_count,
        model_provider=req.model_id,
        status="pending"
    )
    db.add(log)
    await db.commit()
    
    api_keys = {
        "openai": request.headers.get("X-OpenAI-Key"),
        "anthropic": request.headers.get("X-Anthropic-Key"),
        "gemini": request.headers.get("X-Gemini-Key"),
        "glm": request.headers.get("X-GLM-Key")
    }
    
    background_tasks.add_task(process_social_job, task_id, req.brand_voice, req.post_count, req.model_id, api_keys)
    
    return {"status": "success", "task_id": task_id}

@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ExecutionLog).where(ExecutionLog.task_id == task_id))
    log = result.scalar_one_or_none()
    
    if not log:
        raise HTTPException(status_code=404, detail="Task not found")
        
    calendar_data = None
    if log.calendar_json:
        try:
            calendar_data = json.loads(log.calendar_json)
        except:
            pass
            
    return {
        "status": log.status,
        "calendar_data": calendar_data,
        "csv_output": log.csv_output
    }

class ApiKeysUpdate(BaseModel):
    openai_api_key: str = None
    anthropic_api_key: str = None
    gemini_api_key: str = None
    glm_api_key: str = None

@app.post("/api/settings/keys")
async def update_keys(req: ApiKeysUpdate, db: AsyncSession = Depends(get_db)):
    keys = {
        "openai_api_key": req.openai_api_key,
        "anthropic_api_key": req.anthropic_api_key,
        "gemini_api_key": req.gemini_api_key,
        "glm_api_key": req.glm_api_key
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
    uvicorn.run("server:app", host="0.0.0.0", port=8008, reload=True)
