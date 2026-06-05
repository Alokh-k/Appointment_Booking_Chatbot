from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Optional

from app.database import BookingConflictError, ClinicRepository
from app.llm import ActionPlanner
from app.models import Appointment, ChatResponse, ClinicConfig, StructuredAction


class BookingAssistant:
    def __init__(self, config: ClinicConfig, repository: ClinicRepository, planner: ActionPlanner):
        self.config = config
        self.repository = repository
        self.planner = planner

    def chat(self, session_id: str, message: str) -> ChatResponse:
        self.repository.add_conversation_message(session_id, "user", message)
        history = self.repository.conversation_history(session_id, limit=12)
        action = self.planner.plan(message, history, self.config)
        response = self._execute(session_id, action)
        self.repository.add_conversation_message(session_id, "assistant", response.message)
        return response

    def _execute(self, session_id: str, action: StructuredAction) -> ChatResponse:
        if action.action == "book":
            return self._book(session_id, action)
        if action.action == "reschedule":
            return self._reschedule(session_id, action)
        if action.action == "cancel":
            return self._cancel(session_id, action)
        if action.action == "check_availability":
            return self._check_availability(session_id, action)
        if action.action == "list":
            return self._list(session_id)
        return ChatResponse(session_id=session_id, message=self.config.messages.fallback)

    def _book(self, session_id: str, action: StructuredAction) -> ChatResponse:
        missing_fields = self._missing_fields(action, self.config.booking.required_fields)
        if missing_fields:
            return ChatResponse(
                session_id=session_id,
                message=self._missing_message(missing_fields[0]),
                missing_fields=missing_fields,
            )

        invalid_message = self._validate_action(action)
        if invalid_message:
            return ChatResponse(session_id=session_id, message=invalid_message)

        starts_at = action.starts_at or datetime.utcnow()
        ends_at = self._appointment_end(action.service_id or "", starts_at)
        appointment = Appointment(
            patient_name=action.patient_name or "",
            patient_phone=action.patient_phone or "",
            service_id=action.service_id or "",
            provider_id=action.provider_id or "",
            starts_at=starts_at,
            ends_at=ends_at,
        )
        if not self._is_available(appointment.service_id, appointment.provider_id, appointment.starts_at, appointment.ends_at):
            return ChatResponse(session_id=session_id, message=self.config.messages.invalid_starts_at)

        try:
            saved = self.repository.create_appointment(appointment)
        except BookingConflictError:
            return ChatResponse(session_id=session_id, message=self.config.messages.invalid_starts_at)

        return ChatResponse(
            session_id=session_id,
            message=self.config.messages.booking_confirmed,
            appointment=saved.model_dump(mode="json"),
        )

    def _reschedule(self, session_id: str, action: StructuredAction) -> ChatResponse:
        if not action.appointment_id:
            return ChatResponse(session_id=session_id, message=self.config.messages.booking_not_found)
        if not action.new_starts_at:
            return ChatResponse(
                session_id=session_id,
                message=self._missing_message("starts_at"),
                missing_fields=["new_starts_at"],
            )

        appointment = self.repository.get_appointment(action.appointment_id)
        if not appointment:
            return ChatResponse(session_id=session_id, message=self.config.messages.booking_not_found)

        ends_at = self._appointment_end(action.service_id or appointment.service_id, action.new_starts_at)
        if not self._is_available(appointment.service_id, appointment.provider_id, action.new_starts_at, ends_at):
            return ChatResponse(session_id=session_id, message=self.config.messages.invalid_starts_at)

        try:
            updated = self.repository.reschedule_appointment(action.appointment_id, action.new_starts_at, ends_at)
        except BookingConflictError:
            return ChatResponse(session_id=session_id, message=self.config.messages.invalid_starts_at)

        if not updated:
            return ChatResponse(session_id=session_id, message=self.config.messages.booking_not_found)
        return ChatResponse(
            session_id=session_id,
            message=self.config.messages.appointment_rescheduled,
            appointment=updated.model_dump(mode="json"),
        )

    def _cancel(self, session_id: str, action: StructuredAction) -> ChatResponse:
        if not action.appointment_id:
            return ChatResponse(session_id=session_id, message=self.config.messages.booking_not_found)
        cancelled = self.repository.cancel_appointment(action.appointment_id)
        if not cancelled:
            return ChatResponse(session_id=session_id, message=self.config.messages.booking_not_found)
        return ChatResponse(
            session_id=session_id,
            message=self.config.messages.booking_cancelled,
            appointment=cancelled.model_dump(mode="json"),
        )

    def _check_availability(self, session_id: str, action: StructuredAction) -> ChatResponse:
        missing_fields = self._missing_fields(action, ["service_id", "provider_id", "starts_at"])
        if missing_fields:
            return ChatResponse(
                session_id=session_id,
                message=self._missing_message(missing_fields[0]),
                missing_fields=missing_fields,
            )
        invalid_message = self._validate_action(action)
        if invalid_message:
            return ChatResponse(session_id=session_id, message=invalid_message)

        ends_at = self._appointment_end(action.service_id or "", action.starts_at or datetime.utcnow())
        available = self._is_available(action.service_id or "", action.provider_id or "", action.starts_at or datetime.utcnow(), ends_at)
        return ChatResponse(
            session_id=session_id,
            message=self.config.messages.availability_found if available else self.config.messages.availability_not_found,
            data={"available": available},
        )

    def _list(self, session_id: str) -> ChatResponse:
        appointments = [
            appointment.model_dump(mode="json")
            for appointment in self.repository.list_appointments()
        ]
        return ChatResponse(
            session_id=session_id,
            message=self.config.messages.appointments_listed,
            data={"appointments": appointments},
        )

    def _missing_fields(self, action: StructuredAction, fields: list[str]) -> list[str]:
        payload = action.model_dump()
        return [field for field in fields if not payload.get(field)]

    def _appointment_end(self, service_id: str, starts_at: datetime) -> datetime:
        service = self._service(service_id)
        duration = service.duration_minutes if service else 0
        return starts_at + timedelta(minutes=duration)

    def _validate_action(self, action: StructuredAction) -> Optional[str]:
        if action.service_id and action.service_id not in {service.id for service in self.config.services}:
            return self.config.messages.invalid_service_id

        provider = self._provider(action.provider_id or "")
        if action.provider_id and not provider:
            return self.config.messages.invalid_provider_id

        if action.service_id and provider and action.service_id not in provider.service_ids:
            return self.config.messages.invalid_provider_id

        return None

    def _service(self, service_id: str):
        return next(
            (service for service in self.config.services if service.id == service_id),
            None,
        )

    def _missing_message(self, field: str) -> str:
        normalized = "starts_at" if field == "new_starts_at" else field
        return getattr(self.config.messages, f"missing_{normalized}")

    def _is_available(self, service_id: str, provider_id: str, starts_at: datetime, ends_at: datetime) -> bool:
        provider = self._provider(provider_id)
        service = self._service(service_id)
        if not provider or not service or service_id not in provider.service_ids:
            return False

        weekday = starts_at.strftime("%A").lower()
        ranges = provider.working_hours.get(weekday, [])
        inside_working_hours = any(
            self._parse_time(time_range.start) <= starts_at.time()
            and ends_at.time() <= self._parse_time(time_range.end)
            and starts_at.date() == ends_at.date()
            for time_range in ranges
        )
        return inside_working_hours and not self.repository.has_conflict(provider_id, starts_at, ends_at)

    def _provider(self, provider_id: str):
        return next(
            (provider for provider in self.config.providers if provider.id == provider_id),
            None,
        )

    def _parse_time(self, value: str) -> time:
        return datetime.strptime(value, "%H:%M").time()
