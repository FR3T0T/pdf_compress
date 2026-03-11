#!/usr/bin/env python3
"""
PDF Compress — Desktop GUI
Requires: pip install pikepdf pillow
"""

import io, os, sys, threading, tkinter as tk
from tkinter import filedialog, ttk

try:
    import pikepdf
    from PIL import Image
except ImportError:
    import tkinter.messagebox as mb
    root = tk.Tk(); root.withdraw()
    mb.showerror("Missing dependencies", "Run this first:\n\npip install pikepdf pillow")
    sys.exit(1)

# ── Palette ────────────────────────────────────────────────────────
BG       = "#0c0c0d"
CARD     = "#141416"
BORDER   = "#222226"
BORDER2  = "#2e2e34"
GOLD     = "#c9a84c"
GOLD_HV  = "#e0bc6a"
GOLD_DIM = "#7a6230"
TEXT     = "#f0ede8"
SUB      = "#6b6b72"
SUB2     = "#9a9aa4"
GREEN    = "#4caf7d"
RED      = "#e05c5c"
AMBER    = "#e8a84c"

F_TITLE  = ("Segoe UI", 22, "bold")
F_LABEL  = ("Segoe UI", 9)
F_BOLD   = ("Segoe UI", 10, "bold")
F_MONO   = ("Consolas", 10)
F_SM     = ("Segoe UI", 8)
F_MED    = ("Segoe UI", 11)
F_LARGE  = ("Segoe UI", 14, "bold")


def fmt_size(b):
    if b < 1024: return f"{b} B"
    if b < 1048576: return f"{b/1024:.1f} KB"
    return f"{b/1048576:.2f} MB"


def quality_to_settings(pct):
    """Convert 0–100% → jpeg quality and max_dpi"""
    jpeg = max(10, min(95, int(pct * 0.85 + 10)))
    dpi  = max(48, min(220, int(pct * 1.72 + 48)))
    return jpeg, dpi


def estimate_size(original, pct):
    ratio = 0.15 + (pct / 100) * 0.75
    return int(original * ratio)


def quality_label(pct):
    if pct < 20:  return "Minimum"
    if pct < 40:  return "Low"
    if pct < 60:  return "Medium"
    if pct < 80:  return "High"
    return "Maximum"


def compress_images_in_pdf(pdf, jpeg_quality, max_dpi):
    for page in pdf.pages:
        if "/Resources" not in page: continue
        resources = page["/Resources"]
        if "/XObject" not in resources: continue
        xobjects = resources["/XObject"]
        for key in list(xobjects.keys()):
            xobj = xobjects[key]
            if xobj.get("/Subtype") != "/Image": continue
            try:
                raw = bytes(xobj.read_raw_bytes())
                img = Image.open(io.BytesIO(raw))
                w, h = img.size
                scale = min(1.0, (max_dpi * 10) / max(w, h))
                if scale < 1.0:
                    img = img.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
                if img.mode in ("RGBA","P","LA"): img = img.convert("RGB")
                elif img.mode != "RGB": img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
                buf.seek(0)
                xobjects[key] = pikepdf.Stream(pdf, buf.read())
                xobjects[key]["/Type"]             = pikepdf.Name("/XObject")
                xobjects[key]["/Subtype"]          = pikepdf.Name("/Image")
                xobjects[key]["/ColorSpace"]       = pikepdf.Name("/DeviceRGB")
                xobjects[key]["/BitsPerComponent"] = 8
                xobjects[key]["/Width"]            = img.width
                xobjects[key]["/Height"]           = img.height
                xobjects[key]["/Filter"]           = pikepdf.Name("/DCTDecode")
            except Exception:
                continue


# ── Custom Widgets ──────────────────────────────────────────────────

class PercentSlider(tk.Canvas):
    """A custom slider that returns 0–100 and draws itself nicely."""
    def __init__(self, parent, value=60, on_change=None, **kw):
        super().__init__(parent, height=36, bg=CARD,
                         highlightthickness=0, cursor="hand2", **kw)
        self.value     = value
        self.on_change = on_change
        self.dragging  = False
        self.bind("<Configure>",       self._draw)
        self.bind("<ButtonPress-1>",   self._press)
        self.bind("<B1-Motion>",       self._drag)
        self.bind("<ButtonRelease-1>", self._release)

    def _x_from_val(self, w):
        pad = 14
        return pad + (self.value / 100) * (w - 2*pad)

    def _val_from_x(self, x, w):
        pad = 14
        return max(0, min(100, round((x - pad) / (w - 2*pad) * 100)))

    def _draw(self, *_):
        self.delete("all")
        w = self.winfo_width()
        if w < 2: return
        cx = self._x_from_val(w)
        track_y = 18
        pad = 14

        # Track background
        self.create_rounded_rect(pad, track_y-3, w-pad, track_y+3, 3, fill=BORDER2, outline="")
        # Track fill
        self.create_rounded_rect(pad, track_y-3, cx, track_y+3, 3, fill=GOLD_DIM, outline="")
        # Thumb shadow
        self.create_oval(cx-11, track_y-11, cx+11, track_y+11, fill="#000000", outline="", stipple="gray50")
        # Thumb
        self.create_oval(cx-10, track_y-10, cx+10, track_y+10, fill=GOLD, outline="")
        # Thumb inner
        self.create_oval(cx-4, track_y-4, cx+4, track_y+4, fill=BG, outline="")

    def create_rounded_rect(self, x1, y1, x2, y2, r, **kw):
        self.create_arc(x1, y1, x1+2*r, y1+2*r, start=90,  extent=90,  style="pieslice", **kw)
        self.create_arc(x2-2*r, y1, x2, y1+2*r, start=0,   extent=90,  style="pieslice", **kw)
        self.create_arc(x1, y2-2*r, x1+2*r, y2, start=180, extent=90,  style="pieslice", **kw)
        self.create_arc(x2-2*r, y2-2*r, x2, y2, start=270, extent=90,  style="pieslice", **kw)
        self.create_rectangle(x1+r, y1, x2-r, y2, **kw)
        self.create_rectangle(x1, y1+r, x2, y2-r, **kw)

    def _press(self, e):
        self.dragging = True
        self._update(e.x)

    def _drag(self, e):
        if self.dragging: self._update(e.x)

    def _release(self, e):
        self.dragging = False

    def _update(self, x):
        w = self.winfo_width()
        new_val = self._val_from_x(x, w)
        if new_val != self.value:
            self.value = new_val
            self._draw()
            if self.on_change:
                self.on_change(new_val)

    def set(self, val):
        self.value = int(val)
        self._draw()


class Card(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=CARD,
                         highlightbackground=BORDER,
                         highlightthickness=1, **kw)


# ── Main App ────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF Compress")
        self.configure(bg=BG)
        self.resizable(False, False)

        self.input_path  = None
        self.output_path = None
        self.quality_pct = 60
        self.orig_size   = 0

        self._build()
        self.update_idletasks()
        self.geometry(f"480x700+{(self.winfo_screenwidth()-480)//2}+{(self.winfo_screenheight()-700)//2}")

    # ── Build ────────────────────────────────────────────────────────

    def _build(self):
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True, padx=20, pady=20)

        # ── Header
        hdr = tk.Frame(root, bg=BG)
        hdr.pack(fill="x", pady=(0, 4))
        tk.Label(hdr, text="PDF Compress", font=F_TITLE,
                 bg=BG, fg=TEXT).pack(side="left")
        tk.Label(hdr, text="  offline", font=F_LABEL,
                 bg=BG, fg=GOLD).pack(side="left", pady=(8, 0))

        tk.Label(root, text="No files leave your machine. No internet required.",
                 font=F_LABEL, bg=BG, fg=SUB).pack(anchor="w")

        self._gap(root, 14)

        # ── File picker card
        self.file_card = Card(root, cursor="hand2")
        self.file_card.pack(fill="x")
        self.file_card.bind("<Button-1>", lambda e: self._pick_file())

        fc_inner = tk.Frame(self.file_card, bg=CARD)
        fc_inner.pack(fill="x", padx=18, pady=18)
        fc_inner.bind("<Button-1>", lambda e: self._pick_file())

        self.file_icon = tk.Label(fc_inner, text="＋", font=("Segoe UI", 26),
                                  bg=CARD, fg=GOLD, cursor="hand2")
        self.file_icon.pack()
        self.file_icon.bind("<Button-1>", lambda e: self._pick_file())

        self.file_name_lbl = tk.Label(fc_inner, text="Click to select a PDF",
                                      font=F_MED, bg=CARD, fg=TEXT, cursor="hand2")
        self.file_name_lbl.pack(pady=(6, 2))
        self.file_name_lbl.bind("<Button-1>", lambda e: self._pick_file())

        self.file_meta_lbl = tk.Label(fc_inner, text="",
                                      font=F_LABEL, bg=CARD, fg=SUB, cursor="hand2")
        self.file_meta_lbl.pack()
        self.file_meta_lbl.bind("<Button-1>", lambda e: self._pick_file())

        self._gap(root, 10)

        # ── Info row (size + pages)
        self.info_card = Card(root)
        self.info_card.pack(fill="x")
        self.info_row = tk.Frame(self.info_card, bg=CARD)
        self.info_row.pack(fill="x", padx=18, pady=14)

        self.col_orig  = self._stat_col(self.info_row, "ORIGINAL SIZE", "—")
        tk.Frame(self.info_row, bg=BORDER, width=1).pack(side="left", fill="y", padx=16)
        self.col_est   = self._stat_col(self.info_row, "EST. OUTPUT", "—")
        tk.Frame(self.info_row, bg=BORDER, width=1).pack(side="left", fill="y", padx=16)
        self.col_pages = self._stat_col(self.info_row, "PAGES", "—")
        tk.Frame(self.info_row, bg=BORDER, width=1).pack(side="left", fill="y", padx=16)
        self.col_save  = self._stat_col(self.info_row, "EST. SAVING", "—")

        self._gap(root, 10)

        # ── Quality card
        q_card = Card(root)
        q_card.pack(fill="x")
        q_inner = tk.Frame(q_card, bg=CARD)
        q_inner.pack(fill="x", padx=18, pady=(16, 18))

        # Quality header row
        qh = tk.Frame(q_inner, bg=CARD)
        qh.pack(fill="x", pady=(0, 10))
        tk.Label(qh, text="QUALITY", font=("Segoe UI", 8, "bold"),
                 bg=CARD, fg=SUB).pack(side="left")

        self.pct_frame = tk.Frame(qh, bg=CARD)
        self.pct_frame.pack(side="right")
        self.pct_lbl = tk.Label(self.pct_frame, text="60", font=("Segoe UI", 20, "bold"),
                                bg=CARD, fg=GOLD)
        self.pct_lbl.pack(side="left")
        tk.Label(self.pct_frame, text="%", font=("Segoe UI", 12),
                 bg=CARD, fg=GOLD_DIM).pack(side="left", pady=(6, 0))
        self.quality_name_lbl = tk.Label(self.pct_frame, text="  Medium",
                                         font=F_LABEL, bg=CARD, fg=SUB2)
        self.quality_name_lbl.pack(side="left", pady=(8, 0))

        # Slider
        self.slider = PercentSlider(q_inner, value=60, on_change=self._on_quality,
                                    width=420)
        self.slider.pack(fill="x", pady=(0, 8))

        # Tick labels
        ticks = tk.Frame(q_inner, bg=CARD)
        ticks.pack(fill="x")
        for lbl in ["0%", "25%", "50%", "75%", "100%"]:
            tk.Label(ticks, text=lbl, font=F_SM, bg=CARD,
                     fg=SUB).pack(side="left", expand=True)

        self._gap(root, 10)

        # ── Output row
        out_card = Card(root)
        out_card.pack(fill="x")
        out_inner = tk.Frame(out_card, bg=CARD)
        out_inner.pack(fill="x", padx=18, pady=12)
        tk.Label(out_inner, text="OUTPUT", font=("Segoe UI", 8, "bold"),
                 bg=CARD, fg=SUB).pack(side="left")
        self.out_lbl = tk.Label(out_inner, text="Select a file first",
                                font=F_LABEL, bg=CARD, fg=SUB2)
        self.out_lbl.pack(side="left", padx=10)
        self.btn_change = tk.Button(out_inner, text="Change", font=F_SM,
                                    bg=BORDER2, fg=SUB2, relief="flat",
                                    activebackground=BORDER, activeforeground=TEXT,
                                    cursor="hand2", bd=0, padx=10, pady=4,
                                    command=self._pick_output)
        self.btn_change.pack(side="right")

        self._gap(root, 14)

        # ── Compress button
        self.btn = tk.Button(root, text="Compress PDF",
                             font=("Segoe UI", 12, "bold"),
                             bg=GOLD, fg="#0a0a0b",
                             activebackground=GOLD_HV,
                             activeforeground="#0a0a0b",
                             relief="flat", cursor="hand2",
                             bd=0, pady=15,
                             command=self._run, state="disabled")
        self.btn.pack(fill="x")

        # ── Progress
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("P.Horizontal.TProgressbar",
                        troughcolor=BORDER, background=GOLD, thickness=3)
        self.prog = ttk.Progressbar(root, mode="indeterminate",
                                    style="P.Horizontal.TProgressbar")

        # ── Result label
        self.result_lbl = tk.Label(root, text="", font=F_LABEL,
                                   bg=BG, fg=GREEN, wraplength=440, justify="center")
        self.result_lbl.pack(pady=(10, 0))

    # ── Helpers ──────────────────────────────────────────────────────

    def _gap(self, parent, h):
        tk.Frame(parent, bg=BG, height=h).pack(fill="x")

    def _stat_col(self, parent, title, value):
        f = tk.Frame(parent, bg=CARD)
        f.pack(side="left", expand=True)
        tk.Label(f, text=title, font=("Segoe UI", 7, "bold"),
                 bg=CARD, fg=SUB).pack()
        lbl = tk.Label(f, text=value, font=("Segoe UI", 13, "bold"),
                       bg=CARD, fg=TEXT)
        lbl.pack()
        return lbl

    # ── Events ───────────────────────────────────────────────────────

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Select PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
        )
        if not path: return
        self.input_path = path
        base, ext = os.path.splitext(path)
        self.output_path = f"{base}_compressed{ext}"
        self.orig_size   = os.path.getsize(path)

        try:
            with pikepdf.open(path) as pdf:
                pages = len(pdf.pages)
        except Exception:
            pages = "?"

        name = os.path.basename(path)
        self.file_name_lbl.config(text=name if len(name) < 42 else name[:39]+"…")
        self.file_meta_lbl.config(text=f"{fmt_size(self.orig_size)}  ·  {pages} pages  ·  Click to change")
        self.file_icon.config(text="✓", fg=GREEN)

        self.col_orig.config(text=fmt_size(self.orig_size))
        self.col_pages.config(text=str(pages))
        self._refresh_estimate()

        self.out_lbl.config(text=os.path.basename(self.output_path))
        self.btn.config(state="normal")
        self.result_lbl.config(text="")

    def _pick_output(self):
        if not self.input_path: return
        path = filedialog.asksaveasfilename(
            title="Save compressed PDF as",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=os.path.basename(self.output_path),
            initialdir=os.path.dirname(self.input_path),
        )
        if path:
            self.output_path = path
            self.out_lbl.config(text=os.path.basename(path))

    def _on_quality(self, val):
        self.quality_pct = int(val)
        self.pct_lbl.config(text=str(self.quality_pct))
        self.quality_name_lbl.config(text=f"  {quality_label(self.quality_pct)}")
        self._refresh_estimate()

    def _refresh_estimate(self):
        if not self.orig_size: return
        est  = estimate_size(self.orig_size, self.quality_pct)
        save = self.orig_size - est
        pct  = save / self.orig_size * 100
        self.col_est.config(text=fmt_size(est))
        self.col_save.config(text=f"~{pct:.0f}%", fg=GREEN if pct > 10 else SUB2)

    def _run(self):
        if not self.input_path or not self.output_path: return
        self.btn.config(state="disabled", text="Compressing…")
        self.prog.pack(fill="x", pady=(8, 0))
        self.prog.start(8)
        self.result_lbl.config(text="", fg=SUB)
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        jpeg, dpi = quality_to_settings(self.quality_pct)
        orig = os.path.getsize(self.input_path)
        try:
            with pikepdf.open(self.input_path) as pdf:
                compress_images_in_pdf(pdf, jpeg, dpi)
                pdf.save(self.output_path,
                         compress_streams=True,
                         object_stream_mode=pikepdf.ObjectStreamMode.generate)
            new = os.path.getsize(self.output_path)
            if new >= orig:
                os.remove(self.output_path)
                self.after(0, self._done,
                           "File is already well optimised — no reduction possible.", AMBER)
            else:
                pct = (1 - new/orig) * 100
                msg = f"✓  {fmt_size(orig)}  →  {fmt_size(new)}  ({pct:.1f}% smaller)\nSaved to: {self.output_path}"
                self.after(0, self._done, msg, GREEN)
        except Exception as e:
            self.after(0, self._done, f"Error: {e}", RED)

    def _done(self, msg, color):
        self.prog.stop()
        self.prog.pack_forget()
        self.btn.config(state="normal", text="Compress PDF")
        self.result_lbl.config(text=msg, fg=color)


if __name__ == "__main__":
    App().mainloop()
