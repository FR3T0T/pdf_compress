#!/usr/bin/env python3
"""
compress_pdf.py — Offline PDF compressor (command line)
Requires: pip install pikepdf pillow

Usage:
    python compress_pdf.py input.pdf
    python compress_pdf.py input.pdf -p standard
    python compress_pdf.py *.pdf -o compressed/
    python compress_pdf.py input.pdf --linearize

Presets:
    screen    —  72 DPI, JPEG 35  (smallest file, strips metadata)
    ebook     — 120 DPI, JPEG 55  (tablets/laptops, strips metadata)
    standard  — 150 DPI, JPEG 65  (default — lecture notes, docs)
    high      — 200 DPI, JPEG 80  (good prints)
    prepress  — 300 DPI, JPEG 90  (professional printing)
"""

import argparse
import os
import sys

from engine import (
    PRESETS, PRESET_ORDER, compress_pdf, fmt_size,
    EncryptedPDFError,
)


def progress_bar(current, total, status):
    if total <= 0:
        return
    pct = current / total
    w = 25
    filled = int(w * pct)
    bar = "█" * filled + "░" * (w - filled)
    sys.stdout.write(f"\r    [{bar}] {current}/{total}  {status:<20s}")
    sys.stdout.flush()
    if current == total:
        sys.stdout.write("\n")


def main():
    parser = argparse.ArgumentParser(
        description="Compress PDFs offline — DPI-aware, grayscale-preserving."
    )
    parser.add_argument("inputs", nargs="+", help="Input PDF file(s)")
    parser.add_argument(
        "-p", "--preset",
        choices=PRESET_ORDER,
        default="standard",
        help="Quality preset (default: standard)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output path (file for single input, directory for batch)",
    )
    parser.add_argument(
        "--linearize", action="store_true",
        help="Produce web-optimized (linearized) PDF",
    )
    parser.add_argument(
        "--no-pause", action="store_true",
        help="Don't wait for Enter at exit",
    )
    args = parser.parse_args()

    preset = PRESETS[args.preset]

    if args.output and len(args.inputs) > 1:
        if not os.path.isdir(args.output):
            print(f"ERROR: For batch, -o must be an existing directory.")
            sys.exit(1)

    meta_note = "  (strips metadata)" if preset.strip_metadata else ""
    print(f"\n  Preset  : {preset.name}{meta_note}")
    print(f"  DPI     : {preset.target_dpi}")
    print(f"  JPEG    : {preset.jpeg_quality}%")
    if args.linearize:
        print(f"  Output  : linearized (web-optimized)")
    print(f"  {'─' * 42}")

    total_saved = 0
    total_orig = 0
    n_ok = n_skip = n_err = 0

    for path in args.inputs:
        if not os.path.isfile(path):
            print(f"\n  SKIP: {path} — not found")
            continue

        if args.output:
            if os.path.isdir(args.output):
                base = os.path.basename(path)
                name, ext = os.path.splitext(base)
                out = os.path.join(args.output, f"{name}_compressed{ext}")
            else:
                out = args.output
        else:
            out = None

        print(f"\n  {os.path.basename(path)}")

        try:
            result = compress_pdf(
                path, out, preset_key=args.preset,
                on_progress=progress_bar,
                linearize=args.linearize,
            )
            total_orig += result.original_size
            s = result.stats

            if result.skipped:
                n_skip += 1
                print(f"    Already optimized — original kept ({fmt_size(result.original_size)})")
            else:
                n_ok += 1
                total_saved += result.saved_bytes
                print(f"    {fmt_size(result.original_size)} → {fmt_size(result.compressed_size)}"
                      f"  ({result.saved_pct:.1f}% smaller)")
                print(f"    Saved to: {result.output_path}")

            if s.images_total > 0:
                parts = []
                if s.images_recompressed:    parts.append(f"{s.images_recompressed} recompressed")
                if s.images_downscaled:      parts.append(f"{s.images_downscaled} downscaled")
                if s.images_skipped_tiny:    parts.append(f"{s.images_skipped_tiny} tiny (kept)")
                if s.images_skipped_quality: parts.append(f"{s.images_skipped_quality} already compressed (kept)")
                if s.images_skipped_bomb:    parts.append(f"{s.images_skipped_bomb} skipped (too large)")
                if s.images_with_mask_composited:
                    parts.append(f"{s.images_with_mask_composited} transparency composited")
                if parts:
                    print(f"    Images: {', '.join(parts)}")

        except EncryptedPDFError as e:
            n_err += 1
            print(f"    SKIPPED: {e}")

        except Exception as e:
            n_err += 1
            print(f"    ERROR: {e}")

    if n_ok + n_skip + n_err > 1:
        print(f"\n  {'─' * 42}")
        parts = []
        if n_ok:   parts.append(f"{n_ok} compressed")
        if n_skip: parts.append(f"{n_skip} skipped")
        if n_err:  parts.append(f"{n_err} failed")
        print(f"  Summary: {', '.join(parts)}")
        if total_orig > 0 and total_saved > 0:
            pct = total_saved / total_orig * 100
            print(f"  Total saved: {fmt_size(total_saved)} ({pct:.0f}%)")

    print()
    if not args.no_pause:
        input("  Done. Press Enter to exit...")


if __name__ == "__main__":
    main()
