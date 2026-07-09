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
        # Doesn't crash, but a failed input must be a nonzero exit (CLI-01) --
        # this used to assert returncode == 0, codifying the bug: any
        # chained/scripted use (the documented `... -o out/ && next_step`
        # pattern) would treat an all-failed run as success.
        assert result.returncode == 1
        assert "SKIPPED" in result.stdout or "not found" in result.stdout.lower() or "Not a valid" in result.stdout

    @pytest.mark.integration
    def test_success_exits_zero(self, sample_pdf, tmp_path):
        out = str(tmp_path / "cli_out.pdf")
        result = self._run(sample_pdf, "-o", out, "--no-pause")
        assert result.returncode == 0

    @pytest.mark.integration
    def test_mixed_batch_with_one_failure_exits_nonzero(self, sample_pdf, invalid_file, tmp_path):
        # CLI-01: any failure in the batch must make the whole run exit
        # nonzero, even when other inputs in the same batch succeed.
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir)
        result = self._run(sample_pdf, invalid_file, "-o", out_dir, "--no-pause")
        assert result.returncode == 1
