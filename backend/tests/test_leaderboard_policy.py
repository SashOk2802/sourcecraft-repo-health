from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from math import nan

from backend.app.leaderboard import (
    LeaderboardCandidate,
    LeaderboardFilters,
    LeaderboardSort,
    build_leaderboard,
)


class LeaderboardPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)
        self.candidates = (
            candidate(
                "alpha",
                score=92,
                language="Python",
                likes=10,
                last_activity_at=self.now - timedelta(days=2),
            ),
            candidate(
                "bravo",
                score=88,
                language="Rust",
                likes=100,
                last_activity_at=None,
            ),
            candidate(
                "charlie",
                score=88,
                language="Python",
                likes=5,
                last_activity_at=self.now - timedelta(days=1),
            ),
            candidate(
                "preview",
                score=99,
                language="Python",
                likes=1,
                last_activity_at=self.now,
                is_preliminary=True,
            ),
        )

    def test_all_numeric_scores_receive_competition_rank_by_score_only(self) -> None:
        result = build_leaderboard(self.candidates, methodology_version="v1")

        self.assertEqual(
            [(row.candidate.repository_id, row.rank) for row in result.entries],
            [("preview", 1), ("alpha", 2), ("bravo", 3), ("charlie", 3)],
        )
        self.assertEqual(result.total, 4)
        self.assertEqual(result.partial_total, 1)

    def test_sorting_by_likes_reorders_rows_but_not_ranks(self) -> None:
        result = build_leaderboard(
            self.candidates,
            methodology_version="v1",
            sort=LeaderboardSort.LIKES,
        )

        self.assertEqual(
            [(row.candidate.repository_id, row.rank) for row in result.entries],
            [("bravo", 3), ("alpha", 2), ("charlie", 3), ("preview", 1)],
        )

    def test_activity_sort_puts_recent_activity_before_unknown_time(self) -> None:
        result = build_leaderboard(
            self.candidates,
            methodology_version="v1",
            sort=LeaderboardSort.ACTIVITY,
        )

        self.assertEqual(
            [row.candidate.repository_id for row in result.entries],
            ["preview", "charlie", "alpha", "bravo"],
        )

    def test_filters_preserve_global_rank_for_partial_scores(self) -> None:
        result = build_leaderboard(
            self.candidates,
            methodology_version="v1",
            filters=LeaderboardFilters(language=" python "),
        )

        self.assertEqual(
            [(row.candidate.repository_id, row.rank) for row in result.entries],
            [("preview", 1), ("alpha", 2), ("charlie", 3)],
        )
        self.assertEqual(result.total, 3)
        self.assertEqual(result.partial_total, 1)

    def test_search_filters_name_without_recalculating_rank(self) -> None:
        result = build_leaderboard(
            self.candidates,
            methodology_version="v1",
            filters=LeaderboardFilters(search="CHAR"),
        )

        self.assertEqual(
            [(row.candidate.repository_id, row.rank) for row in result.entries],
            [("charlie", 3)],
        )
        self.assertEqual(result.partial_total, 0)

    def test_rejects_duplicate_repository_identity_in_one_methodology(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate repository/version pairs"):
            build_leaderboard(
                (self.candidates[0], self.candidates[0]),
                methodology_version="v1",
            )

    def test_versions_build_independent_rankings(self) -> None:
        version_two = candidate(
            "alpha",
            methodology_version="v2",
            score=100,
            language="Python",
            likes=20,
            last_activity_at=self.now,
        )
        candidates = (*self.candidates, version_two)

        version_one_result = build_leaderboard(candidates, methodology_version=" v1 ")
        version_two_result = build_leaderboard(candidates, methodology_version="v2")

        self.assertEqual(version_one_result.methodology_version, "v1")
        self.assertEqual(
            [(row.candidate.repository_id, row.rank) for row in version_one_result.entries],
            [("preview", 1), ("alpha", 2), ("bravo", 3), ("charlie", 3)],
        )
        self.assertEqual(version_two_result.methodology_version, "v2")
        self.assertEqual(
            [(row.candidate.repository_id, row.rank) for row in version_two_result.entries],
            [("alpha", 1)],
        )

    def test_rejects_invalid_candidate_values_without_coercion(self) -> None:
        invalid_arguments = (
            {"score": nan},
            {"score": True},
            {"is_preliminary": "true"},
            {"likes": -1},
            {"likes": True},
            {"last_activity_at": self.now.replace(tzinfo=None)},
            {"language": " "},
            {"methodology_version": " "},
        )
        for arguments in invalid_arguments:
            with (
                self.subTest(arguments=arguments),
                self.assertRaises((TypeError, ValueError)),
            ):
                candidate("invalid", **arguments)

    def test_rejects_invalid_sort_filter_and_version_type(self) -> None:
        with self.assertRaisesRegex(TypeError, "sort"):
            build_leaderboard(
                self.candidates,
                methodology_version="v1",
                sort="likes",  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "language"):
            LeaderboardFilters(language=5)  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "filters"):
            build_leaderboard(
                self.candidates,
                methodology_version="v1",
                filters="python",  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(TypeError, "candidates"):
            build_leaderboard(("invalid",), methodology_version="v1")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "methodology_version"):
            build_leaderboard(self.candidates, methodology_version=" ")
        with self.assertRaisesRegex(TypeError, "methodology_version"):
            build_leaderboard(self.candidates, methodology_version=1)  # type: ignore[arg-type]


def candidate(
    repository_id: str,
    *,
    methodology_version: str = "v1",
    score: float = 50,
    language: str | None = "Python",
    likes: int = 0,
    last_activity_at: datetime | None = None,
    is_preliminary: bool = False,
) -> LeaderboardCandidate:
    return LeaderboardCandidate(
        repository_id=repository_id,
        organization_slug="org",
        repository_slug=repository_id,
        methodology_version=methodology_version,
        score=score,
        is_preliminary=is_preliminary,
        language=language,
        likes=likes,
        last_activity_at=last_activity_at,
    )
