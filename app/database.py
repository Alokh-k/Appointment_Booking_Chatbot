from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from app.models import Appointment, ClinicConfig


class Database:
    def __init__(self, database_url: str):
        self.path = self._sqlite_path(database_url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS doctors (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS services (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0)
                );

                CREATE TABLE IF NOT EXISTS doctor_services (
                    doctor_id TEXT NOT NULL REFERENCES doctors(id),
                    service_id TEXT NOT NULL REFERENCES services(id),
                    PRIMARY KEY (doctor_id, service_id)
                );

                CREATE TABLE IF NOT EXISTS appointments (
                    id TEXT PRIMARY KEY,
                    patient_name TEXT NOT NULL,
                    patient_phone TEXT NOT NULL,
                    service_id TEXT NOT NULL REFERENCES services(id),
                    provider_id TEXT NOT NULL REFERENCES doctors(id),
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS unique_active_slot
                ON appointments(provider_id, starts_at)
                WHERE status != 'cancelled';

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    appointment_id TEXT NOT NULL REFERENCES appointments(id),
                    remind_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def seed(self, config: ClinicConfig) -> None:
        with self.connection() as connection:
            for service in config.services:
                connection.execute(
                    "INSERT OR IGNORE INTO services(id, name, duration_minutes) VALUES (?, ?, ?)",
                    (service.id, service.name, service.duration_minutes),
                )
            for provider in config.providers:
                connection.execute(
                    "INSERT OR IGNORE INTO doctors(id, name) VALUES (?, ?)",
                    (provider.id, provider.name),
                )
                for service_id in provider.service_ids:
                    connection.execute(
                        "INSERT OR IGNORE INTO doctor_services(doctor_id, service_id) VALUES (?, ?)",
                        (provider.id, service_id),
                    )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _sqlite_path(self, database_url: str) -> Path:
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ValueError("Only sqlite:/// database URLs are supported by this implementation")
        return Path(database_url[len(prefix) :])


class ClinicRepository:
    def __init__(self, database: Database, reminder_lead_minutes: int):
        self.database = database
        self.reminder_lead_minutes = reminder_lead_minutes

    def create_appointment(self, appointment: Appointment) -> Appointment:
        with self.database.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO appointments(
                        id, patient_name, patient_phone, service_id, provider_id,
                        starts_at, ends_at, status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._appointment_params(appointment),
                )
            except sqlite3.IntegrityError as error:
                raise BookingConflictError(str(error)) from error
            self._create_reminder(connection, appointment)
        return appointment

    def reschedule_appointment(self, appointment_id: str, starts_at: datetime, ends_at: datetime) -> Optional[Appointment]:
        appointment = self.get_appointment(appointment_id)
        if not appointment or appointment.status == "cancelled":
            return None

        updated = appointment.model_copy(update={"starts_at": starts_at, "ends_at": ends_at, "status": "rescheduled"})
        with self.database.connection() as connection:
            try:
                connection.execute(
                    "UPDATE appointments SET starts_at = ?, ends_at = ?, status = ? WHERE id = ? AND status != 'cancelled'",
                    (starts_at.isoformat(), ends_at.isoformat(), "rescheduled", appointment_id),
                )
            except sqlite3.IntegrityError as error:
                raise BookingConflictError(str(error)) from error
            connection.execute(
                "UPDATE reminders SET status = ? WHERE appointment_id = ? AND status = ?",
                ("cancelled", appointment_id, "scheduled"),
            )
            self._create_reminder(connection, updated)
        return updated

    def cancel_appointment(self, appointment_id: str) -> Optional[Appointment]:
        appointment = self.get_appointment(appointment_id)
        if not appointment or appointment.status == "cancelled":
            return None
        cancelled = appointment.model_copy(update={"status": "cancelled"})
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE appointments SET status = ? WHERE id = ? AND status != ?",
                ("cancelled", appointment_id, "cancelled"),
            )
            connection.execute(
                "UPDATE reminders SET status = ? WHERE appointment_id = ? AND status = ?",
                ("cancelled", appointment_id, "scheduled"),
            )
        return cancelled

    def get_appointment(self, appointment_id: str) -> Optional[Appointment]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM appointments WHERE id = ?",
                (appointment_id,),
            ).fetchone()
        return self._appointment_from_row(row) if row else None

    def list_appointments(self, session_id: Optional[str] = None) -> list[Appointment]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM appointments WHERE status != ? ORDER BY starts_at",
                ("cancelled",),
            ).fetchall()
        return [self._appointment_from_row(row) for row in rows]

    def has_conflict(self, provider_id: str, starts_at: datetime, ends_at: datetime) -> bool:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM appointments
                WHERE provider_id = ? AND status != ?
                  AND NOT (ends_at <= ? OR starts_at >= ?)
                LIMIT 1
                """,
                (provider_id, "cancelled", starts_at.isoformat(), ends_at.isoformat()),
            ).fetchone()
        return row is not None

    def add_conversation_message(self, session_id: str, role: str, content: str) -> None:
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO conversation_messages(session_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, role, content, datetime.utcnow().isoformat()),
            )

    def conversation_history(self, session_id: str, limit: int) -> list[dict[str, str]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT role, content FROM conversation_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def due_reminders(self, now: datetime) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT reminders.id, reminders.remind_at, appointments.*
                FROM reminders
                JOIN appointments ON appointments.id = reminders.appointment_id
                WHERE reminders.status = ? AND reminders.remind_at <= ?
                ORDER BY reminders.remind_at
                """,
                ("scheduled", now.isoformat()),
            ).fetchall()
        return [dict(row) for row in rows]

    def process_due_reminders(self, now: datetime | None = None) -> list[dict[str, Any]]:
        if now is None:
            now = datetime.utcnow()
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT reminders.id, reminders.remind_at, appointments.*
                FROM reminders
                JOIN appointments ON appointments.id = reminders.appointment_id
                WHERE reminders.status = ? AND reminders.remind_at <= ?
                ORDER BY reminders.remind_at
                """,
                ("scheduled", now.isoformat()),
            ).fetchall()
            if rows:
                connection.execute(
                    "UPDATE reminders SET status = ? WHERE status = ? AND remind_at <= ?",
                    ("sent", "scheduled", now.isoformat()),
                )
        return [dict(row) for row in rows]

    def _create_reminder(self, connection: sqlite3.Connection, appointment: Appointment) -> None:
        remind_at = appointment.starts_at - timedelta(minutes=self.reminder_lead_minutes)
        connection.execute(
            """
            INSERT INTO reminders(appointment_id, remind_at, status, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (appointment.id, remind_at.isoformat(), "scheduled", datetime.utcnow().isoformat()),
        )

    def _appointment_params(self, appointment: Appointment) -> tuple[str, ...]:
        return (
            appointment.id,
            appointment.patient_name,
            appointment.patient_phone,
            appointment.service_id,
            appointment.provider_id,
            appointment.starts_at.isoformat(),
            appointment.ends_at.isoformat(),
            appointment.status,
            appointment.created_at.isoformat(),
        )

    def _appointment_from_row(self, row: sqlite3.Row) -> Appointment:
        return Appointment(
            id=row["id"],
            patient_name=row["patient_name"],
            patient_phone=row["patient_phone"],
            service_id=row["service_id"],
            provider_id=row["provider_id"],
            starts_at=datetime.fromisoformat(row["starts_at"]),
            ends_at=datetime.fromisoformat(row["ends_at"]),
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class BookingConflictError(Exception):
    pass
