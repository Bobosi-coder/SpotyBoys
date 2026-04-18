from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from typing import Any


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "dict"):
        return to_jsonable(value.dict())
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=True, sort_keys=True)

