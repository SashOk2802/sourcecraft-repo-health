"""Генерация SVG-бейджей здоровья репозитория для README."""

from __future__ import annotations

import xml.sax.saxutils

# Цветовая палитра шкалы Score в соответствии с методологией
_COLOR_SUCCESS = "#2ea44f"  # 80-100 (high)
_COLOR_WARNING = "#dfb317"  # 60-79 (mid)
_COLOR_DANGER = "#cb2431"   # 0-59 (low)
_COLOR_PRELIMINARY = "#0969da"  # Предварительная оценка
_COLOR_UNKNOWN = "#6a737d"  # Нет данных / неизвестно


def _estimate_text_width(text: str) -> int:
    """Приблизительная ширина текста при шрифте 11px Verdana/DejaVu.

    Эвристика оптимизирована для латинских символов (стиль Shields.io).
    Не-ASCII символы (например, кириллица) имеют более широкие глифы (~9-11px),
    поэтому для них используется ширина 10px. Для кастомных label рекомендуется
    использовать латиницу.
    """
    width = 0
    for char in text:
        if ord(char) > 127:
            width += 10
        elif char in "mwMW":
            width += 9
        elif char in "ijltI.: ":
            width += 4
        elif char.isupper() or char.isdigit() or char in "/-%":
            width += 7
        else:
            width += 6
    return max(width, 10)


def render_score_badge(
    score: int | None = None,
    *,
    is_preliminary: bool = False,
    label: str = "repo health",
    value_override: str | None = None,
    color_override: str | None = None,
) -> str:
    """Создаёт SVG-бейдж в формате Shields.io для встраивания в README."""

    if value_override is not None:
        value = value_override
        color = color_override or _COLOR_UNKNOWN
    elif is_preliminary:
        value = "preliminary"
        color = _COLOR_PRELIMINARY
    elif score is not None:
        value = f"{score}/100"
        if score >= 80:
            color = _COLOR_SUCCESS
        elif score >= 60:
            color = _COLOR_WARNING
        else:
            color = _COLOR_DANGER
    else:
        value = "no data"
        color = _COLOR_UNKNOWN

    escaped_label = xml.sax.saxutils.escape(label)
    escaped_value = xml.sax.saxutils.escape(value)

    label_width = _estimate_text_width(label) + 12
    value_width = _estimate_text_width(value) + 12
    total_width = label_width + value_width

    label_x = label_width / 2
    value_x = label_width + (value_width / 2)

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20" '
        f'role="img" aria-label="{escaped_label}: {escaped_value}">\n'
        f'  <linearGradient id="s" x2="0" y2="100%">\n'
        f'    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>\n'
        f'    <stop offset="100%" stop-opacity=".1"/>\n'
        f'  </linearGradient>\n'
        f'  <clipPath id="r">\n'
        f'    <rect width="{total_width}" height="20" rx="3" fill="#fff"/>\n'
        f'  </clipPath>\n'
        f'  <g clip-path="url(#r)">\n'
        f'    <rect width="{label_width}" height="20" fill="#555"/>\n'
        f'    <rect x="{label_width}" width="{value_width}" height="20" fill="{color}"/>\n'
        f'    <rect width="{total_width}" height="20" fill="url(#s)"/>\n'
        f'  </g>\n'
        f'  <g fill="#fff" text-anchor="middle" '
        f'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" '
        f'font-size="110">\n'
        f'    <text aria-hidden="true" x="{int(label_x * 10)}" y="150" fill="#010101" fill-opacity=".3" '
        f'transform="scale(.1)" textLength="{int((label_width - 12) * 10)}">{escaped_label}</text>\n'
        f'    <text x="{int(label_x * 10)}" y="140" transform="scale(.1)" '
        f'fill="#fff" textLength="{int((label_width - 12) * 10)}">{escaped_label}</text>\n'
        f'    <text aria-hidden="true" x="{int(value_x * 10)}" y="150" fill="#010101" fill-opacity=".3" '
        f'transform="scale(.1)" textLength="{int((value_width - 12) * 10)}">{escaped_value}</text>\n'
        f'    <text x="{int(value_x * 10)}" y="140" transform="scale(.1)" '
        f'fill="#fff" textLength="{int((value_width - 12) * 10)}">{escaped_value}</text>\n'
        f'  </g>\n'
        f'</svg>'
    )
