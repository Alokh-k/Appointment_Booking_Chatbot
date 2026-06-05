from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class TimeRange(BaseModel):
    start: str
    end: str


class ProviderConfig(BaseModel):
    id: str
    name: str
    service_ids: list[str]
    working_hours: dict[str, list[TimeRange]]


class ServiceConfig(BaseModel):
    id: str
    name: str
    duration_minutes: int = Field(gt=0)


class ClinicInfo(BaseModel):
    name: str
    timezone: str


class BookingConfig(BaseModel):
    slot_duration_minutes: int = Field(gt=0)
    required_fields: list[str]


class MessageConfig(BaseModel):
    welcome: str
    missing_patient_name: str
    missing_patient_phone: str
    missing_service_id: str
    missing_provider_id: str
    missing_starts_at: str
    invalid_service_id: str
    invalid_provider_id: str
    invalid_starts_at: str
    booking_confirmed: str
    appointment_rescheduled: str
    booking_cancelled: str
    booking_not_found: str
    availability_found: str
    availability_not_found: str
    appointments_listed: str
    fallback: str


class LlmConfig(BaseModel):
    system_prompt: str


class ClinicConfig(BaseModel):
    clinic: ClinicInfo
    booking: BookingConfig
    services: list[ServiceConfig]
    providers: list[ProviderConfig]
    messages: MessageConfig
    llm: LlmConfig


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    message: str
    appointment: Optional[dict[str, Any]] = None
    data: Optional[dict[str, Any]] = None
    missing_fields: list[str] = Field(default_factory=list)


class Appointment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    patient_name: str
    patient_phone: str
    service_id: str
    provider_id: str
    starts_at: datetime
    ends_at: datetime
    status: Literal["confirmed", "cancelled", "rescheduled"] = "confirmed"
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("patient_name", "patient_phone", "service_id", "provider_id")
    @classmethod
    def not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value cannot be blank")
        return stripped


class StructuredAction(BaseModel):
    action: Literal["book", "reschedule", "cancel", "list", "check_availability", "unknown"] = "unknown"
    patient_name: Optional[str] = None
    patient_phone: Optional[str] = None
    service_id: Optional[str] = None
    provider_id: Optional[str] = None
    starts_at: Optional[datetime] = None
    new_starts_at: Optional[datetime] = None
    appointment_id: Optional[str] = None


class SessionState(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)
