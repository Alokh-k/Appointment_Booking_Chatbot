# AI Appointment Booking Chatbot

Python backend service for a clinic appointment booking assistant. It uses FastAPI, OpenAI function/tool calling when credentials are configured, SQLite persistence, and a deterministic local planner for tests and offline development.

## Features

- Natural language `/chat` endpoint with `session_id`
- OpenAI tool calling converts messages into structured backend actions
- Supports booking, rescheduling, cancelling, listing, and checking availability
- Stores doctors, services, appointments, reminders, and conversation history in SQLite
- Prevents double bookings with a database-level unique index for confirmed doctor/time slots
- Supports multiple doctors and service mappings
- Generates scheduled reminder records for booked/rescheduled appointments
- Interactive API docs at `/docs`

## Configuration

Clinic/business values are not embedded in Python code. The clinic name, services, doctors, working hours, user-facing messages, and LLM system prompt are loaded from `APP_CONFIG_PATH`.

Runtime values are supplied with environment variables:

```bash
cp .env.example .env
```

Important variables:

```bash
APP_CONFIG_PATH=config/clinic.example.json
DATABASE_URL=sqlite:///.data/clinic.db
LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
REMINDER_LEAD_MINUTES=1440
```

If `OPENAI_API_KEY` is blank, the app uses the deterministic fallback planner.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`.

## API

`GET /health`

Returns service health.

`GET /config`

Returns public clinic configuration: clinic, services, and doctors.

`POST /chat`

```json
{
  "session_id": "demo-session",
  "message": "Book appointment name: Priya phone +91 99999 99999 for General Consultation with Dr. Asha at 2026-06-08T09:00"
}
```

Other example messages:

```text
Is Dr. Asha available for General Consultation at 2026-06-08T09:00?
Reschedule appointment: <appointment_id> to 2026-06-08T10:00
Cancel appointment: <appointment_id>
Show appointments
```

`GET /reminders/due`

Returns reminder records due at the current server time. This is intentionally a read endpoint for interview/demo purposes; a production reminder worker would claim and send these reminders.

## Database Models

SQLite tables are created automatically at startup:

- `doctors`: configured doctors/providers
- `services`: configured appointment services
- `doctor_services`: many-to-many doctor/service support
- `appointments`: booked appointments and status
- `conversation_messages`: persisted chat context by `session_id`
- `reminders`: reminder schedule records for appointments

The `appointments` table has a partial unique index on `(provider_id, starts_at)` where `status = 'confirmed'` to prevent double bookings even under concurrent writes.

## Tests

```bash
pytest
```
# Appointment_Booking_Chatbot
