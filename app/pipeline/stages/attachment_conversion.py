"""Convert non-PDF document attachments to PDF via LibreOffice headless."""

import logging
import subprocess
import tempfile
import uuid
from pathlib import Path

from app.base.config import settings

log = logging.getLogger(__name__)

CONVERTIBLE_MIME_TYPES: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
}

PDF_MIME_TYPE = "application/pdf"

ALL_ATTACHMENT_MIME_TYPES = set(CONVERTIBLE_MIME_TYPES) | {PDF_MIME_TYPE}


class ConversionError(Exception):
    def __init__(self, filename: str, detail: str):
        self.filename = filename
        self.detail = detail
        super().__init__(f"Failed to convert {filename}: {detail}")


def convert_to_pdf(data: bytes, filename: str, mime_type: str) -> bytes:
    """Convert document bytes to PDF using LibreOffice headless.

    Raises ConversionError on failure, timeout, or missing output.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        ext = CONVERTIBLE_MIME_TYPES.get(mime_type, Path(filename).suffix)
        stem = Path(filename).stem or "attachment"
        input_path = Path(tmpdir) / f"{stem}{ext}"
        input_path.write_bytes(data)

        # Isolated user profile so concurrent conversions don't collide
        profile_dir = f"file:///tmp/lo_{uuid.uuid4().hex}"

        try:
            result = subprocess.run(
                [
                    "libreoffice",
                    "--headless",
                    f"-env:UserInstallation={profile_dir}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    tmpdir,
                    str(input_path),
                ],
                timeout=settings.LIBREOFFICE_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            raise ConversionError(filename, "LibreOffice timed out")

        if result.returncode != 0:
            raise ConversionError(filename, result.stderr)

        output_path = Path(tmpdir) / f"{stem}.pdf"
        if not output_path.exists():
            raise ConversionError(filename, "LibreOffice produced no output file")

        return output_path.read_bytes()
