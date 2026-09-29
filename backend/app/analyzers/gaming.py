"""Продвинутая защита от накрутки рейтинга (бонус со звёздочкой).

Детектор смотрит на уже собранные факты Activity: метки времени коммитов,
смерженные MR и релизы. Он не меняет оценку категории, Repo Health Score,
coverage и место в рейтинге. Потолки Activity (3 MR, 3 релиза) — устойчивость
формулы; здесь сырой объём лишь помечается как подозрительный, если он
существенно выше потолка.

Лайки и реакции в детектор не входят: поштучных данных по реакциям нет,
а агрегат rating не является входом Score и не подменяет проверку накрутки.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.app.analyzers.activity import (
    MERGED_MR_CAP,
    RELEASE_CAP,
    ActivityFacts,
)
from backend.app.contracts import AnalysisContext, DataStatus

# Окно «очень короткого» всплеска относительно 180-дневного периода.
BURST_WINDOW = timedelta(hours=1)
# Минимум коммитов в окне, чтобы говорить о накрутке, а не о паре правок подряд.
BURST_MIN_COMMITS = 15
# Плотность всплеска относительно остального периода (коммитов в час).
BURST_RATE_RATIO = 20.0

# Сырой объём «далеко» за потолком Activity: 3 × 5 = 15.
VOLUME_PADDING_MULTIPLIER = 5
MR_VOLUME_FLAG_THRESHOLD = MERGED_MR_CAP * VOLUME_PADDING_MULTIPLIER
RELEASE_VOLUME_FLAG_THRESHOLD = RELEASE_CAP * VOLUME_PADDING_MULTIPLIER

WARNING_LABEL = "аномальная активность"
# Один сигнал (15 MR, 15 релизов или плотный час) для активного проекта обычен.
# Публичная пометка появляется только при двух независимых сигналах сразу.
MIN_INDEPENDENT_FLAGS = 2

_SIGNAL_BURST = "burst"
_SIGNAL_MR_VOLUME = "mr_volume"
_SIGNAL_RELEASE_VOLUME = "release_volume"

_recorded_detection: ContextVar[GamingDetectionResult | None] = ContextVar(
    "gaming_detection",
    default=None,
)


@dataclass(frozen=True, slots=True)
class GamingSignal:
    """Один честный сигнал: измерен, неприменим или данных нет."""

    code: str
    status: DataStatus
    summary: str
    flagged: bool = False
    detail: str | None = None
    value: int | None = None

    def __post_init__(self) -> None:
        if self.status is DataStatus.MEASURED and self.value is None:
            raise ValueError("a measured gaming signal requires a value")
        if self.status is not DataStatus.MEASURED and self.value is not None:
            raise ValueError("only a measured gaming signal may have a value")
        if self.flagged and self.status is not DataStatus.MEASURED:
            raise ValueError("only a measured gaming signal may be flagged")


@dataclass(frozen=True, slots=True)
class GamingDetectionResult:
    """Сводка сигналов. ``suspected`` — есть хотя бы один измеренный флаг."""

    signals: tuple[GamingSignal, ...]
    summary: str

    @property
    def suspected(self) -> bool:
        flagged = sum(1 for signal in self.signals if signal.flagged)
        return flagged >= MIN_INDEPENDENT_FLAGS

    @property
    def label(self) -> str | None:
        return WARNING_LABEL if self.suspected else None


def record_detection(result: GamingDetectionResult) -> None:
    """Сохраняет результат для текущего запуска анализа (см. ``take_recorded_detection``)."""

    _recorded_detection.set(result)


def take_recorded_detection() -> GamingDetectionResult | None:
    """Забирает и сбрасывает результат, записанный провайдером Activity."""

    result = _recorded_detection.get()
    _recorded_detection.set(None)
    return result


def detect(facts: ActivityFacts, context: AnalysisContext) -> GamingDetectionResult:
    """Чистая функция: факты Activity → сигналы накрутки. Без сети и без лайков."""

    signals = (
        _burst_signal(facts, context),
        _merged_mr_volume_signal(facts, context),
        _release_volume_signal(facts, context),
    )
    flagged = tuple(signal for signal in signals if signal.flagged)
    if len(flagged) >= MIN_INDEPENDENT_FLAGS:
        summary = (
            "Совпали независимые сигналы необычной активности: "
            + "; ".join(signal.summary for signal in flagged)
            + ". Score не снижен: это наблюдение, а не штраф."
        )
    elif flagged:
        summary = (
            "Один сигнал необычной активности сам по себе не помечает репозиторий: "
            "для активного проекта такой объём бывает обычным. "
            + flagged[0].summary
        )
    else:
        unavailable = tuple(
            signal for signal in signals if signal.status is DataStatus.UNAVAILABLE
        )
        if unavailable and not any(signal.status is DataStatus.MEASURED for signal in signals):
            summary = (
                "Проверка необычной активности не завершена: не хватает исходных данных. "
                "Отсутствие данных не означает, что активность обычная."
            )
        elif unavailable:
            summary = (
                "Измеренные сигналы необычной активности не совпали. "
                "Часть источников недоступна — отсутствие данных не означает, "
                "что активность обычная."
            )
        else:
            summary = (
                "Измеренные сигналы необычной активности не совпали. "
                "Это не доказательство обычной активности по недоступным источникам."
            )
    return GamingDetectionResult(signals=signals, summary=summary)


def _in_period(moment: datetime, context: AnalysisContext) -> bool:
    return context.period_start <= moment <= context.period_end


def _burst_signal(facts: ActivityFacts, context: AnalysisContext) -> GamingSignal:
    history = facts.commit_history
    if not history.collected:
        return GamingSignal(
            code=_SIGNAL_BURST,
            status=DataStatus.UNAVAILABLE,
            summary="История коммитов не запрашивалась — всплеск проверить нельзя.",
            detail="commit_history_not_collected",
        )
    if history.error is not None:
        return GamingSignal(
            code=_SIGNAL_BURST,
            status=DataStatus.UNAVAILABLE,
            summary="История коммитов недоступна — всплеск проверить нельзя.",
            detail=history.error,
        )
    if history.truncated:
        return GamingSignal(
            code=_SIGNAL_BURST,
            status=DataStatus.UNAVAILABLE,
            summary="История коммитов прочитана не полностью — всплеск проверить нельзя.",
            detail="commit_history_truncated",
        )

    moments = tuple(
        moment.astimezone(UTC)
        for moment in history.committed_at
        if _in_period(moment, context)
    )
    if not moments:
        return GamingSignal(
            code=_SIGNAL_BURST,
            status=DataStatus.NOT_APPLICABLE,
            summary="За период нет коммитов — искать всплеск не на чем.",
        )

    densest = _densest_window_count(moments, BURST_WINDOW)
    total = len(moments)
    period_seconds = max((context.period_end - context.period_start).total_seconds(), 1.0)
    window_seconds = max(BURST_WINDOW.total_seconds(), 1.0)
    rest = total - densest
    rest_seconds = max(period_seconds - window_seconds, 1.0)
    densest_rate = densest / window_seconds
    rest_rate = rest / rest_seconds
    unusual = densest >= BURST_MIN_COMMITS and (
        rest == 0 or densest_rate >= BURST_RATE_RATIO * rest_rate
    )
    summary = (
        f"в окне {int(window_seconds // 3600)} ч. сосредоточено {densest} "
        f"из {total} коммитов периода"
    )
    if unusual:
        return GamingSignal(
            code=_SIGNAL_BURST,
            status=DataStatus.MEASURED,
            summary=f"Необычно плотный всплеск коммитов: {summary}.",
            flagged=True,
            detail=(
                f"порог {BURST_MIN_COMMITS} коммитов за {BURST_WINDOW}; "
                f"отношение плотностей ≥ {BURST_RATE_RATIO:g}"
            ),
            value=densest,
        )
    return GamingSignal(
        code=_SIGNAL_BURST,
        status=DataStatus.MEASURED,
        summary=f"Плотность коммитов в коротком окне в пределах обычной: {summary}.",
        flagged=False,
        value=densest,
    )


def _densest_window_count(moments: tuple[datetime, ...], window: timedelta) -> int:
    ordered = sorted(moments)
    if not ordered:
        return 0
    best = 1
    left = 0
    for right, moment in enumerate(ordered):
        while moment - ordered[left] > window:
            left += 1
        best = max(best, right - left + 1)
    return best


def _merged_mr_volume_signal(facts: ActivityFacts, context: AnalysisContext) -> GamingSignal:
    if facts.pulls_error:
        return GamingSignal(
            code=_SIGNAL_MR_VOLUME,
            status=DataStatus.UNAVAILABLE,
            summary="Список MR недоступен — избыточный объём merge проверить нельзя.",
            detail=facts.pulls_error,
        )
    if facts.pulls_truncated:
        return GamingSignal(
            code=_SIGNAL_MR_VOLUME,
            status=DataStatus.UNAVAILABLE,
            summary="Список MR прочитан не полностью — избыточный объём merge проверить нельзя.",
            detail="pulls_truncated",
        )
    if not facts.pulls:
        return GamingSignal(
            code=_SIGNAL_MR_VOLUME,
            status=DataStatus.NOT_APPLICABLE,
            summary="Список MR пуст — сигнал объёма merge неприменим.",
        )

    merged = sum(
        1
        for pull in facts.pulls
        if pull.is_merged and _in_period(pull.updated_at, context)
    )
    return _volume_signal(
        code=_SIGNAL_MR_VOLUME,
        count=merged,
        cap=MERGED_MR_CAP,
        threshold=MR_VOLUME_FLAG_THRESHOLD,
        noun_one="смерженный MR",
        noun_many="смерженных MR",
    )


def _release_volume_signal(facts: ActivityFacts, context: AnalysisContext) -> GamingSignal:
    if facts.releases_error:
        return GamingSignal(
            code=_SIGNAL_RELEASE_VOLUME,
            status=DataStatus.UNAVAILABLE,
            summary="Список релизов недоступен — избыточный объём релизов проверить нельзя.",
            detail=facts.releases_error,
        )
    if facts.releases_truncated:
        return GamingSignal(
            code=_SIGNAL_RELEASE_VOLUME,
            status=DataStatus.UNAVAILABLE,
            summary=(
                "Список релизов прочитан не полностью — "
                "избыточный объём релизов проверить нельзя."
            ),
            detail="releases_truncated",
        )
    if not facts.releases:
        return GamingSignal(
            code=_SIGNAL_RELEASE_VOLUME,
            status=DataStatus.NOT_APPLICABLE,
            summary="Список релизов пуст — сигнал объёма релизов неприменим.",
        )

    published = sum(
        1
        for release in facts.releases
        if release.is_published and _in_period(release.released_at, context)
    )
    return _volume_signal(
        code=_SIGNAL_RELEASE_VOLUME,
        count=published,
        cap=RELEASE_CAP,
        threshold=RELEASE_VOLUME_FLAG_THRESHOLD,
        noun_one="релиз",
        noun_many="релизов",
    )


def _volume_signal(
    *,
    code: str,
    count: int,
    cap: int,
    threshold: int,
    noun_one: str,
    noun_many: str,
) -> GamingSignal:
    flagged = count >= threshold
    if flagged:
        summary = (
            f"За период {count} {noun_many}: это выше порога наблюдения ({threshold}) "
            f"при потолке Score {cap}. Оценка категории не растёт. "
            f"Один такой сигнал не помечает репозиторий."
        )
    else:
        summary = (
            f"За период {count} {noun_many if count != 1 else noun_one} "
            f"(порог предупреждения — {threshold}, потолок Score — {cap})."
        )
    return GamingSignal(
        code=code,
        status=DataStatus.MEASURED,
        summary=summary,
        flagged=flagged,
        detail=f"cap={cap}; flag_threshold={threshold}",
        value=count,
    )
