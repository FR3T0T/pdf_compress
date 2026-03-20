"""Tests for compress_pdf.py — CLI entry point."""

import os
import subprocess
import sys

import pytest


class TestCLI:
    def _run(self, *args):
        """Run the CLI and return the completed process."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "compress_pdf.py", *args],
            capture_output=True, text=True, timeout=60, env=env,
        )

    def test_help(self):
        result = self._run("--help")
        assert result.returncode == 0
        assert "Compress PDFs offline" in result.stdout

    @pytest.mark.integration
    def test_compress_default(self, sample_pdf, tmp_path):
        out = str(tmp_path / "cli_out.pdf")
        result = self._run(sample_pdf, "-o", out, "--no-pause")
        assert result.returncode == 0

    def test_invalid_file(self, invalid_file, tmp_path):
        out = str(tmp_path / "cli_out.pdf")
        result = self._run(invalid_file, "-o", out, "--no-pause")
        # Should still exit 0 (skips invalid files, doesn't crash)
        assert result.returncode == 0
        assert "SKIPPED" in result.stdout or "not found" in result.stdout.lower() or "Not a valid" in result.stdout
