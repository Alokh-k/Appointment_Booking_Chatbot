from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import load_clinic_config
from app.database import ClinicRepository, Database
from app.llm import build_planner
from app.models import ChatRequest, ChatResponse
from app.service import BookingAssistant
from app.settings import get_settings


def build_assistant() -> BookingAssistant:
    settings = get_settings()
    config = load_clinic_config()
    database = Database(settings.database_url)
    database.seed(config)
    repository = ClinicRepository(database, reminder_lead_minutes=settings.reminder_lead_minutes)
    planner = build_planner(settings)
    return BookingAssistant(config=config, repository=repository, planner=planner)


app = FastAPI(
    title="AI Appointment Booking Chatbot",
    description="Clinic appointment assistant with LLM tool calling, SQLite persistence, and double-booking prevention.",
    version="1.0.0",
)
assistant = build_assistant()
web_dir = Path(__file__).resolve().parent.parent / "web"
app.mount("/static", StaticFiles(directory=web_dir), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(web_dir / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
def public_config() -> dict:
    config = load_clinic_config()
    return {
        "clinic": config.clinic.model_dump(),
        "services": [service.model_dump() for service in config.services],
        "providers": [provider.model_dump() for provider in config.providers],
    }


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, booking_assistant: BookingAssistant = Depends(lambda: assistant)):
    try:
        return booking_assistant.chat(request.session_id, request.message)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/reminders/due")
def due_reminders() -> dict:
    from datetime import datetime

    settings = get_settings()
    repository = ClinicRepository(Database(settings.database_url), settings.reminder_lead_minutes)
    return {"items": repository.due_reminders(datetime.utcnow())}


@app.post("/reminders/send")
def send_reminders() -> dict:
    from datetime import datetime

    settings = get_settings()
    repository = ClinicRepository(Database(settings.database_url), settings.reminder_lead_minutes)
    items = repository.process_due_reminders(datetime.utcnow())
    return {"sent": len(items), "items": items}


async def _reminder_worker() -> None:
    settings = get_settings()
    repository = ClinicRepository(Database(settings.database_url), settings.reminder_lead_minutes)
    try:
        while True:
            items = await asyncio.to_thread(repository.process_due_reminders)
            if items:
                print(f"[reminder_worker] processed {len(items)} reminders")
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        return


@app.on_event("startup")
async def start_reminder_worker() -> None:
    app.state.reminder_task = asyncio.create_task(_reminder_worker())


@app.on_event("shutdown")
async def stop_reminder_worker() -> None:
    task = getattr(app.state, "reminder_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
