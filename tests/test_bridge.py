"""Tests for the pure compress output-path helper.

Imports from ``compress_paths`` (a Qt-free module) rather than ``ui.bridge``
so this runs on a headless CI runner without the PySide6 GUI stack -- see the
module docstring in ``compress_paths.py`` for why that import path matters.
"""

import os

from compress_paths import compress_output_path


def test_single_file_honors_explicit_output(tmp_path):
    chosen = str(tmp_path / "chosen.pdf")
    got = compress_output_path(
        str(tmp_path / "a.pdf"), 1, chosen, "", "{name}_compressed", "standard"
    )
    assert got == chosen


def test_batch_does_not_reuse_single_explicit_output(tmp_path):
    # Regression: a single explicit outputPath must NOT be applied to every
    # file in a batch (that made all files overwrite one path). With no output
    # dir and default naming, each file falls back to the beside-source default
    # (None -> compress_pdf writes <name>_compressed.pdf next to the source).
    chosen = str(tmp_path / "chosen.pdf")
    got = compress_output_path(
        str(tmp_path / "a.pdf"), 3, chosen, "", "{name}_compressed", "standard"
    )
    assert got != chosen
    assert got is None


def test_default_returns_none_for_beside_source(tmp_path):
    got = compress_output_path(
        str(tmp_path / "a.pdf"), 1, None, "", "{name}_compressed", "standard"
    )
    assert got is None


def test_output_dir_builds_distinct_per_file_path(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    got = compress_output_path(
        str(tmp_path / "report.pdf"), 2, None, str(out_dir), "{name}_compressed", "ebook"
    )
    assert got is not None
    assert os.path.basename(got) == "report_compressed.pdf"
    assert os.path.realpath(os.path.dirname(got)) == os.path.realpath(str(out_dir))


def test_naming_template_variables_applied(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    got = compress_output_path(
        str(tmp_path / "report.pdf"), 1, None, str(out_dir), "{name}-{preset}", "ebook"
    )
    assert os.path.basename(got) == "report-ebook.pdf"


def test_bad_naming_template_falls_back(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    got = compress_output_path(
        str(tmp_path / "report.pdf"), 1, None, str(out_dir), "{unknown}", "standard"
    )
    assert os.path.basename(got) == "report_compressed.pdf"
