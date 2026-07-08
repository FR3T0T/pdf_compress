"""Pure output-path resolution for the compress tool.

Extracted from ``ui/bridge.py`` so this logic can be unit-tested without
importing the PySide6 GUI stack: importing anything under the ``ui`` package
runs ``ui/__init__.py`` -> ``web_shell`` -> Qt, whose shared libraries need a
graphics stack that headless CI runners lack (``libEGL.so.1``). This module
depends only on ``os`` and ``pdf_ops.contained_output_path`` -- no Qt -- so
``tests/test_bridge.py`` can exercise it directly on a headless runner.
"""

import os

from pdf_ops import contained_output_path


def compress_output_path(
    input_path: str,
    file_count: int,
    explicit_output: str | None,
    output_dir: str,
    naming: str,
    preset_key: str,
) -> str | None:
    """Resolve one file's compression output path for a (possibly batch) run.

    A single explicit ``outputPath`` is honored only for a single-file call --
    for a batch it would make every file overwrite the same path (the bug this
    replaces). When an output directory or a non-default naming template is
    given, a distinct path is built per file (sanitized via
    ``contained_output_path``). Otherwise ``None`` is returned so
    ``compress_pdf`` writes ``<name>_compressed.pdf`` beside the source, the
    long-standing default.
    """
    if file_count == 1 and explicit_output:
        return explicit_output
    naming = naming or "{name}_compressed"
    if not output_dir and naming == "{name}_compressed":
        return None
    name_no_ext = os.path.splitext(os.path.basename(input_path))[0]
    try:
        out_name = naming.format(name=name_no_ext, preset=preset_key)
    except (KeyError, IndexError):
        out_name = f"{name_no_ext}_compressed"
    out_folder = output_dir or os.path.dirname(input_path)
    return contained_output_path(out_folder, out_name + ".pdf")
