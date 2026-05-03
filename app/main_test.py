"""Tests for the scheduler entry point — pipeline chaining and job registration."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from apscheduler.schedulers.blocking import BlockingScheduler

from app.base.config import scheduler_settings
from app.main import (
    _fan_out,
    _run_stage,
    negotiation_pipeline,
    register_jobs,
    sourcing_pipeline,
)


class TestRunStage:
    def test_returns_result_and_logs(self, caplog):
        with caplog.at_level(logging.INFO):
            result = _run_stage("test", lambda: 42)
        assert result == 42
        assert "test: starting" in caplog.text
        assert "test: finished" in caplog.text

    def test_returns_none_on_exception(self, caplog):
        with caplog.at_level(logging.ERROR):
            result = _run_stage(
                "test", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
            )
        assert result is None
        assert "test: failed" in caplog.text
        assert "boom" in caplog.text

    def test_passes_args_and_kwargs(self):
        def fn(a, b, c=None):
            return (a, b, c)

        assert _run_stage("test", fn, 1, 2, c=3) == (1, 2, 3)


class TestFanOut:
    def test_returns_item_result_pairs(self):
        results = _fan_out("test", lambda x: x * 2, [1, 2, 3])
        result_dict = dict(results)
        assert result_dict == {1: 2, 2: 4, 3: 6}

    def test_failed_items_get_none(self):
        def maybe_fail(x):
            if x == 2:
                raise RuntimeError("boom")
            return x * 10

        results = _fan_out("test", maybe_fail, [1, 2, 3])
        result_dict = dict(results)
        assert result_dict[1] == 10
        assert result_dict[2] is None
        assert result_dict[3] == 30

    def test_empty_items_returns_empty(self):
        assert _fan_out("test", lambda x: x, []) == []

    def test_logs_summary(self, caplog):
        with caplog.at_level(logging.INFO):
            _fan_out("test", lambda x: x, [1, 2])
        assert "test: starting (2 items" in caplog.text
        assert "2/2 succeeded" in caplog.text


class TestRegisterJobs:
    def test_registers_both_pipelines(self):
        test_scheduler = BlockingScheduler()
        with patch("app.main.scheduler", test_scheduler):
            register_jobs()
        job_ids = {job.id for job in test_scheduler.get_jobs()}
        assert job_ids == {"sourcing_pipeline", "negotiation_pipeline"}

    def test_uses_cron_trigger(self):
        test_scheduler = BlockingScheduler()
        with patch("app.main.scheduler", test_scheduler):
            register_jobs()
        for job in test_scheduler.get_jobs():
            assert job.trigger.__class__.__name__ == "CronTrigger"

    def test_sets_replace_existing_flag(self):
        """replace_existing=True is set so restarts with a persistent jobstore
        update existing jobs rather than raising ConflictingIdError."""
        test_scheduler = BlockingScheduler()
        with patch("app.main.scheduler", test_scheduler) as mock_sched:
            with patch.object(mock_sched, "add_job", wraps=mock_sched.add_job) as spy:
                register_jobs()
                for call_args in spy.call_args_list:
                    assert call_args.kwargs.get("replace_existing") is True


class TestSourcingPipeline:
    def test_chains_all_stages_on_new_urls(self):
        pending = [
            {"row_index": 0, "url": "https://kogan.com/a/"},
            {"row_index": 1, "url": "https://kogan.com/b/"},
        ]
        mock_product_a = MagicMock(id=10)
        mock_product_b = MagicMock(id=20)
        mock_sheets = MagicMock()

        with (
            patch(
                "app.pipeline.triggers.input_sheet.get_new_urls", return_value=pending
            ),
            patch(
                "app.pipeline.stages.s1_spec_extraction.extract_specs",
                side_effect=[mock_product_a, mock_product_b],
            ),
            patch(
                "app.pipeline.stages.s2_supplier_search.run_supplier_search",
                return_value=[],
            ) as mock_search,
            patch(
                "app.pipeline.stages.s3_outreach.send_outreach", return_value=3
            ) as mock_outreach,
            patch(
                "app.pipeline.stages.s6_sheet_update.update_sheet", return_value=5
            ) as mock_sheet,
            patch("app.services.sheets.SheetsService", return_value=mock_sheets),
        ):
            sourcing_pipeline()

        assert mock_search.call_count == 2
        mock_search.assert_any_call(10)
        mock_search.assert_any_call(20)
        mock_outreach.assert_called_once()
        mock_sheet.assert_called_once()
        mock_sheets.update_input_status.assert_any_call(0, "processing")
        mock_sheets.update_input_status.assert_any_call(0, "done")

    def test_skips_when_no_new_urls(self):
        mock_outreach = MagicMock()

        with (
            patch("app.pipeline.triggers.input_sheet.get_new_urls", return_value=[]),
            patch("app.pipeline.stages.s3_outreach.send_outreach", mock_outreach),
        ):
            sourcing_pipeline()

        mock_outreach.assert_not_called()

    def test_marks_error_and_continues_on_stage1_failure(self):
        bad_url = "https://kogan.com/bad/"
        good_url = "https://kogan.com/good/"
        pending = [{"row_index": 0, "url": bad_url}, {"row_index": 1, "url": good_url}]
        mock_product = MagicMock(id=10)
        mock_sheets = MagicMock()

        def extract_or_fail(url):
            if url == bad_url:
                raise RuntimeError("bad")
            return mock_product

        with (
            patch(
                "app.pipeline.triggers.input_sheet.get_new_urls", return_value=pending
            ),
            patch(
                "app.pipeline.stages.s1_spec_extraction.extract_specs",
                side_effect=extract_or_fail,
            ),
            patch(
                "app.pipeline.stages.s2_supplier_search.run_supplier_search",
                return_value=[],
            ) as mock_search,
            patch("app.pipeline.stages.s3_outreach.send_outreach", return_value=0),
            patch("app.pipeline.stages.s6_sheet_update.update_sheet", return_value=0),
            patch("app.services.sheets.SheetsService", return_value=mock_sheets),
        ):
            sourcing_pipeline()

        mock_sheets.update_input_status.assert_any_call(0, "error")
        mock_sheets.update_input_status.assert_any_call(1, "done")
        mock_search.assert_called_once_with(10)

    def test_skips_when_trigger_fails(self):
        mock_outreach = MagicMock()

        with (
            patch(
                "app.pipeline.triggers.input_sheet.get_new_urls",
                side_effect=RuntimeError("sheets down"),
            ),
            patch("app.pipeline.stages.s3_outreach.send_outreach", mock_outreach),
        ):
            sourcing_pipeline()

        mock_outreach.assert_not_called()


class TestNegotiationPipeline:
    def test_chains_stages_on_supplier_replies(self):
        mock_triage = MagicMock(return_value={"supplier_reply": 2, "archived_noise": 5})
        mock_negotiate = MagicMock(return_value={"replied": 1})
        mock_sheet = MagicMock(return_value=3)

        with (
            patch("app.pipeline.stages.s4_inbox_triage.triage_inbox", mock_triage),
            patch(
                "app.pipeline.stages.s5_negotiation.process_negotiations",
                mock_negotiate,
            ),
            patch("app.pipeline.stages.s6_sheet_update.update_sheet", mock_sheet),
        ):
            negotiation_pipeline()

        mock_triage.assert_called_once()
        mock_negotiate.assert_called_once()
        mock_sheet.assert_called_once()

    def test_skips_negotiation_when_no_replies(self):
        mock_triage = MagicMock(return_value={"supplier_reply": 0, "archived_noise": 3})
        mock_negotiate = MagicMock()

        with (
            patch("app.pipeline.stages.s4_inbox_triage.triage_inbox", mock_triage),
            patch(
                "app.pipeline.stages.s5_negotiation.process_negotiations",
                mock_negotiate,
            ),
        ):
            negotiation_pipeline()

        mock_triage.assert_called_once()
        mock_negotiate.assert_not_called()

    def test_skips_negotiation_when_triage_fails(self):
        mock_triage = MagicMock(side_effect=RuntimeError("gmail down"))
        mock_negotiate = MagicMock()

        with (
            patch("app.pipeline.stages.s4_inbox_triage.triage_inbox", mock_triage),
            patch(
                "app.pipeline.stages.s5_negotiation.process_negotiations",
                mock_negotiate,
            ),
        ):
            negotiation_pipeline()

        mock_negotiate.assert_not_called()
