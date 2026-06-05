from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.models import ClinicConfig
from app.settings import get_settings


@lru_cache
def load_clinic_config() -> ClinicConfig:
    settings = get_settings()
    config_path = Path(settings.app_config_path)
    with config_path.open() as config_file:
        payload = json.load(config_file)
    return ClinicConfig.model_validate(payload)
