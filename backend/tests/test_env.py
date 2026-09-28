"""Тесты вспомогательных функций чтения переменных окружения."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.app._env import env_float, env_int


class EnvHelpersTest(unittest.TestCase):
    def test_env_int_returns_default_when_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env_int("TEST_INT_VAR", 42), 42)

    def test_env_int_returns_default_for_invalid_values(self) -> None:
        invalid_cases = ["", "   ", "not_a_number", "0", "-1", "-100", "12.34"]
        for case in invalid_cases:
            with self.subTest(case=case), patch.dict(os.environ, {"TEST_INT_VAR": case}):
                self.assertEqual(env_int("TEST_INT_VAR", 50), 50)

    def test_env_int_parses_valid_positive_integers(self) -> None:
        valid_cases = [("1", 1), (" 42 ", 42), ("50000", 50000), ("999999", 999999)]
        for raw, expected in valid_cases:
            with self.subTest(raw=raw, expected=expected), patch.dict(os.environ, {"TEST_INT_VAR": raw}):
                self.assertEqual(env_int("TEST_INT_VAR", 10), expected)

    def test_env_float_returns_default_when_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env_float("TEST_FLOAT_VAR", 12.5), 12.5)

    def test_env_float_returns_default_for_invalid_values(self) -> None:
        invalid_cases = ["", "   ", "not_a_float", "0", "0.0", "-0.0", "-1.5", "nan", "inf", "-inf"]
        for case in invalid_cases:
            with self.subTest(case=case), patch.dict(os.environ, {"TEST_FLOAT_VAR": case}):
                self.assertEqual(env_float("TEST_FLOAT_VAR", 33.3), 33.3)

    def test_env_float_parses_valid_positive_floats(self) -> None:
        valid_cases = [("0.5", 0.5), (" 120.0 ", 120.0), ("42", 42.0), ("1e2", 100.0)]
        for raw, expected in valid_cases:
            with self.subTest(raw=raw, expected=expected), patch.dict(os.environ, {"TEST_FLOAT_VAR": raw}):
                self.assertEqual(env_float("TEST_FLOAT_VAR", 1.0), expected)


if __name__ == "__main__":
    unittest.main()
