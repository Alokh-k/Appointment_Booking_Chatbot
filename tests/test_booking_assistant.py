from unittest.mock import patch

from app.database import ClinicRepository, Database
from app.llm import OpenAIActionPlanner, RuleBasedActionPlanner
from app.models import (
    BookingConfig,
    ClinicConfig,
    ClinicInfo,
    LlmConfig,
    MessageConfig,
    ProviderConfig,
    ServiceConfig,
    TimeRange,
)
from app.settings import Settings
from app.service import BookingAssistant


def test_books_valid_appointment(tmp_path):
    assistant = _assistant(tmp_path)

    response = assistant.chat(
        "session-1",
        "Book appointment name: Priya phone +91 99999 99999 for consultation with doctor one at 2026-06-08T09:00",
    )

    assert response.message == "confirmed"
    assert response.appointment
    assert response.appointment["patient_name"] == "Priya"


def test_prevents_double_booking(tmp_path):
    assistant = _assistant(tmp_path)
    message = "Book appointment name: Priya phone +91 99999 99999 for consultation with doctor one at 2026-06-08T09:00"

    assistant.chat("session-1", message)
    response = assistant.chat("session-2", message)

    assert response.message == "invalid time"


def test_checks_availability(tmp_path):
    assistant = _assistant(tmp_path)

    response = assistant.chat(
        "session-1",
        "Is doctor one available for consultation at 2026-06-08T09:00?",
    )

    assert response.message == "available"
    assert response.data == {"available": True}


def test_reschedules_existing_appointment(tmp_path):
    assistant = _assistant(tmp_path)

    booked = assistant.chat(
        "session-1",
        "Book appointment name: Priya phone +91 99999 99999 for consultation with doctor one at 2026-06-08T09:00",
    )
    response = assistant.chat(
        "session-1",
        f"Reschedule appointment: {booked.appointment['id']} to 2026-06-08T10:00",
    )

    assert response.message == "rescheduled"
    assert response.appointment["starts_at"] == "2026-06-08T10:00:00"
    assert response.appointment["status"] == "rescheduled"


def test_cancels_existing_appointment(tmp_path):
    assistant = _assistant(tmp_path)

    booked = assistant.chat(
        "session-1",
        "Book appointment name: Priya phone +91 99999 99999 for consultation with doctor one at 2026-06-08T09:00",
    )
    response = assistant.chat(
        "session-1",
        f"Cancel appointment: {booked.appointment['id']}",
    )

    assert response.message == "cancelled"
    assert response.appointment["status"] == "cancelled"


def test_persists_conversation_history(tmp_path):
    assistant = _assistant(tmp_path)

    assistant.chat("session-1", "Show appointments")

    history = assistant.repository.conversation_history("session-1", limit=10)
    assert [message["role"] for message in history] == ["user", "assistant"]


def test_openai_planner_parses_tool_call(monkeypatch):
    class FakeFunction:
        name = "check_availability"
        arguments = '{"service_id": "consultation", "provider_id": "doctor_one", "starts_at": "2026-06-08T09:00:00"}'

    class FakeToolCall:
        function = FakeFunction()

    class FakeMessage:
        tool_calls = [FakeToolCall()]

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletion:
        choices = [FakeChoice()]

    class FakeClient:
        def __init__(self, api_key: str):
            self.api_key = api_key

        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            return FakeCompletion()

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeClient)

    settings = Settings(
        app_config_path=".env",
        database_url="sqlite:///:memory:",
        llm_provider="openai",
        openai_api_key="x",
        openai_model="gpt-4o-mini",
        reminder_lead_minutes=1440,
    )
    planner = OpenAIActionPlanner(settings)
    config = _config()
    action = planner.plan("Is doctor one available?", [], config)

    assert action.action == "check_availability"
    assert action.service_id == "consultation"
    assert action.provider_id == "doctor_one"
    assert action.starts_at.isoformat() == "2026-06-08T09:00:00"


def _assistant(tmp_path) -> BookingAssistant:
    config = _config()
    database = Database(f"sqlite:///{tmp_path / 'clinic.db'}")
    database.seed(config)
    return BookingAssistant(
        config=config,
        repository=ClinicRepository(database, reminder_lead_minutes=1440),
        planner=RuleBasedActionPlanner(),
    )


def _config() -> ClinicConfig:
    return ClinicConfig(
        clinic=ClinicInfo(name="Clinic", timezone="Asia/Kolkata"),
        booking=BookingConfig(
            slot_duration_minutes=30,
            required_fields=[
                "patient_name",
                "patient_phone",
                "service_id",
                "provider_id",
                "starts_at",
            ],
        ),
        services=[
            ServiceConfig(id="consultation", name="consultation", duration_minutes=30),
        ],
        providers=[
            ProviderConfig(
                id="doctor_one",
                name="doctor one",
                service_ids=["consultation"],
                working_hours={
                    "monday": [TimeRange(start="09:00", end="17:00")],
                },
            ),
        ],
        messages=MessageConfig(
            welcome="welcome",
            missing_patient_name="missing name",
            missing_patient_phone="missing phone",
            missing_service_id="missing service",
            missing_provider_id="missing provider",
            missing_starts_at="missing time",
            invalid_service_id="invalid service",
            invalid_provider_id="invalid provider",
            invalid_starts_at="invalid time",
            booking_confirmed="confirmed",
            appointment_rescheduled="rescheduled",
            booking_cancelled="cancelled",
            booking_not_found="not found",
            availability_found="available",
            availability_not_found="not available",
            appointments_listed="appointments listed",
            fallback="fallback",
        ),
        llm=LlmConfig(system_prompt="prompt"),
    )
