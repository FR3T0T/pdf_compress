"""Files that keep earlier versions of themselves, built by hand (#122).

An incremental save appends to a file: the objects that changed, a new
cross-reference section and a trailer whose /Prev points at the one before.
Everything the earlier version held is still in the file. The updates here
are written byte by byte, not by a library's incremental save, so that what
the tests read does not change with a library's version; the base files are
written by pikepdf with a fixed identifier.

Not a test module (no ``test_`` prefix): the tests import it.
"""

from __future__ import annotations

import re

import pikepdf
from pikepdf import Dictionary, Name

PASSWORD = "user-pw"


def base(path, words: bytes = b"Account ACME-4471 is closed", *, encrypt: bool = False,
         linearize: bool = False, title: str | None = None) -> str:
    """One page of 300 x 200 pt that shows *words*, saved in one go."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1,
                                        BaseFont=Name.Helvetica))
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 300, 200],
                                             Resources=Dictionary(Font=Dictionary(F1=font)))))
    pdf.pages[0].obj["/Contents"] = pdf.make_stream(b"BT /F1 12 Tf 20 100 Td (" + words + b") Tj ET")
    if title is not None:
        pdf.docinfo["/Title"] = title
    kwargs = {}
    if encrypt:
        kwargs["encryption"] = pikepdf.Encryption(user=PASSWORD, owner="owner-pw", R=4, aes=True)
    pdf.save(str(path), linearize=linearize, static_id=True, **kwargs)
    pdf.close()
    return str(path)


def _last_xref(data: bytes) -> int:
    return int(list(re.finditer(rb"startxref\s+(\d+)", data))[-1].group(1))


def append_update(path, objects: dict, password: str = "") -> None:
    """Append an incremental update holding *objects* ({number: body}),
    generation 0, after the file's last cross-reference section."""
    with pikepdf.open(str(path), password=password) as pdf:
        size = int(pdf.trailer.Size)
        root = pdf.Root.objgen
        encrypt = pdf.trailer.get("/Encrypt")
        encrypt = encrypt.objgen if encrypt is not None else None
        info = pdf.trailer.get("/Info")
        info = info.objgen if info is not None and info.is_indirect else None
        ids = [bytes(i) for i in pdf.trailer.ID] if "/ID" in pdf.trailer else None
    data = open(path, "rb").read()
    prev = _last_xref(data)
    if not data.endswith(b"\n"):
        data += b"\n"
    offsets = {}
    for num, body in sorted(objects.items()):
        offsets[num] = len(data)
        data += b"%d 0 obj\n" % num + body + b"\nendobj\n"
    at = len(data)
    data += b"xref\n" + b"".join(b"%d 1\n%010d 00000 n \n" % (num, off)
                                 for num, off in sorted(offsets.items()))
    trailer = b"<< /Size %d /Root %d %d R /Prev %d" % (max(size, max(objects) + 1), *root, prev)
    if encrypt:
        trailer += b" /Encrypt %d %d R" % encrypt
    if info:
        trailer += b" /Info %d %d R" % info
    if ids:
        trailer += b" /ID [<%s> <%s>]" % (ids[0].hex().encode(), ids[1].hex().encode())
    data += b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % at
    with open(path, "wb") as fh:
        fh.write(data)


def rewrite_page(path, words: bytes = b"Account is closed") -> str:
    """Append an update in which the page shows *words* instead: what an
    editor that saves incrementally writes when something is taken off a
    page. The earlier content stays in the file. (Unencrypted files only:
    the new stream is written as it is.)"""
    with pikepdf.open(str(path)) as pdf:
        size = int(pdf.trailer.Size)
        page = pdf.pages[0].obj
        number, parent = page.objgen[0], page.Parent.objgen[0]
        font = page.Resources.Font.F1.objgen[0]
    content = b"BT /F1 12 Tf 20 100 Td (" + words + b") Tj ET"
    append_update(path, {
        size: b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        number: (b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 300 200] "
                 b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>")
                % (parent, font, size),
    })
    return str(path)


def change_layout(path, password: str = "") -> str:
    """Append an update that rewrites the catalog, as it is, with
    /PageLayout added. The catalog of these files holds no string where the
    file is encrypted, so the update needs no encryption."""
    with pikepdf.open(str(path), password=password) as pdf:
        root = pdf.Root.objgen[0]
        body = pdf.Root.unparse(resolved=True)
    assert body.rstrip().endswith(b">>")
    append_update(path, {root: body.rstrip()[:-2] + b" /PageLayout /SinglePage >>"}, password)
    return str(path)


def break_last_xref(path) -> str:
    """Point the last startxref nowhere, so a reader must rebuild the file's
    cross-references to open it (MuPDF says it repaired the file)."""
    data = open(path, "rb").read()
    data = re.sub(rb"startxref\s+\d+\s+%%EOF\s*$", b"startxref\n999999\n%%EOF\n", data)
    with open(path, "wb") as fh:
        fh.write(data)
    return str(path)


def drop_first_end(path) -> str:
    """Take out the first version's "startxref … %%EOF", so the file has one
    of each, though its updated objects are still defined twice."""
    data = open(path, "rb").read()
    data = re.sub(rb"startxref\s+\d+\s+%%EOF\s*", b"", data, count=1)
    with open(path, "wb") as fh:
        fh.write(data)
    return str(path)


def first_version(path) -> bytes:
    """The file as it was before its first incremental update: everything up
    to the first %%EOF."""
    data = open(path, "rb").read()
    return data[:data.index(b"%%EOF") + 5] + b"\n"
