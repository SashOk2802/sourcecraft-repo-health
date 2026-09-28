"""Построение API-модели и Markdown-отчёта по готовому анализу."""

from backend.app.reporting.badge import render_score_badge
from backend.app.reporting.builder import build_report_payload, render_markdown_report

__all__ = ("build_report_payload", "render_markdown_report", "render_score_badge")