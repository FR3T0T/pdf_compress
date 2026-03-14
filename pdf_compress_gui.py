#!/usr/bin/env python3
"""
██████████████████████████████████████████████████████████████████████
  DEPRECATED — This is the legacy v1.x tkinter GUI.
  It uses an older engine WITHOUT:
    - DPI-aware compression (uses a broken heuristic)
    - XObject deduplication (causes generation loss)
    - Grayscale preservation (forces all images to RGB)
    - Soft mask / transparency handling
    - Decompression bomb protection

  Use  app.py  (PySide6 GUI) instead.
██████████████████████████████████████████████████████████████████████

PDF Compress — Desktop GUI (legacy tkinter)
Requires: pip install pikepdf pillow
"""

import sys
import warnings
warnings.warn(
    "pdf_compress_gui.py is deprecated. Use app.py (PySide6) instead.",
    DeprecationWarning,
    stacklevel=2,
)

# ── DPI fix — MUST run before tkinter import ───────────────────────
import os
if sys.platform == "win32":
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

import io, threading, subprocess, tempfile
import tkinter as tk
from tkinter import filedialog, ttk

try:
    import pikepdf
    from PIL import Image
except ImportError:
    import tkinter.messagebox as mb
    root = tk.Tk(); root.withdraw()
    mb.showerror("Missing dependencies", "Run this first:\n\npip install pikepdf pillow")
    sys.exit(1)


# ── Palette — muted, warm neutrals ────────────────────────────────
BG       = "#111113"
SURFACE  = "#19191c"
BORDER   = "#27272b"
ACCENT   = "#a08a5e"
ACCENT2  = "#c4a96a"
ACCENT_D = "#6b5f42"
TEXT     = "#e8e6e1"
TEXT2    = "#9b9a97"
TEXT3    = "#5c5c5f"
GREEN    = "#5ea07a"
RED      = "#c45c5c"
AMBER    = "#c4a24c"

_F = "Segoe UI" if sys.platform == "win32" else "SF Pro Display" if sys.platform == "darwin" else "Cantarell"


def fmt(b):
    if b < 1024:    return f"{b} B"
    if b < 1048576: return f"{b/1024:.1f} KB"
    return f"{b/1048576:.2f} MB"


def q_label(pct):
    if pct < 15: return "Minimum"
    if pct < 35: return "Low"
    if pct < 65: return "Medium"
    if pct < 85: return "High"
    return "Maximum"


def q_settings(pct):
    jpeg = max(10, min(95, int(pct * 0.85 + 10)))
    dpi  = max(48, min(220, int(pct * 1.72 + 48)))
    return jpeg, dpi


def count_images(pdf):
    n = 0
    for page in pdf.pages:
        if "/Resources" not in page: continue
        res = page["/Resources"]
        if "/XObject" not in res: continue
        for k in res["/XObject"]:
            if res["/XObject"][k].get("/Subtype") == "/Image":
                n += 1
    return n


def compress_images(pdf, jpeg_quality, max_dpi, cb=None):
    total = count_images(pdf)
    cur = 0
    for page in pdf.pages:
        if "/Resources" not in page: continue
        res = page["/Resources"]
        if "/XObject" not in res: continue
        xo = res["/XObject"]
        for key in list(xo.keys()):
            if xo[key].get("/Subtype") != "/Image": continue
            cur += 1
            try:
                img = Image.open(io.BytesIO(bytes(xo[key].read_raw_bytes())))
                w, h = img.size
                s = min(1.0, (max_dpi * 10) / max(w, h))
                if s < 1.0:
                    img = img.resize((int(w*s), int(h*s)), Image.LANCZOS)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
                buf.seek(0)
                xo[key] = pikepdf.Stream(pdf, buf.read())
                xo[key]["/Type"]             = pikepdf.Name("/XObject")
                xo[key]["/Subtype"]          = pikepdf.Name("/Image")
                xo[key]["/ColorSpace"]       = pikepdf.Name("/DeviceRGB")
                xo[key]["/BitsPerComponent"] = 8
                xo[key]["/Width"]            = img.width
                xo[key]["/Height"]           = img.height
                xo[key]["/Filter"]           = pikepdf.Name("/DCTDecode")
            except Exception:
                pass
            if cb:
                cb(cur, total)
    return cur, total


def do_compress(input_path, output_path, jpeg, dpi, cb=None):
    orig = os.path.getsize(input_path)
    d = os.path.dirname(output_path) or "."
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=d)
    try:
        os.close(fd)
        with pikepdf.open(input_path) as pdf:
            compress_images(pdf, jpeg, dpi, cb)
            pdf.remove_unreferenced_resources()
            pdf.save(tmp, compress_streams=True,
                     object_stream_mode=pikepdf.ObjectStreamMode.generate)
        comp = os.path.getsize(tmp)
        if comp >= orig:
            os.remove(tmp)
            return orig, orig, True
        if os.path.exists(output_path):
            os.remove(output_path)
        os.rename(tmp, output_path)
        return orig, comp, False
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


class Slider(tk.Canvas):
    def __init__(self, parent, value=60, on_change=None, **kw):
        super().__init__(parent, height=28, bg=SURFACE,
                         highlightthickness=0, cursor="hand2", **kw)
        self.val = value
        self.cb  = on_change
        self.bind("<Configure>",       self._draw)
        self.bind("<ButtonPress-1>",   self._click)
        self.bind("<B1-Motion>",       self._move)

    def _draw(self, *_):
        self.delete("all")
        w = self.winfo_width()
        if w < 20: return
        pad, y = 8, 14
        x = pad + (self.val / 100) * (w - 2 * pad)
        self.create_line(pad, y, w - pad, y, fill=BORDER, width=2, capstyle="round")
        if x > pad + 1:
            self.create_line(pad, y, x, y, fill=ACCENT_D, width=2, capstyle="round")
        self.create_oval(x-7, y-7, x+7, y+7, fill=ACCENT, outline="")
        self.create_oval(x-3, y-3, x+3, y+3, fill=BG, outline="")

    def _click(self, e): self._set(e.x)
    def _move(self, e):  self._set(e.x)

    def _set(self, x):
        w = self.winfo_width()
        pad = 8
        v = max(0, min(100, round((x - pad) / (w - 2 * pad) * 100)))
        if v != self.val:
            self.val = v
            self._draw()
            if self.cb: self.cb(v)

    def set(self, v):
        self.val = int(v)
        self._draw()


class FileRow(tk.Frame):
    def __init__(self, parent, path, on_remove, **kw):
        super().__init__(parent, bg=SURFACE, **kw)
        self.filepath = path
        self.grid_columnconfigure(1, weight=1)
        self.icon = tk.Label(self, text="·", font=(_F, 10), bg=SURFACE, fg=TEXT3, width=2)
        self.icon.grid(row=0, column=0, rowspan=2, padx=(12, 4), pady=6)
        name = os.path.basename(path)
        tk.Label(self, text=name if len(name) < 48 else name[:45] + "…",
                 font=(_F, 9), bg=SURFACE, fg=TEXT, anchor="w",
                 ).grid(row=0, column=1, sticky="w", padx=0, pady=(6, 0))
        sz = os.path.getsize(path)
        self.status = tk.Label(self, text=fmt(sz), font=(_F, 8), bg=SURFACE, fg=TEXT3, anchor="w")
        self.status.grid(row=1, column=1, sticky="w", padx=0, pady=(0, 6))
        self.rm_btn = tk.Button(self, text="×", font=(_F, 9), bg=SURFACE, fg=TEXT3,
                                relief="flat", bd=0, cursor="hand2", activebackground=BORDER,
                                command=lambda: on_remove(self))
        self.rm_btn.grid(row=0, column=2, rowspan=2, padx=(4, 10), pady=6)
        tk.Frame(self, bg=BORDER, height=1).grid(row=2, column=0, columnspan=3, sticky="ew")

    def set_working(self):
        self.icon.config(text="◌", fg=ACCENT)
        self.status.config(text="Compressing…", fg=ACCENT)
        self.rm_btn.config(state="disabled")

    def set_progress(self, cur, tot):
        self.status.config(text=f"Image {cur}/{tot}")

    def set_done(self, orig, comp, skip):
        self.rm_btn.config(state="normal")
        if skip:
            self.icon.config(text="–", fg=AMBER)
            self.status.config(text=f"{fmt(orig)} · already optimized", fg=AMBER)
        else:
            pct = (1 - comp / orig) * 100
            self.icon.config(text="✓", fg=GREEN)
            self.status.config(text=f"{fmt(orig)} → {fmt(comp)}  ·  {pct:.1f}% smaller", fg=GREEN)

    def set_error(self, msg):
        self.rm_btn.config(state="normal")
        self.icon.config(text="×", fg=RED)
        self.status.config(text=msg[:55], fg=RED)


class App(tk.Tk):
    def __init__(self, initial_files=None):
        super().__init__()
        self.title("PDF Compress (LEGACY — use app.py)")
        self.configure(bg=BG)
        self.rows    = []
        self.quality = 60
        self.out_dir = None
        self.running = False
        self._build()
        w, h = 480, 620
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(400, 480)
        if initial_files:
            self.after(150, lambda: self._add(initial_files))

    def _build(self):
        pad = 24

        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=pad, pady=(pad, 0))
        tk.Label(hdr, text="PDF Compress", font=(_F, 14), bg=BG, fg=TEXT).pack(side="left")
        tk.Label(hdr, text="LEGACY", font=(_F, 8), bg=BG, fg=RED).pack(side="left", padx=(8, 0), pady=(3, 0))

        tk.Label(self, text="⚠ This GUI is deprecated — use app.py instead",
                 font=(_F, 8), bg=BG, fg=RED).pack(anchor="w", padx=pad, pady=(2, 0))

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=pad, pady=(12, 0))

        self.file_area = tk.Frame(self, bg=BG)
        self.file_area.pack(fill="both", expand=True, padx=pad, pady=(12, 0))

        self.drop = tk.Frame(self.file_area, bg=SURFACE,
                             highlightbackground=BORDER, highlightthickness=1)
        self.drop_c = tk.Frame(self.drop, bg=SURFACE)
        self.drop_c.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(self.drop_c, text="+", font=(_F, 20), bg=SURFACE, fg=ACCENT_D).pack()
        tk.Label(self.drop_c, text="Select PDFs", font=(_F, 10), bg=SURFACE, fg=TEXT).pack(pady=(2, 0))
        tk.Label(self.drop_c, text="or drag onto .bat to launch with files",
                 font=(_F, 8), bg=SURFACE, fg=TEXT3).pack(pady=(2, 0))
        for w in [self.drop, self.drop_c] + list(self.drop_c.winfo_children()):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e: self._browse())

        self.list_wrap = tk.Frame(self.file_area, bg=SURFACE,
                                  highlightbackground=BORDER, highlightthickness=1)
        self.list_cv = tk.Canvas(self.list_wrap, bg=SURFACE, highlightthickness=0, bd=0)
        self.list_sb = tk.Scrollbar(self.list_wrap, orient="vertical", command=self.list_cv.yview)
        self.list_in = tk.Frame(self.list_cv, bg=SURFACE)
        self.list_in.bind("<Configure>",
                          lambda e: self.list_cv.configure(scrollregion=self.list_cv.bbox("all")))
        self.list_cv.create_window((0, 0), window=self.list_in, anchor="nw", tags="win")
        self.list_cv.configure(yscrollcommand=self.list_sb.set)
        self.list_cv.pack(side="left", fill="both", expand=True)
        self.list_sb.pack(side="right", fill="y")
        self.list_cv.bind("<Configure>",
                          lambda e: self.list_cv.itemconfig("win", width=e.width))

        def _wheel(e):
            self.list_cv.yview_scroll(-1 * (e.delta // 120), "units")
        self.list_cv.bind("<MouseWheel>", _wheel)
        self.list_in.bind("<MouseWheel>", _wheel)

        self._show_drop()

        self.count_lbl = tk.Label(self, text="", font=(_F, 8), bg=BG, fg=TEXT3, anchor="w")
        self.count_lbl.pack(fill="x", padx=pad, pady=(8, 0))

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=pad, pady=(8, 0))

        qf = tk.Frame(self, bg=BG)
        qf.pack(fill="x", padx=pad, pady=(12, 0))
        qt = tk.Frame(qf, bg=BG)
        qt.pack(fill="x")
        tk.Label(qt, text="Quality", font=(_F, 9), bg=BG, fg=TEXT2).pack(side="left")
        self.q_det = tk.Label(qt, text="", font=(_F, 8), bg=BG, fg=TEXT3)
        self.q_det.pack(side="right")
        self.q_val = tk.Label(qt, text="", font=(_F, 9), bg=BG, fg=ACCENT)
        self.q_val.pack(side="right", padx=(0, 12))
        self.slider = Slider(qf, value=60, on_change=self._on_q)
        self.slider.pack(fill="x", pady=(6, 0))
        tf = tk.Frame(qf, bg=BG)
        tf.pack(fill="x", pady=(2, 0))
        for t in ["Smallest", "", "", "", "Best quality"]:
            tk.Label(tf, text=t, font=(_F, 7), bg=BG, fg=TEXT3).pack(side="left", expand=True)
        self._upd_q()

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=pad, pady=(10, 0))

        of = tk.Frame(self, bg=BG)
        of.pack(fill="x", padx=pad, pady=(10, 0))
        tk.Label(of, text="Output", font=(_F, 9), bg=BG, fg=TEXT2).pack(side="left")
        self.out_lbl = tk.Label(of, text="Same folder as input", font=(_F, 8), bg=BG, fg=TEXT3)
        self.out_lbl.pack(side="left", padx=(10, 0))
        tk.Button(of, text="Reset", font=(_F, 7), bg=BG, fg=TEXT3, relief="flat", bd=0,
                  cursor="hand2", activebackground=BG, command=self._reset_out).pack(side="right")
        tk.Button(of, text="Change", font=(_F, 7), bg=BORDER, fg=TEXT2, relief="flat", bd=0,
                  cursor="hand2", padx=8, pady=2, activebackground=BORDER,
                  command=self._pick_out).pack(side="right", padx=(0, 6))

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=pad, pady=(10, 0))

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=pad, pady=(12, 0))
        self.btn_add = tk.Button(bar, text="+ Add", font=(_F, 9), bg=BORDER, fg=TEXT,
                                 relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
                                 activebackground=BORDER, activeforeground=TEXT,
                                 command=self._browse)
        self.btn_add.pack(side="left")
        self.btn_clr = tk.Button(bar, text="Clear", font=(_F, 8), bg=BG, fg=TEXT3,
                                 relief="flat", bd=0, padx=8, pady=6, cursor="hand2",
                                 activebackground=BG, command=self._clear)
        self.btn_clr.pack(side="left", padx=(6, 0))
        self.btn_go = tk.Button(bar, text="Compress", font=(_F, 10), bg=ACCENT, fg="#0a0a0b",
                                relief="flat", bd=0, padx=20, pady=7, cursor="hand2",
                                activebackground=ACCENT2, activeforeground="#0a0a0b",
                                command=self._run, state="disabled")
        self.btn_go.pack(side="right")
        self.btn_open = tk.Button(bar, text="Open folder", font=(_F, 8), bg=BG, fg=TEXT3,
                                  relief="flat", bd=0, padx=8, pady=6, cursor="hand2",
                                  activebackground=BG, command=self._open_folder)

        sty = ttk.Style(self)
        sty.theme_use("clam")
        sty.configure("A.Horizontal.TProgressbar", troughcolor=BORDER, background=ACCENT, thickness=3)
        self.prog = ttk.Progressbar(self, mode="determinate", maximum=100,
                                    style="A.Horizontal.TProgressbar")

        self.result = tk.Label(self, text="", font=(_F, 8), bg=BG, fg=GREEN, wraplength=440)
        self.result.pack(padx=pad, pady=(6, pad))

    def _show_drop(self):
        self.list_wrap.pack_forget()
        self.drop.pack(fill="both", expand=True)

    def _show_list(self):
        self.drop.pack_forget()
        self.list_wrap.pack(fill="both", expand=True)

    def _upd_count(self):
        n = len(self.rows)
        if not n:
            self.count_lbl.config(text="")
        else:
            total = sum(os.path.getsize(r.filepath) for r in self.rows)
            self.count_lbl.config(text=f"{n} file{'s' if n != 1 else ''}  ·  {fmt(total)}")

    def _browse(self):
        if self.running: return
        paths = filedialog.askopenfilenames(title="Select PDFs",
                                            filetypes=[("PDF", "*.pdf"), ("All", "*.*")])
        if paths: self._add(list(paths))

    def _add(self, paths):
        have = {r.filepath for r in self.rows}
        new = [p for p in paths if p.lower().endswith(".pdf") and os.path.isfile(p) and p not in have]
        if not new: return
        if not self.rows: self._show_list()
        for p in new:
            row = FileRow(self.list_in, p, on_remove=self._remove)
            row.pack(fill="x")
            self.rows.append(row)
        self._upd_count()
        self.btn_go.config(state="normal")
        self.result.config(text="")
        self.btn_open.pack_forget()

    def _remove(self, row):
        if self.running: return
        row.pack_forget(); row.destroy()
        self.rows.remove(row)
        if not self.rows:
            self._show_drop()
            self.btn_go.config(state="disabled")
        self._upd_count()

    def _clear(self):
        if self.running: return
        for r in list(self.rows):
            r.pack_forget(); r.destroy()
        self.rows.clear()
        self._show_drop()
        self.btn_go.config(state="disabled")
        self._upd_count()
        self.result.config(text="")
        self.btn_open.pack_forget()

    def _pick_out(self):
        d = filedialog.askdirectory(title="Output folder")
        if d:
            self.out_dir = d
            self.out_lbl.config(text=d if len(d) < 40 else "…" + d[-37:])

    def _reset_out(self):
        self.out_dir = None
        self.out_lbl.config(text="Same folder as input")

    def _open_folder(self):
        if not self.rows: return
        folder = self.out_dir or os.path.dirname(self.rows[0].filepath)
        if sys.platform == "win32":    os.startfile(folder)
        elif sys.platform == "darwin": subprocess.Popen(["open", folder])
        else:                          subprocess.Popen(["xdg-open", folder])

    def _on_q(self, v):
        self.quality = int(v)
        self._upd_q()

    def _upd_q(self):
        jpeg, dpi = q_settings(self.quality)
        self.q_val.config(text=f"{self.quality}%  {q_label(self.quality)}")
        self.q_det.config(text=f"{dpi} DPI · JPEG {jpeg}%")

    def _run(self):
        if self.running or not self.rows: return
        self.running = True
        self.btn_go.config(state="disabled", text="Compressing…")
        self.btn_add.config(state="disabled")
        self.btn_clr.config(state="disabled")
        self.btn_open.pack_forget()
        self.result.config(text="")
        self.prog.pack(fill="x", padx=24, pady=(2, 0))
        self.prog["value"] = 0
        jpeg, dpi = q_settings(self.quality)
        threading.Thread(target=self._work, args=(jpeg, dpi), daemon=True).start()

    def _work(self, jpeg, dpi):
        nf = len(self.rows)
        to = tc = 0
        nk = ns = 0
        for i, row in enumerate(self.rows):
            if self.out_dir:
                nm, ext = os.path.splitext(os.path.basename(row.filepath))
                out = os.path.join(self.out_dir, f"{nm}_compressed{ext}")
            else:
                b, ext = os.path.splitext(row.filepath)
                out = f"{b}_compressed{ext}"
            self.after(0, row.set_working)

            def cb(cur, tot, _r=row, _i=i):
                self.after(0, _r.set_progress, cur, tot)
                fp = (_i / nf) * 100 + (cur / max(tot, 1)) * (100 / nf)
                self.after(0, lambda v=fp: self.prog.configure(value=min(100, v)))

            try:
                o, c, skip = do_compress(row.filepath, out, jpeg, dpi, cb)
                to += o; tc += c
                if skip: ns += 1
                else:    nk += 1
                self.after(0, row.set_done, o, c, skip)
            except Exception as e:
                self.after(0, row.set_error, str(e))
        self.after(0, self._done, to, tc, nk, ns)

    def _done(self, to, tc, nk, ns):
        self.running = False
        self.prog.pack_forget()
        self.btn_go.config(state="normal", text="Compress")
        self.btn_add.config(state="normal")
        self.btn_clr.config(state="normal")
        sv = to - tc
        parts = []
        if nk:  parts.append(f"{nk} compressed")
        if ns:  parts.append(f"{ns} already optimized")
        if sv > 0 and to > 0:
            parts.append(f"saved {fmt(sv)} ({sv/to*100:.0f}%)")
        self.result.config(text="  ·  ".join(parts), fg=GREEN if nk else AMBER)
        self.count_lbl.config(text="  ·  ".join(parts))
        self.btn_open.pack(side="right", padx=(6, 0))


if __name__ == "__main__":
    init = [f for f in sys.argv[1:] if f.lower().endswith(".pdf") and os.path.isfile(f)]
    App(initial_files=init or None).mainloop()
