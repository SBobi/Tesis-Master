"""Unit tests for Phase 13 — report_builder, formatters, reporter."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kmp_repair_pipeline.reporting.formatters import (
    aggregate_by_mode,
    to_csv,
    to_json,
    to_markdown,
)
from kmp_repair_pipeline.reporting.report_builder import ReportRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(
    case_id: str = "case-001",
    repair_mode: str = "full_thesis",
    bsr: float = 1.0,
    ctsr: float = 1.0,
    ffsr: float = 1.0,
    efr: float | None = 0.8,
    hit_at_1: float | None = 1.0,
    hit_at_3: float | None = 1.0,
    hit_at_5: float | None = 1.0,
    source_set_accuracy: float | None = None,
) -> ReportRow:
    return ReportRow(
        case_id=case_id,
        repair_mode=repair_mode,
        case_status="EVALUATED",
        repo_url="https://github.com/test/repo",
        pr_ref="PR #1",
        update_class="direct_library",
        bsr=bsr,
        ctsr=ctsr,
        ffsr=ffsr,
        efr=efr,
        hit_at_1=hit_at_1,
        hit_at_3=hit_at_3,
        hit_at_5=hit_at_5,
        source_set_accuracy=source_set_accuracy,
    )


# ---------------------------------------------------------------------------
# to_csv
# ---------------------------------------------------------------------------


class TestToCsv:
    def test_header_present(self) -> None:
        out = to_csv([_row()])
        reader = csv.DictReader(io.StringIO(out))
        assert "bsr" in reader.fieldnames
        assert "repair_mode" in reader.fieldnames

    def test_values_correct(self) -> None:
        out = to_csv([_row(bsr=1.0, ctsr=0.0)])
        reader = csv.DictReader(io.StringIO(out))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["bsr"] == "1.0"
        assert rows[0]["ctsr"] == "0.0"

    def test_none_values_written_as_empty(self) -> None:
        out = to_csv([_row(efr=None, hit_at_1=None)])
        reader = csv.DictReader(io.StringIO(out))
        rows = list(reader)
        assert rows[0]["efr"] == ""
        assert rows[0]["hit_at_1"] == ""

    def test_multiple_rows(self) -> None:
        out = to_csv([_row("c1"), _row("c2")])
        reader = csv.DictReader(io.StringIO(out))
        assert len(list(reader)) == 2

    def test_empty_rows_returns_header_only(self) -> None:
        out = to_csv([])
        lines = [l for l in out.splitlines() if l]
        assert len(lines) == 1   # header only


# ---------------------------------------------------------------------------
# to_json
# ---------------------------------------------------------------------------


class TestToJson:
    def test_valid_json(self) -> None:
        out = to_json([_row()])
        parsed = json.loads(out)
        assert isinstance(parsed, list)
        assert len(parsed) == 1

    def test_fields_present(self) -> None:
        out = to_json([_row()])
        d = json.loads(out)[0]
        assert "case_id" in d
        assert "bsr" in d
        assert "repair_mode" in d

    def test_none_preserved(self) -> None:
        out = to_json([_row(source_set_accuracy=None)])
        d = json.loads(out)[0]
        assert d["source_set_accuracy"] is None

    def test_empty_list(self) -> None:
        out = to_json([])
        assert json.loads(out) == []


# ---------------------------------------------------------------------------
# to_markdown
# ---------------------------------------------------------------------------


class TestToMarkdown:
    def test_contains_header(self) -> None:
        md = to_markdown([_row()])
        assert "| case_id |" in md

    def test_contains_row_data(self) -> None:
        md = to_markdown([_row(case_id="case-abc")])
        assert "case-abc" in md

    def test_na_for_none(self) -> None:
        md = to_markdown([_row(source_set_accuracy=None)])
        assert "N/A" in md

    def test_summary_section_present(self) -> None:
        md = to_markdown([_row(repair_mode="full_thesis"), _row(repair_mode="raw_error")])
        assert "Per-Mode Averages" in md

    def test_empty_returns_placeholder(self) -> None:
        md = to_markdown([])
        assert "No evaluation results" in md


# ---------------------------------------------------------------------------
# aggregate_by_mode
# ---------------------------------------------------------------------------


class TestAggregateByMode:
    def test_single_mode(self) -> None:
        rows = [_row(bsr=1.0), _row(bsr=0.0)]
        agg = aggregate_by_mode(rows)
        assert "full_thesis" in agg
        assert agg["full_thesis"]["bsr"] == 0.5
        assert agg["full_thesis"]["n"] == 2

    def test_multiple_modes(self) -> None:
        rows = [
            _row(repair_mode="full_thesis", bsr=1.0),
            _row(repair_mode="raw_error", bsr=0.0),
        ]
        agg = aggregate_by_mode(rows)
        assert agg["full_thesis"]["bsr"] == 1.0
        assert agg["raw_error"]["bsr"] == 0.0

    def test_none_values_excluded_from_mean(self) -> None:
        rows = [
            _row(efr=0.8),
            _row(efr=None),   # excluded from mean
        ]
        agg = aggregate_by_mode(rows)
        assert agg["full_thesis"]["efr"] == 0.8

    def test_all_none_gives_none(self) -> None:
        rows = [_row(efr=None)]
        agg = aggregate_by_mode(rows)
        assert agg["full_thesis"]["efr"] is None

    def test_empty_rows(self) -> None:
        assert aggregate_by_mode([]) == {}


# ---------------------------------------------------------------------------
# build_report — patched DB
# ---------------------------------------------------------------------------


class TestBuildReport:
    def _make_metric_mock(self, case_id: str, mode: str, bsr: float) -> MagicMock:
        m = MagicMock()
        m.repair_case_id = case_id
        m.repair_mode = mode
        m.bsr = bsr
        m.ctsr = 1.0
        m.ffsr = 1.0
        m.efr = 0.9
        m.hit_at_1 = 1.0
        m.hit_at_3 = 1.0
        m.hit_at_5 = 1.0
        m.source_set_accuracy = None
        m.extra = {}
        return m

    def _make_case_mock(self, case_id: str) -> MagicMock:
        case = MagicMock()
        case.id = case_id
        case.status = "EVALUATED"
        event = MagicMock()
        event.pr_ref = "PR #1"
        event.update_class = "direct_library"
        repo = MagicMock()
        repo.url = "https://github.com/test/repo"
        event.repository = repo
        case.dependency_event = event
        return case

    def test_returns_report_rows(self) -> None:
        from kmp_repair_pipeline.reporting.report_builder import build_report

        session = MagicMock()
        metric = self._make_metric_mock("case-001", "full_thesis", 1.0)
        case = self._make_case_mock("case-001")

        with (
            patch("kmp_repair_pipeline.reporting.report_builder.EvaluationMetricRepo") as MockMetric,
            patch("kmp_repair_pipeline.reporting.report_builder.RepairCaseRepo") as MockCase,
        ):
            MockMetric.return_value.list_all.return_value = [metric]
            MockCase.return_value.get_by_id.return_value = case

            rows = build_report(session)

        assert len(rows) == 1
        assert rows[0].case_id == "case-001"
        assert rows[0].bsr == 1.0
        assert rows[0].repair_mode == "full_thesis"

    def test_empty_metrics_returns_empty(self) -> None:
        from kmp_repair_pipeline.reporting.report_builder import build_report

        session = MagicMock()

        with patch("kmp_repair_pipeline.reporting.report_builder.EvaluationMetricRepo") as MockMetric:
            MockMetric.return_value.list_all.return_value = []
            rows = build_report(session)

        assert rows == []


# ---------------------------------------------------------------------------
# generate_report — file writing
# ---------------------------------------------------------------------------


class TestGenerateReport:
    def _make_rows(self) -> list[ReportRow]:
        return [_row("case-001", "full_thesis"), _row("case-001", "raw_error", bsr=0.0)]

    def test_writes_all_formats(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.reporting.reporter import generate_report

        session = MagicMock()

        with patch("kmp_repair_pipeline.reporting.reporter.build_report",
                   return_value=self._make_rows()):
            result = generate_report(
                session=session,
                output_dir=tmp_path / "reports",
                formats=("all",),
            )

        assert (tmp_path / "reports" / "report.csv").exists()
        assert (tmp_path / "reports" / "report.json").exists()
        assert (tmp_path / "reports" / "report.md").exists()
        assert result.row_count == 2
        assert len(result.files) == 3

    def test_writes_only_csv(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.reporting.reporter import generate_report

        session = MagicMock()

        with patch("kmp_repair_pipeline.reporting.reporter.build_report",
                   return_value=self._make_rows()):
            result = generate_report(
                session=session,
                output_dir=tmp_path / "reports",
                formats=("csv",),
            )

        assert (tmp_path / "reports" / "report.csv").exists()
        assert not (tmp_path / "reports" / "report.json").exists()
        assert len(result.files) == 1

    def test_aggregates_populated(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.reporting.reporter import generate_report

        session = MagicMock()

        with patch("kmp_repair_pipeline.reporting.reporter.build_report",
                   return_value=self._make_rows()):
            result = generate_report(
                session=session,
                output_dir=tmp_path / "r",
                formats=("json",),
            )

        assert "full_thesis" in result.aggregates
        assert "raw_error" in result.aggregates

    def test_empty_rows_writes_empty_files(self, tmp_path: Path) -> None:
        from kmp_repair_pipeline.reporting.reporter import generate_report

        session = MagicMock()

        with patch("kmp_repair_pipeline.reporting.reporter.build_report", return_value=[]):
            result = generate_report(
                session=session,
                output_dir=tmp_path / "r",
                formats=("markdown",),
            )

        assert result.row_count == 0
        md = (tmp_path / "r" / "report.md").read_text()
        assert "No evaluation results" in md
