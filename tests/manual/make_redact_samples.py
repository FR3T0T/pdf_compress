"""Write the sample files for the Redact app checklist (#119).

Usage: python tests/manual/make_redact_samples.py <folder>

Every file is built here, object by object, with pikepdf, Pillow and
PyMuPDF, which the app already needs; nothing is downloaded or copied. Each
page says what the sample is for. The checklist that uses them is in the
pull request for #119. This is not a test: pytest collects only test_*.py
files.
"""
import io
import os
import sys
import zlib

import fitz
import pikepdf
from pikepdf import Array, Dictionary, Name, String
from PIL import Image, ImageDraw

TERM = "ACME-4471"


def font(pdf):
    return pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))


def text(*rows, size: int = 14, x: float = 72, y: float = 720, step: float = 24) -> bytes:
    """Each row on a line of its own, from the top down. A row is a string, or
    a list of (x, string) pieces on one line."""
    out = []
    for i, row in enumerate(rows):
        for at, words in ([(x, row)] if isinstance(row, str) else row):
            out.append(f"BT /F1 {size} Tf {at} {y - i * step} Td ({words}) Tj ET")
    return "\n".join(out).encode()


def add_page(pdf, content: bytes, **entries):
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
    page = pdf.pages[-1].obj
    page["/Resources"] = Dictionary(Font=Dictionary(F1=font(pdf)))
    page["/Contents"] = pdf.make_stream(content)
    for key, value in entries.items():
        page[f"/{key}"] = value
    return page


def on_page(page, *annots) -> None:
    if "/Annots" not in page:
        page["/Annots"] = Array()
    for a in annots:
        page["/Annots"].append(a)


def panel(pdf, words: str, width: float, height: float = 30):
    """An appearance stream: a yellow panel with a red border and red words."""
    return pdf.make_stream(
        f"1 1 0 rg 1 0 0 RG 0 0 {width} {height} re B "
        f"BT /F1 16 Tf 1 0 0 rg 8 9 Td ({words}) Tj ET".encode(),
        Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, width, height],
        Resources=Dictionary(Font=Dictionary(F1=font(pdf))))


def stamp(pdf, rect, words: str):
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    return pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Stamp, Rect=rect,
                                        AP=Dictionary(N=panel(pdf, words, width, height))))


def image(pdf, picture: Image.Image):
    """*picture* as an image XObject, Flate-compressed RGB."""
    rgb = picture.convert("RGB")
    return pdf.make_stream(zlib.compress(rgb.tobytes()), Type=Name.XObject, Subtype=Name.Image,
                           Width=rgb.width, Height=rgb.height, ColorSpace=Name.DeviceRGB,
                           BitsPerComponent=8, Filter=Name.FlateDecode)


#: Where a scan goes on its page, and where its rows of words are on it.
SCAN = (72, 560, 468, 156)          # x, y of its lower left corner, width, height
SCAN_ROW = (12, 30, 34)             # x, the first baseline and the step, from its top


def picture_of_words(rows) -> Image.Image:
    """A picture of typed words, as a scanner would see them: grey paper, dark
    Helvetica 14 at SCAN_ROW, drawn at twice the size of the page."""
    doc = fitz.open()
    page = doc.new_page(width=SCAN[2], height=SCAN[3])
    for i, row in enumerate(rows):
        page.insert_text((SCAN_ROW[0], SCAN_ROW[1] + i * SCAN_ROW[2]), row, fontname="helv",
                         fontsize=14)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY)
    doc.close()
    picture = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    return picture.point(lambda v: 235 if v > 128 else 30)


def scan_page(pdf, rows, layer: bool):
    """A page that is a picture of *rows*, with a hidden text layer of the
    same words over it (text drawn in the invisible mode), if *layer*."""
    x, y, width, height = SCAN
    hidden = b"".join(
        f"\nBT 3 Tr /F1 14 Tf {x + SCAN_ROW[0]} {y + height - SCAN_ROW[1] - i * SCAN_ROW[2]} Td "
        f"({row}) Tj ET".encode() for i, row in enumerate(rows)) if layer else b""
    page = add_page(pdf, f"q {width} 0 0 {height} {x} {y} cm /Scan Do Q".encode() + hidden)
    page["/Resources"]["/XObject"] = Dictionary(Scan=image(pdf, picture_of_words(rows)))


def save(pdf, folder: str, name: str) -> None:
    pdf.save(os.path.join(folder, name), fix_metadata_version=False)
    pdf.close()
    print("wrote", name)


def number_across_a_line(folder):
    pdf = pikepdf.Pdf.new()
    add_page(pdf, text("Sample 1: a number broken across a line.", "",
                       "The old account, number 4455-",
                       "8870, was closed in May. Nothing else on this page goes."))
    save(pdf, folder, "01_number_across_a_line.pdf")


def letter_spaced_heading(folder):
    pdf = pikepdf.Pdf.new()
    add_page(pdf, text("C O N F I D E N T I A L", size=24) + b"\n"
             + text("Sample 2: a heading set with a space between its letters.", y=680))
    save(pdf, folder, "02_letter_spaced_heading.pdf")


def stamp_over_a_box(folder):
    pdf = pikepdf.Pdf.new()
    page = add_page(pdf, text("Sample 3: a stamp lies over a word. Draw the box over the word.", "",
                              [(72, "The payment reference is"), (260, "TANGO-STAMP"),
                               (380, "and it is due today.")]))
    on_page(page, stamp(pdf, [250, 662, 372, 694], "PAID"))
    save(pdf, folder, "03_stamp_over_a_box.pdf")


def stamp_that_says_the_term(folder):
    pdf = pikepdf.Pdf.new()
    page = add_page(pdf, text("Sample 4: the stamp below says the word; the page text does not."))
    on_page(page, stamp(pdf, [72, 560, 372, 600], "CHECKED BY TANGO-SEAL"))
    save(pdf, folder, "04_stamp_that_says_the_term.pdf")


def _tagged(pdf, page, kids):
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    top = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    for kid in kids:
        kid["/P"] = top
    top["/K"] = Array(kids)
    root["/K"] = Array([top])
    pdf.Root["/StructTreeRoot"] = root
    pdf.Root["/MarkInfo"] = Dictionary(Marked=True)


def tagged_picture(folder):
    pdf = pikepdf.Pdf.new()
    photo = Image.new("RGB", (240, 160), (90, 140, 200))
    ImageDraw.Draw(photo).rectangle([40, 30, 200, 130], fill=(240, 200, 60))
    page = add_page(pdf, b"/P <</MCID 0>> BDC "
                    + text("Sample 5: a tagged file. The picture below has a description.")
                    + b" EMC\n/Figure <</MCID 1>> BDC q 240 0 0 160 72 450 cm /Im1 Do Q EMC")
    page["/Resources"]["/XObject"] = Dictionary(Im1=image(pdf, photo))
    line = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, Pg=page, K=0))
    figure = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Figure, Pg=page, K=1,
                                          Alt=String("photo of the signed contract")))
    _tagged(pdf, page, [line, figure])
    save(pdf, folder, "05_tagged_picture_with_description.pdf")


def tagged_link(folder):
    pdf = pikepdf.Pdf.new()
    page = add_page(pdf, b"/P <</MCID 0>> BDC "
                    + text("Sample 6: a tagged file. The address below is a link.") + b" EMC\n"
                    + b"/Link <</MCID 1>> BDC " + text("example.org/terms", y=660) + b" EMC")
    link = pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Link, Rect=[70, 655, 200, 675], Border=[0, 0, 0],
        A=Dictionary(S=Name.URI, URI=String("https://example.org/terms"))))
    on_page(page, link)
    line = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, Pg=page, K=0))
    elem = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Link, Pg=page, K=Array([
        1, Dictionary(Type=Name.OBJR, Pg=page, Obj=link)])))
    _tagged(pdf, page, [line, elem])
    save(pdf, folder, "06_tagged_link_under_a_box.pdf")


def signature_of_curves(folder):
    pdf = pikepdf.Pdf.new()
    add_page(pdf, text("Sample 7: a signature drawn as curves, with nothing else near it.")
             + b"\nq 0 0 0.5 RG 2 w 1 J 120 520 m 150 600 170 600 180 540 c "
             b"190 480 220 480 240 540 c 255 590 280 590 300 530 c "
             b"315 490 340 500 360 540 c S Q")
    save(pdf, folder, "07_signature_of_curves.pdf")


def rule_through_a_box(folder):
    pdf = pikepdf.Pdf.new()
    add_page(pdf, text("Sample 8: a rule runs through a word, from one side of the page to "
                       "the other.", "", [(72, "Before the word"), (240, "TANGO-RULE"),
                                          (370, "after the word")])
             + b"\nq 0.8 0 0 RG 1.5 w 40 677 m 572 677 l S Q")
    save(pdf, folder, "08_rule_through_a_box.pdf")


def turned(folder, number: int, degrees: int, by_the_tree: bool = False):
    pdf = pikepdf.Pdf.new()
    how = "by the page tree" if by_the_tree else "by the page itself"
    page = add_page(pdf, text(f"Sample {number}: this page is turned {degrees} degrees, {how}.", "",
                              [(72, "The code word is"), (210, f"TANGO-{degrees}"),
                               (320, "and nothing else goes.")]))
    # The page tree's /Rotate is written where it is set. (pikepdf moves it
    # down onto the page when it opens the file, so read it with MuPDF.)
    (pdf.Root["/Pages"] if by_the_tree else page)["/Rotate"] = degrees
    save(pdf, folder, f"{number:02d}_turned_{degrees}"
         + ("_by_the_page_tree" if by_the_tree else "") + ".pdf")


def turned_with_crop_box_and_stamp(folder):
    pdf = pikepdf.Pdf.new()
    page = add_page(pdf, text("Sample 11: turned 90 degrees, a crop box that does not start at",
                              "the corner, and a stamp over the word.", "",
                              [(72, "The code word is"), (210, "TANGO-CROP"),
                               (330, "and nothing else goes.")]),
                    Rotate=90, CropBox=[40, 60, 600, 780])
    on_page(page, stamp(pdf, [200, 640, 322, 672], "SEEN"))
    save(pdf, folder, "11_turned_with_crop_box_and_stamp.pdf")


def field_on_two_pages(folder):
    pdf = pikepdf.Pdf.new()
    pages = [add_page(pdf, text(f"Sample 12, page {n}: the field below is one field, shown on "
                                f"page 1 and page 2.")) for n in (1, 2)]
    field = pdf.make_indirect(Dictionary(FT=Name.Tx, T=String("account"), V=String(TERM)))
    widgets = [pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Widget, Parent=field, Rect=[72, 620, 272, 650], P=page,
        DA=String("/Helv 14 Tf 0 g"), AP=Dictionary(N=panel(pdf, TERM, 200))))
        for page in pages]
    field["/Kids"] = Array(widgets)
    for page, widget in zip(pages, widgets, strict=True):
        on_page(page, widget)
    pdf.Root["/AcroForm"] = Dictionary(Fields=Array([field]), DA=String("/Helv 14 Tf 0 g"),
                                       DR=Dictionary(Font=Dictionary(Helv=font(pdf))))
    save(pdf, folder, "12_field_on_two_pages.pdf")


def scan_with_text_layer(folder):
    pdf = pikepdf.Pdf.new()
    scan_page(pdf, ["Sample 13, page 1: a scan with a text layer.", f"Invoice {TERM} is paid.",
                    "Thank you for your order."], layer=True)
    scan_page(pdf, ["Sample 13, page 2: a scan with no text layer.",
                    "Words in it are not searched."], layer=False)
    save(pdf, folder, "13_scan_with_text_layer.pdf")


def text_outside_the_page(folder):
    pdf = pikepdf.Pdf.new()
    add_page(pdf, text("Sample 14: the reference below is also written outside the page.", "",
                       f"The reference {TERM} is on this page.")
             + f"\nBT /F1 14 Tf -400 300 Td ({TERM}) Tj ET".encode())
    save(pdf, folder, "14_text_outside_the_page.pdf")


def xfa_only_form(folder):
    pdf = pikepdf.Pdf.new()
    add_page(pdf, text("Sample 15: this file's form is stored only as XFA."))
    packet = pdf.make_stream(
        f'<xdp:xdp xmlns:xdp="http://ns.adobe.com/xdp/"><xfa:datasets '
        f'xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/"><xfa:data><form>'
        f"<account>{TERM}</account></form></xfa:data></xfa:datasets></xdp:xdp>".encode())
    pdf.Root["/AcroForm"] = Dictionary(Fields=Array(), XFA=packet)
    save(pdf, folder, "15_xfa_only_form.pdf")


def term_in_an_output_intent(folder):
    pdf = pikepdf.Pdf.new()
    add_page(pdf, text("Sample 16: the reference is on this page and in the file's settings.",
                       "", f"The reference {TERM} is on this page."))
    pdf.Root["/OutputIntents"] = Array([Dictionary(
        Type=Name.OutputIntent, S=Name.GTS_PDFX, OutputConditionIdentifier=String(TERM))])
    save(pdf, folder, "16_term_in_the_file_settings.pdf")


def many_places_and_notes(folder):
    """Ninety pages. Every third page is a drawing with no text; the others
    hold the reference twenty times. Also an attachment that holds it, a
    packed attachment that does not, a bookmark, the title, a link and a
    page thumbnail."""
    pdf = pikepdf.Pdf.new()
    for n in range(1, 91):
        if n % 3 == 0:
            add_page(pdf, b"q 0.2 0.4 0.7 rg 72 400 468 300 re f Q")
            continue
        rows = [f"Sample 17, page {n}: many places and every kind of note."] + [
            f"Line {i}: the reference {TERM} appears here, and {TERM} again." for i in range(1, 11)]
        add_page(pdf, text(*rows, size=12, step=20))
    first = pdf.pages[0].obj
    on_page(first, pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Link, Rect=[72, 300, 300, 320], Border=[0, 0, 0],
        A=Dictionary(S=Name.URI, URI=String(f"https://example.org/orders/{TERM}")))))
    thumb = Image.new("RGB", (60, 78), (250, 250, 250))
    first["/Thumb"] = image(pdf, thumb)
    packed = io.BytesIO()
    packed.write(b"PK\x03\x04" + bytes(26) + b"notes.txt" + zlib.compress(b"nothing to see"))
    files = {"notes.zip": packed.getvalue(), "reference.txt": f"order {TERM}\n".encode()}
    tree = []
    for name, data in files.items():
        stream = pdf.make_stream(data, Type=Name.EmbeddedFile)
        tree += [String(name), pdf.make_indirect(Dictionary(
            Type=Name.Filespec, F=String(name), UF=String(name), EF=Dictionary(F=stream)))]
    pdf.Root["/Names"] = Dictionary(EmbeddedFiles=Dictionary(Names=Array(tree)))
    outline = pdf.make_indirect(Dictionary(Type=Name.Outlines, Count=1))
    mark = pdf.make_indirect(Dictionary(Title=String(f"Order {TERM}"), Parent=outline,
                                        Dest=Array([first, Name.Fit])))
    outline["/First"] = outline["/Last"] = mark
    pdf.Root["/Outlines"] = outline
    pdf.trailer["/Info"] = pdf.make_indirect(Dictionary(Title=String(f"Orders for {TERM}")))
    save(pdf, folder, "17_many_places_and_notes.pdf")


def main(folder: str) -> None:
    os.makedirs(folder, exist_ok=True)
    number_across_a_line(folder)
    letter_spaced_heading(folder)
    stamp_over_a_box(folder)
    stamp_that_says_the_term(folder)
    tagged_picture(folder)
    tagged_link(folder)
    signature_of_curves(folder)
    rule_through_a_box(folder)
    turned(folder, 9, 90)
    turned(folder, 10, 180, by_the_tree=True)
    turned_with_crop_box_and_stamp(folder)
    field_on_two_pages(folder)
    scan_with_text_layer(folder)
    text_outside_the_page(folder)
    xfa_only_form(folder)
    term_in_an_output_intent(folder)
    many_places_and_notes(folder)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python tests/manual/make_redact_samples.py <folder>")
    main(sys.argv[1])
