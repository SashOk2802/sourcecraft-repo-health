"""Вспомогательные функции для безопасного чтения настроек из переменных окружения."""

from __future__ import annotations

import math
import os


def env_int(name: str, default: int) -> int:
    """Возвращает положительное целое число из переменной окружения или default."""
    raw = os.environ.get(name, "").strip()
    if raw.isdigit():
        value = int(raw)
        if value > 0:
            return value
    return default


def env_float(name: str, default: float) -> float:
    """Возвращает положительное конечное число с плавающей точкой из переменной окружения или default."""
    raw = os.environ.get(name, "").strip()
    try:
        val = float(raw)
        if math.isfinite(val) and val > 0:
            return val
    except ValueError:
        pass
    return default
