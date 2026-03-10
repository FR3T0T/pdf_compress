#!/usr/bin/env python3
"""
compress_pdf.py — Offline PDF compressor
Requires: pip install pikepdf pillow

Usage:
    python compress_pdf.py input.pdf
    python compress_pdf.py input.pdf -q 3
    python compress_pdf.py file1.pdf file2.pdf
    python compress_pdf.py *.pdf

Quality levels (1–5):
    1 — Minimum  (~72 DPI,  JPEG 30) — smallest file, rough images
    2 — Low      (~96 DPI,  JPEG 50)
    3 — Medium   (~120 DPI, JPEG 65) — DEFAULT
    4 — High     (~150 DPI, JPEG 80)
    5 — Maximum  (~200 DPI, JPEG 90) — lightest compression
"""

import argparse
import io
import os
import sys

try:
    import pikepdf
    from PIL import Image
except ImportError:
    print("Missing dependencies. Run:  pip install pikepdf pillow")
    input("\nPress Enter to exit...")
    sys.exit(1)

QUALITY_SETTINGS = {
    1: {"jpeg_quality": 30, "max_dpi": 72,  "label": "Minimum"},
    2: {"jpeg_quality": 50, "max_dpi": 96,  "label": "Low"},
    3: {"jpeg_quality": 65, "max_dpi": 120, "label": "Medium"},
    4: {"jpeg_quality": 80, "max_dpi": 150, "label": "High"},
    5: {"jpeg_quality": 90, "max_dpi": 200, "label": "Maximum"},
}


def compress_images_in_pdf(pdf, jpeg_quality, max_dpi):
    for page in pdf.pages:
        if "/Resources" not in page:
            continue
        resources = page["/Resources"]
        if "/XObject" not in resources:
            continue

        xobjects = resources["/XObject"]
        for key in list(xobjects.keys()):
            xobj = xobjects[key]
            if xobj.get("/Subtype") != "/Image":
                continue
            try:
                raw = bytes(xobj.read_raw_bytes())
                image = Image.open(io.BytesIO(raw))

                orig_w, orig_h = image.size
                scale = min(1.0, (max_dpi * 10) / max(orig_w, orig_h))
                if scale < 1.0:
                    image = image.resize(
                        (int(orig_w * scale), int(orig_h * scale)),
                        Image.LANCZOS
                    )

                if image.mode in ("RGBA", "P", "LA"):
                    image = image.convert("RGB")
                elif image.mode != "RGB":
                    image = image.convert("RGB")

                buf = io.BytesIO()
                image.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
                buf.seek(0)

                xobjects[key] = pikepdf.Stream(pdf, buf.read())
                xobjects[key]["/Type"]             = pikepdf.Name("/XObject")
                xobjects[key]["/Subtype"]          = pikepdf.Name("/Image")
                xobjects[key]["/ColorSpace"]       = pikepdf.Name("/DeviceRGB")
                xobjects[key]["/BitsPerComponent"] = 8
                xobjects[key]["/Width"]            = image.width
                xobjects[key]["/Height"]           = image.height
                xobjects[key]["/Filter"]           = pikepdf.Name("/DCTDecode")

            except Exception:
                continue


def compress_pdf(input_path, output_path, level):
    s = QUALITY_SETTINGS[level]
    original_size = os.path.getsize(input_path)

    with pikepdf.open(input_path) as pdf:
        compress_images_in_pdf(pdf, s["jpeg_quality"], s["max_dpi"])
        pdf.save(
            output_path,
            compress_streams=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
        )

    compressed_size = os.path.getsize(output_path)

    if compressed_size >= original_size:
        os.remove(output_path)
        print(f"  {os.path.basename(input_path)}")
        print(f"    Result : No size reduction achieved — original kept.")
        return

    reduction = (1 - compressed_size / original_size) * 100
    print(f"  {os.path.basename(input_path)}")
    print(f"    Before : {original_size   / 1024:.1f} KB")
    print(f"    After  : {compressed_size / 1024:.1f} KB  ({reduction:.1f}% smaller)")
    print(f"    Saved  : {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Compress PDFs offline — no Ghostscript needed."
    )
    parser.add_argument("inputs", nargs="+", help="Input PDF file(s)")
    parser.add_argument(
        "-q", "--quality",
        type=int,
        choices=[1, 2, 3, 4, 5],
        default=3,
        help="Quality level 1–5 (default: 3 = Medium)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output path (single file only)",
    )
    args = parser.parse_args()

    if args.output and len(args.inputs) > 1:
        print("ERROR: -o/--output can only be used with a single input file.")
        sys.exit(1)

    label = QUALITY_SETTINGS[args.quality]["label"]
    print(f"\nQuality : {args.quality}/5 — {label}")
    print("-" * 40)

    for input_path in args.inputs:
        if not os.path.isfile(input_path):
            print(f"SKIP: {input_path} — file not found.")
            continue

        if args.output:
            output_path = args.output
        else:
            base, ext = os.path.splitext(input_path)
            output_path = f"{base}_compressed{ext}"

        compress_pdf(input_path, output_path, args.quality)
        print()

    input("Done! Press Enter to exit...")


if __name__ == "__main__":
    main()
