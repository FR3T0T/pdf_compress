"""Tests for compress_pdf.py — CLI entry point."""

import os
import subprocess
import sys

import pikepdf
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

    @pytest.mark.integration
    def test_same_basename_batch_produces_distinct_outputs(self, tmp_path):
        # CLI-02: two inputs sharing a basename (from different source
        # directories) must not silently overwrite each other's output.
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        out_dir = tmp_path / "out"
        dir_a.mkdir()
        dir_b.mkdir()
        out_dir.mkdir()

        path_a = str(dir_a / "report.pdf")
        path_b = str(dir_b / "report.pdf")
        for path, marker in ((path_a, "FROM_A"), (path_b, "FROM_B")):
            pdf = pikepdf.Pdf.new()
            pdf.add_blank_page(page_size=(200, 200))
            pdf.Root["/Marker"] = marker
            pdf.save(path)
            pdf.close()

        result = self._run(path_a, path_b, "-o", str(out_dir), "--no-pause")
        assert result.returncode == 0

        out_files = sorted(os.listdir(out_dir))
        assert len(out_files) == 2

        markers = set()
        for f in out_files:
            with pikepdf.open(str(out_dir / f)) as pdf:
                markers.add(str(pdf.Root["/Marker"]))
        assert markers == {"FROM_A", "FROM_B"}

    @pytest.mark.integration
    def test_no_pause_flag_omitted_on_closed_stdin_does_not_crash(self, sample_pdf, tmp_path):
        # CLI-03: without --no-pause, an unguarded input() at exit raised
        # EOFError on non-interactive stdin (piped/redirected/CI), crashing
        # with exit code 1 regardless of whether compression actually
        # succeeded -- masking the real exit status CLI-01 established.
        # A closed pipe (not DEVNULL/NUL, which Windows reports as a tty)
        # reliably reproduces non-interactive stdin.
        out = str(tmp_path / "cli_out.pdf")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [sys.executable, "compress_pdf.py", sample_pdf, "-o", out],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
        _, stderr = proc.communicate(input="", timeout=60)
        assert proc.returncode == 0, stderr
        assert "EOFError" not in stderr
