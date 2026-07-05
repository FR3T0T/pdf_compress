Drop a Unicode TTF here named DejaVuSans.ttf (or NotoSans-Regular.ttf) to
make translated-PDF output portable across machines.

pdf_translate.py's _resolve_unicode_font_path() checks this directory
first, before falling back to OS-provided fonts (arial.ttf/tahoma.ttf on
Windows, etc.) which may not be present on every machine.

DejaVu Sans covers Latin, Greek, and Cyrillic and is free to redistribute
(Bitstream Vera + public domain additions). Get it from
https://dejavu-fonts.github.io/ and place DejaVuSans.ttf directly in this
folder -- no other setup needed, pdf_translate.py picks it up automatically.
