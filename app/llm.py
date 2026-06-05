from __future__ import annotations

import json
import re
from typing import Protocol

from app.models import ClinicConfig, StructuredAction
from app.settings import Settings


class ActionPlanner(Protocol):
    def plan(self, message: str, history: list[dict[str, str]], config: ClinicConfig) -> StructuredAction:
        ...


class OpenAIActionPlanner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.fallback = RuleBasedActionPlanner()

    def plan(self, message: str, history: list[dict[str, str]], config: ClinicConfig) -> StructuredAction:
        if not self.settings.openai_api_key or not self.settings.openai_model:
            return self.fallback.plan(message, history, config)

        from openai import OpenAI

        client = OpenAI(api_key=self.settings.openai_api_key)
        completion = client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[
                {"role": "system", "content": config.llm.system_prompt},
                *history,
                {"role": "user", "content": self._contextual_message(message, config)},
            ],
            tools=self._tools(),
            tool_choice="auto",
        )
        tool_calls = completion.choices[0].message.tool_calls or []
        if not tool_calls:
            return StructuredAction(action="unknown")
        tool_call = tool_calls[0]
        arguments = json.loads(tool_call.function.arguments or "{}")
        return StructuredAction.model_validate({"action": tool_call.function.name, **arguments})

    def _contextual_message(self, message: str, config: ClinicConfig) -> str:
        return json.dumps(
            {
                "message": message,
                "services": [service.model_dump() for service in config.services],
                "doctors": [provider.model_dump() for provider in config.providers],
            }
        )

    def _tools(self) -> list[dict]:
        patient_fields = {
            "patient_name": {"type": "string"},
            "patient_phone": {"type": "string"},
            "service_id": {"type": "string"},
            "provider_id": {"type": "string"},
            "starts_at": {"type": "string", "description": "ISO 8601 appointment start time"},
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": "book",
                    "description": "Book a new clinic appointment.",
                    "parameters": {
                        "type": "object",
                        "properties": patient_fields,
                        "required": list(patient_fields.keys()),
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "reschedule",
                    "description": "Move an existing appointment to a new start time.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "appointment_id": {"type": "string"},
                            "new_starts_at": {"type": "string", "description": "ISO 8601 new start time"},
                        },
                        "required": ["appointment_id", "new_starts_at"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "cancel",
                    "description": "Cancel an existing appointment.",
                    "parameters": {
                        "type": "object",
                        "properties": {"appointment_id": {"type": "string"}},
                        "required": ["appointment_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_availability",
                    "description": "Check whether a doctor has an available appointment time.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service_id": {"type": "string"},
                            "provider_id": {"type": "string"},
                            "starts_at": {"type": "string", "description": "ISO 8601 appointment start time"},
                        },
                        "required": ["service_id", "provider_id", "starts_at"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list",
                    "description": "List confirmed appointments.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]


class RuleBasedActionPlanner:
    def plan(self, message: str, history: list[dict[str, str]], config: ClinicConfig) -> StructuredAction:
        normalized = message.lower()
        action = "unknown"
        if self._contains_any(normalized, ["reschedule", "move", "change"]):
            action = "reschedule"
        elif self._contains_any(normalized, ["cancel"]):
            action = "cancel"
        elif self._contains_any(normalized, ["available", "availability", "free"]):
            action = "check_availability"
        elif self._contains_any(normalized, ["list", "show", "appointments"]):
            action = "list"
        elif self._contains_any(normalized, ["book", "appointment", "schedule"]):
            action = "book"

        return StructuredAction(
            action=action,
            patient_name=self._match_value(message, "name"),
            patient_phone=self._match_phone(message),
            service_id=self._match_option(normalized, [(service.id, service.name) for service in config.services]),
            provider_id=self._match_option(normalized, [(provider.id, provider.name) for provider in config.providers]),
            starts_at=self._match_iso_datetime(message),
            new_starts_at=self._match_iso_datetime(message),
            appointment_id=self._match_value(message, "appointment"),
        )

    def _contains_any(self, text: str, terms: list[str]) -> bool:
        return any(term in text for term in terms)

    def _match_option(self, text: str, options: list[tuple[str, str]]) -> str | None:
        return next(
            (
                option_id
                for option_id, option_name in options
                if option_id.lower() in text or option_name.lower() in text
            ),
            None,
        )

    def _match_value(self, text: str, label: str) -> str | None:
        boundary = r"(?=\s+(?:name|phone|service|provider|appointment|for|with|at|to)\b|$)"
        match = re.search(rf"{label}\s*[:=]\s*(.+?){boundary}", text, re.IGNORECASE)
        return match.group(1).strip() if match else None

    def _match_phone(self, text: str) -> str | None:
        match = re.search(r"(\+?\d[\d -]{7,}\d)", text)
        return match.group(1).strip() if match else None

    def _match_iso_datetime(self, text: str):
        from datetime import datetime

        matches = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", text)
        if not matches:
            return None
        return datetime.fromisoformat(matches[-1])


def build_planner(settings: Settings) -> ActionPlanner:
    if settings.llm_provider.lower() == "openai":
        return OpenAIActionPlanner(settings)
    return RuleBasedActionPlanner()
