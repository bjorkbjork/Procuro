"""Tests for attachment_conversion module. Mocks subprocess.run —
no LibreOffice needed in CI."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.pipeline.stages.attachment_conversion import (
    ALL_ATTACHMENT_MIME_TYPES,
    CONVERTIBLE_MIME_TYPES,
    PDF_MIME_TYPE,
    ConversionError,
    convert_to_pdf,
)


def _fake_libreoffice_success(tmpdir_path: str, stem: str):
    """Return a side_effect that writes a fake PDF into the tmpdir."""

    def side_effect(*args, **kwargs):
        cmd = args[0]
        outdir = cmd[cmd.index("--outdir") + 1]
        Path(outdir, f"{stem}.pdf").write_bytes(b"%PDF-fake-output")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    return side_effect


class TestConvertToPdf:
    def test_success(self):
        with patch(
            "app.pipeline.stages.attachment_conversion.subprocess.run"
        ) as mock_run:
            mock_run.side_effect = _fake_libreoffice_success("/tmp", "quote")
            result = convert_to_pdf(
                b"xlsx-data",
                "quote.xlsx",
                CONVERTIBLE_MIME_TYPES[
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ],
            )

        assert result == b"%PDF-fake-output"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "libreoffice" in cmd
        assert "--headless" in cmd
        assert "--convert-to" in cmd
        assert "pdf" in cmd

    def test_timeout_raises_conversion_error(self):
        with patch(
            "app.pipeline.stages.attachment_conversion.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="libreoffice", timeout=60),
        ):
            with pytest.raises(ConversionError, match="timed out"):
                convert_to_pdf(
                    b"data", "big.docx", CONVERTIBLE_MIME_TYPES["application/msword"]
                )

    def test_nonzero_exit_raises_conversion_error(self):
        with patch(
            "app.pipeline.stages.attachment_conversion.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="Error: corrupted file"
            ),
        ):
            with pytest.raises(ConversionError, match="corrupted file"):
                convert_to_pdf(
                    b"data",
                    "bad.xls",
                    CONVERTIBLE_MIME_TYPES["application/vnd.ms-excel"],
                )

    def test_missing_output_raises_conversion_error(self):
        with patch(
            "app.pipeline.stages.attachment_conversion.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            ),
        ):
            with pytest.raises(ConversionError, match="no output file"):
                convert_to_pdf(
                    b"data",
                    "empty.docx",
                    CONVERTIBLE_MIME_TYPES[
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ],
                )


class TestMimeTypeConstants:
    def test_pdf_in_all_types(self):
        assert PDF_MIME_TYPE in ALL_ATTACHMENT_MIME_TYPES

    def test_convertible_types_in_all_types(self):
        for mime in CONVERTIBLE_MIME_TYPES:
            assert mime in ALL_ATTACHMENT_MIME_TYPES

    def test_expected_convertible_types(self):
        assert (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            in CONVERTIBLE_MIME_TYPES
        )
        assert "application/vnd.ms-excel" in CONVERTIBLE_MIME_TYPES
        assert (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            in CONVERTIBLE_MIME_TYPES
        )
        assert "application/msword" in CONVERTIBLE_MIME_TYPES
