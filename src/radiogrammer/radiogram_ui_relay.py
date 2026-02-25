# scripts/radiogram_ui_relay.py
from __future__ import annotations

import json
import logging
import os
import re
import sys
import subprocess
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tkinter.font as tkfont
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Tuple

# --- Your existing project imports ---
from radiogrammer.kiss import KissTcpClient
from radiogrammer.storage import save_radiogram, list_files
from radiogrammer.models import Radiogram, Preamble, Address, RadiogramText, BookKeeping
from radiogrammer.outbox_tx_gui import relay_outbox_file


# =========================
# UI helpers
# =========================

def set_ui_font(root: tk.Tk, *, size: int = 11, scaling: float = 1.0):
    root.tk.call("tk", "scaling", scaling)
    for name in (
        "TkDefaultFont",
        "TkTextFont",
        "TkFixedFont",
        "TkMenuFont",
        "TkHeadingFont",
        "TkCaptionFont",
        "TkSmallCaptionFont",
        "TkIconFont",
        "TkTooltipFont",
    ):
        try:
            f = tkfont.nametofont(name)
            f.configure(size=size)
        except Exception:
            pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(".", font=tkfont.nametofont("TkDefaultFont"))
    style.configure("TLabelframe.Label", font=("TkDefaultFont", size, "bold"))
    style.configure("TButton", padding=(10, 6))
    style.configure("TEntry", padding=(6, 3))


def word_groups_count(lines: list[str]) -> int:
    joined = " ".join(" ".join(ln.strip().split()) for ln in lines if ln.strip())
    return len(joined.split()) if joined else 0


def open_file(path: Path) -> None:
    """Best-effort open a file with the OS default viewer."""
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
            return
        subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        # Fallback: do nothing; caller will show a message.
        pass


# =========================
# PDF rendering (template overlay + optional single-form crop)
# =========================
# This is a compact, embedded version of fill_radiogram_template_single.py so
# the UI can render PDFs directly.


def _val(x: Any) -> str:
    return "" if x is None else str(x)


def flatten_words(text_obj: Any) -> List[str]:
    lines: List[str] = []
    if isinstance(text_obj, dict):
        if text_obj.get("lines"):
            lines = [str(x) for x in text_obj.get("lines") if x is not None]
        elif text_obj.get("body"):
            lines = [str(text_obj["body"])]
    elif isinstance(text_obj, list):
        lines = [str(x) for x in text_obj if x is not None]
    elif text_obj is not None:
        lines = [str(text_obj)]

    joined = " ".join(lines).strip()
    joined = re.sub(r"\s+", " ", joined)
    return joined.split(" ") if joined else []


def compute_check(words: List[str]) -> str:
    n = len(words)
    if n == 0:
        return ""
    if words and words[0].upper() == "ARL":
        return f"ARL {n}"
    return str(n)


def parse_da(da: str) -> Tuple[str, float]:
    if not da:
        return ("Helvetica", 12.0)
    m = re.search(r"/([A-Za-z0-9\-_]+)\s+([0-9.]+)\s+Tf", da)
    if not m:
        return ("Helvetica", 12.0)
    font = m.group(1)
    size = float(m.group(2))
    font_map = {
        "Helvetica": "Helvetica",
        "Helvetica-Bold": "Helvetica-Bold",
        "Times-Roman": "Times-Roman",
        "Courier": "Courier",
    }
    return (font_map.get(font, "Helvetica"), size)


def _ensure_pdf_deps():
    # Silence noisy template warnings from pypdf/PyPDF2 (common with some fillable PDFs).
    for name in ("pypdf", "PyPDF2"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.ERROR)
        lg.propagate = False

    try:
        import pypdf  # noqa: F401
        from reportlab.pdfgen import canvas as _  # noqa: F401
        from reportlab.pdfbase import pdfmetrics as _  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "PDF rendering requires 'pypdf' and 'reportlab'. "
            "Install them in your environment (pip install pypdf reportlab)."
        ) from e


def build_field_texts(data: Dict[str, Any], which: str = "top") -> Dict[str, str]:
    which = which.strip().lower()
    if which not in {"top", "bottom"}:
        raise ValueError("which must be 'top' or 'bottom'")

    suffix = "Top" if which == "top" else "Bottom"
    pre = data.get("preamble", {}) or {}
    addr = data.get("address", {}) or {}
    words = flatten_words(data.get("text", {}))

    if len(words) > 25:
        raise ValueError(
            f"Message is {len(words)} words, but the template has 25 word boxes per form. "
            "Shorten the message or change the template/logic."
        )

    check = pre.get("check")
    if not check:
        check = compute_check(words)

    fm: Dict[str, str] = {
        f"Number - {suffix}": _val(pre.get("number")),
        f"Precedence - {suffix}": _val(pre.get("precedence")),
        f"HX - {suffix}": _val(pre.get("hx")),
        f"Station Of Origin - {suffix}": _val(pre.get("station_of_origin")),
        f"Check - {suffix}": _val(check),
        f"Place Of Origin - {suffix}": _val(pre.get("place_of_origin")),
        f"Time Filed - {suffix}": _val(pre.get("time_filed")),
        f"Date Filed - {suffix}": _val(pre.get("filed_date")),
        f"Recipient Name / Call - {suffix}": _val(addr.get("name")),
        f"Recipient Address - {suffix}": _val(addr.get("street")),
        f"Recipient City, State, & ZIP - {suffix}": _val(addr.get("city_state_zip")),
        f"Recipient Phone - {suffix}": _val(addr.get("phone")),
        f"Signature - {suffix}": _val(data.get("signature")),
        f"Received From - {suffix}": _val(data.get("bookkeeping", {}).get("received_from", "")),
        f"Sent To - {suffix}": _val(data.get("bookkeeping", {}).get("sent_to", "")),
    }

    for i in range(1, 26):
        fm[f"Word {i:02d} - {suffix}"] = words[i - 1] if (i - 1) < len(words) else ""

    return fm


def _collect_field_rects(template_pdf: Path) -> Tuple[Dict[str, List[float]], Dict[str, str], float, float]:
    from pypdf import PdfReader

    r = PdfReader(str(template_pdf), strict=False)
    page = r.pages[0]
    W = float(page.mediabox.width)
    H = float(page.mediabox.height)

    rects: Dict[str, List[float]] = {}
    das: Dict[str, str] = {}

    annots = page.get("/Annots")
    if not annots:
        return rects, das, W, H

    for a in annots.get_object():
        obj = a.get_object()
        name = obj.get("/T")
        if not name:
            continue
        rects[str(name)] = obj.get("/Rect")
        das[str(name)] = obj.get("/DA")

    return rects, das, W, H


def _fit_font_size(text: str, font_name: str, max_size: float, max_width: float, max_height: float, min_size: float = 6.0) -> float:
    from reportlab.pdfbase import pdfmetrics

    size = float(max_size)
    while size >= min_size:
        w = pdfmetrics.stringWidth(text, font_name, size)
        if w <= max_width and size <= max_height * 0.9:
            return size
        size -= 0.5
    return min_size


def _draw_text_in_rect(c, text: str, rect: List[float], font_name: str, font_size: float, align: str, valign: str, padding: float = 2.0) -> None:
    from reportlab.pdfbase import pdfmetrics

    if text is None:
        return
    text = str(text)

    x0, y0, x1, y1 = [float(v) for v in rect]
    w = x1 - x0
    h = y1 - y0

    fs = _fit_font_size(text, font_name, font_size, w - 2 * padding, h, min_size=6.0)
    c.setFont(font_name, fs)

    ascent = pdfmetrics.getAscent(font_name, fs) / 1000.0 * fs
    descent = abs(pdfmetrics.getDescent(font_name, fs) / 1000.0 * fs)

    if valign == "top":
        baseline = y1 - padding - descent
    elif valign == "middle":
        baseline = y0 + (h - (ascent + descent)) / 2 + descent
    else:
        baseline = y0 + padding + descent

    if align == "center":
        c.drawCentredString(x0 + w / 2.0, baseline, text)
    elif align == "right":
        c.drawRightString(x1 - padding, baseline, text)
    else:
        c.drawString(x0 + padding, baseline, text)


def _make_overlay_pdf(template_pdf: Path, field_texts: Dict[str, str]) -> BytesIO:
    from reportlab.pdfgen import canvas as rl_canvas
    from pypdf import PdfReader

    rects, das, W, H = _collect_field_rects(template_pdf)

    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(W, H))

    preamble_prefixes = [
        "Number -",
        "Precedence -",
        "HX -",
        "Station Of Origin -",
        "Check -",
        "Place Of Origin -",
        "Time Filed -",
        "Date Filed -",
    ]
    address_prefixes = [
        "Recipient Name / Call -",
        "Recipient Address -",
        "Recipient City, State, & ZIP -",
        "Recipient Phone -",
        "Signature -",
    ]
    book_keeping_prefixes = [
        "Received From -",
        "Sent To -",
    ]

    for fname, text in field_texts.items():
        if fname not in rects:
            continue
        rect = rects[fname]
        font, size = parse_da(das.get(fname, ""))

        if fname.startswith("Word"):
            align, valign = "center", "middle"
        elif any(fname.startswith(p) for p in preamble_prefixes):
            align, valign = "center", "bottom"
        elif any(fname.startswith(p) for p in address_prefixes):
            align, valign = "left", "bottom"
        else:
            align, valign = "left", "bottom"

        _draw_text_in_rect(c, text, rect, font_name=font, font_size=size, align=align, valign=valign, padding=2.0)

    c.showPage()
    c.save()
    buf.seek(0)

    # Touch reader here so errors surface early in this function.
    PdfReader(buf)
    buf.seek(0)
    return buf


def _compute_single_crop_bounds(template_pdf: Path, which: str) -> Tuple[float, float, float, float]:
    rects, _das, W, H = _collect_field_rects(template_pdf)
    top = [r for n, r in rects.items() if n.endswith("Top")]
    bottom = [r for n, r in rects.items() if n.endswith("Bottom")]

    which = which.strip().lower()
    if which not in {"top", "bottom"}:
        raise ValueError("which must be 'top' or 'bottom'")

    if not top or not bottom:
        if which == "top":
            return (0.0, H / 2.0, W, H)
        return (0.0, 0.0, W, H / 2.0)

    min_top_y0 = min(float(r[1]) for r in top)
    max_bottom_y1 = max(float(r[3]) for r in bottom)
    split = (min_top_y0 + max_bottom_y1) / 2.0

    if which == "top":
        return (0.0, split, W, H)
    else:
        return (0.0, 0.0, W, split)


def _apply_crop(page, bounds: Tuple[float, float, float, float]) -> None:
    x0, y0, x1, y1 = bounds
    page.mediabox.lower_left = (x0, y0)
    page.mediabox.upper_right = (x1, y1)
    page.cropbox.lower_left = (x0, y0)
    page.cropbox.upper_right = (x1, y1)


def render_radiogram_pdf(*, template_pdf: Path, json_path: Path, out_pdf: Path, which: str = "top", layout: str = "single") -> None:
    """Render one JSON file into the template and write out_pdf."""
    _ensure_pdf_deps()

    from pypdf import PdfReader, PdfWriter

    data = json.loads(json_path.read_text(encoding="utf-8"))
    field_texts = build_field_texts(data, which=which)

    overlay_buf = _make_overlay_pdf(template_pdf, field_texts)
    overlay_reader = PdfReader(overlay_buf)

    base_reader = PdfReader(str(template_pdf), strict=False)
    base_page = base_reader.pages[0]
    base_page.merge_page(overlay_reader.pages[0])

    if layout == "single":
        bounds = _compute_single_crop_bounds(template_pdf, which=which)
        _apply_crop(base_page, bounds)

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_page(base_page)
    with out_pdf.open("wb") as f:
        writer.write(f)


# =========================
# Main UI
# =========================


class RadiogramUI(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master)
        self.master = master
        self.pack(fill="both", expand=True)

        self.outbox_paths: list[Path] = []
        self.archive_paths: list[Path] = []
        self.sent_paths: list[Path] = []

        self.log_q: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None

        # ACK decision coordination
        self.decision_event: threading.Event = threading.Event()
        self.decision_result: dict = {"action": None}
        self.decision_info: dict = {}

        # PDF template
        self.template_var = tk.StringVar(value=str(self._default_template_path()))

        # Draft autosave location (relaxed JSON; can be incomplete)
        self.draft_path: Path = self._default_draft_path()

        self._build()

        # Keybindings
        try:
            self.master.bind_all("<Control-s>", lambda _e: self.save_draft())
            self.master.bind_all("<Control-l>", lambda _e: self.clear_form())
        except Exception:
            pass

        # Optionally restore last draft
        self.load_draft(silent=True)

        self._refresh_lists()
        try:
            self.c_precedence.set("R")
        except Exception:
            pass
        self._update_check_label()
        self._drain_log_queue()

    @staticmethod
    def _default_template_path() -> Path:
        """Pick a sensible default template path (prefers nearby files)."""
        here = Path(__file__).resolve().parent
        candidates = [
            here / "RadiogramTemplate.pdf",
            here / "Fillable Radiogram Form.pdf",
            here.parent / "Fillable Radiogram Form.pdf",
            Path.cwd() / "Fillable Radiogram Form.pdf",
        ]
        for c in candidates:
            if c.exists():
                return c
        # Fall back to CWD-resolved path (even if missing) so the UI shows intent.
        return (Path.cwd() / "RadiogramTemplate.pdf").resolve()


    @staticmethod
    def _default_draft_path() -> Path:
        """Default location for the compose-form draft JSON.

        Stored in the user home directory so it persists across runs.
        """
        base = Path.home() / ".radiogrammer"
        return base / "draft.json"

    # ---------- layout ----------
    def _build(self):
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(0, weight=1)

        # ---- Left: Compose form ----
        left = ttk.Frame(self, padding=10)
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        form = ttk.Frame(left)
        form.grid(row=0, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)

        # Preamble
        pre = ttk.Labelframe(form, text="Preamble", padding=(10, 8))
        pre.grid(row=0, column=0, columnspan=2, sticky="ew")
        pre.columnconfigure(1, weight=1)

        r = 0
        self.e_number = self._row(pre, r, "NR (number)", ""); r += 1
        self.c_precedence = self._row_combo(pre, r, "Precedence", ["R","P","W","EMERGENCY","TEST R","TEST P","TEST W","TEST EMERGENCY"], "R"); r += 1
        self.e_hx = self._row(pre, r, "HX (optional)", ""); r += 1
        self.e_station = self._row(pre, r, "STN ORIG", ""); r += 1
        self.e_check = self._row(pre, r, "CHECK", ""); r += 1
        self.e_place = self._row(pre, r, "PLACE OF ORIG", ""); r += 1
        self.e_time = self._row(pre, r, "TIME FILED (optional)", ""); r += 1
        self.e_date = self._row(pre, r, "FILED DATE (e.g., DEC 29)", ""); r += 1

        self.check_var = tk.StringVar(value="CHECK: 0")
        ttk.Label(pre, textvariable=self.check_var).grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # Address
        addr = ttk.Labelframe(form, text="Address", padding=(10, 8))
        addr.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        addr.columnconfigure(1, weight=1)

        r = 0
        self.e_name = self._row(addr, r, "Name", ""); r += 1
        self.e_street = self._row(addr, r, "Street", ""); r += 1
        self.e_citystzip = self._row(addr, r, "City State ZIP", ""); r += 1
        self.e_phone = self._row(addr, r, "Phone", ""); r += 1
        self.e_mail = self._row(addr, r, "Email (optional)", ""); r += 1

        # Text
        txt = ttk.Labelframe(form, text="Text (max 5 lines; 5 words per line)", padding=(10, 8))
        txt.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        txt.columnconfigure(1, weight=1)

        self.text_entries: list[ttk.Entry] = []
        for i in range(5):
            ttk.Label(txt, text=f"Line {i+1}").grid(row=i, column=0, sticky="w")
            e = ttk.Entry(txt)
            e.grid(row=i, column=1, sticky="ew", pady=2)
            e.bind("<KeyRelease>", lambda _evt: self._update_check_label())
            self.text_entries.append(e)

        # Signature
        sig = ttk.Labelframe(form, text="Signature", padding=(10, 8))
        sig.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        sig.columnconfigure(1, weight=1)
        self.e_sig = self._row(sig, 0, "Signature", "")

        # Bookkeeping
        bk = ttk.Labelframe(form, text="Book keeping (optional)", padding=(10, 8))
        bk.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        bk.columnconfigure(1, weight=1)

        r = 0
        self.e_received_from = self._row(bk, r, "Received From (optional)", ""); r += 1
        self.e_sent_to = self._row(bk, r, "Sent To (optional)", ""); r += 1

        # Actions
        btns = ttk.Frame(form)
        btns.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        for i in range(3):
            btns.columnconfigure(i, weight=1)

        ttk.Button(btns, text="Save Draft", command=self.save_draft).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(btns, text="Load Draft", command=self.load_draft).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(btns, text="Clear", command=self.clear_form).grid(row=0, column=2, sticky="ew", padx=(6, 0))

        ttk.Button(btns, text="Save to Outbox", command=self.save_outbox).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(8, 0))
        ttk.Button(btns, text="Archive", command=self.save_archive).grid(row=1, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Button(btns, text="Refresh lists", command=self._refresh_lists).grid(row=1, column=2, sticky="ew", padx=(6, 0), pady=(8, 0))

        # ---- Right: Settings + Tabs + Log ----
        right = ttk.Frame(self, padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        right.rowconfigure(3, weight=1)

        # KISS settings
        kiss = ttk.Labelframe(right, text="KISS Settings", padding=(10, 8))
        kiss.grid(row=0, column=0, sticky="ew")
        kiss.columnconfigure(1, weight=1)

        ttk.Label(kiss, text="Host").grid(row=0, column=0, sticky="w")
        self.e_host = ttk.Entry(kiss)
        self.e_host.insert(0, "127.0.0.1")
        self.e_host.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        ttk.Label(kiss, text="Port").grid(row=1, column=0, sticky="w")
        self.e_port = ttk.Entry(kiss)
        self.e_port.insert(0, "8001")
        self.e_port.grid(row=1, column=1, sticky="ew", padx=(6, 0))

        ttk.Label(kiss, text='My Call (messages sent from here)').grid(row=2, column=0, sticky="w")
        self.e_mycall = ttk.Entry(kiss)
        self.e_mycall.insert(0, "KC1YMT")
        self.e_mycall.grid(row=2, column=1, sticky="ew", padx=(6, 0))

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(kiss, textvariable=self.status_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # ACK Decision Panel (initially hidden)
        self.decision_frame = ttk.Labelframe(right, text="⚠ ACK Not Received", padding=(10, 8))
        self.decision_frame.columnconfigure(0, weight=1)

        self.decision_line_var = tk.StringVar(value="")
        self.decision_msgid_var = tk.StringVar(value="")
        self.decision_attempts_var = tk.StringVar(value="")

        ttk.Label(self.decision_frame, text="Line:", font=("TkDefaultFont", 9, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(self.decision_frame, textvariable=self.decision_line_var, wraplength=300).grid(row=1, column=0, sticky="w", pady=(0, 4))

        ttk.Label(self.decision_frame, textvariable=self.decision_msgid_var).grid(row=2, column=0, sticky="w", pady=(0, 4))
        ttk.Label(self.decision_frame, textvariable=self.decision_attempts_var).grid(row=3, column=0, sticky="w", pady=(0, 8))

        ttk.Label(self.decision_frame, text="Check aprs.fi or other means, then:", font=("TkDefaultFont", 9)).grid(row=4, column=0, sticky="w", pady=(0, 6))

        btn_frame = ttk.Frame(self.decision_frame)
        btn_frame.grid(row=5, column=0, sticky="ew")
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        ttk.Button(btn_frame, text="Retry Again", command=self._decision_retry).grid(row=0, column=0, sticky="ew", pady=2)
        ttk.Button(btn_frame, text="Send Next Anyway", command=self._decision_continue).grid(row=1, column=0, sticky="ew", pady=2)
        ttk.Button(btn_frame, text="Abort Relay", command=self._decision_abort).grid(row=2, column=0, sticky="ew", pady=2)

        # Decision panel is initially hidden (will be shown when needed)
        # self.decision_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))

        # PDF template
        pdf = ttk.Labelframe(right, text="PDF Template", padding=(10, 8))
        pdf.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        pdf.columnconfigure(0, weight=1)
        pdf.columnconfigure(1, weight=0)

        self.e_template = ttk.Entry(pdf, textvariable=self.template_var)
        self.e_template.grid(row=0, column=0, sticky="ew")
        ttk.Button(pdf, text="Browse…", command=self.browse_template).grid(row=0, column=1, padx=(8, 0))

        # Tabs
        self.nb = ttk.Notebook(right)
        self.nb.grid(row=3, column=0, sticky="nsew", pady=(10, 0))

        self._tab_outbox = self._make_list_tab(self.nb, title="Outbox")
        self.lb_outbox, self._outbox_scroll = self._tab_outbox["listbox"], self._tab_outbox["scroll"]
        self._make_outbox_buttons(self._tab_outbox["button_row"])

        self._tab_sent = self._make_list_tab(self.nb, title="Sent")
        self.lb_sent, self._sent_scroll = self._tab_sent["listbox"], self._tab_sent["scroll"]
        self._make_pdf_buttons(self._tab_sent["button_row"], box="sent")

        self._tab_archive = self._make_list_tab(self.nb, title="Archive")
        self.lb_archive, self._arch_scroll = self._tab_archive["listbox"], self._tab_archive["scroll"]
        self._make_pdf_buttons(self._tab_archive["button_row"], box="archive")

        # Log
        logf = ttk.Labelframe(right, text="Log", padding=(10, 8))
        logf.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)

        self.txt_log = tk.Text(logf, height=10, wrap="word")
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(logf, orient="vertical", command=self.txt_log.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=log_scroll.set)

    def _make_list_tab(self, nb: ttk.Notebook, *, title: str) -> Dict[str, Any]:
        frame = ttk.Frame(nb, padding=(10, 10))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        nb.add(frame, text=title)

        lb = tk.Listbox(frame, height=12)
        lb.grid(row=0, column=0, sticky="nsew")

        sb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
        sb.grid(row=0, column=1, sticky="ns")
        lb.configure(yscrollcommand=sb.set)

        btnrow = ttk.Frame(frame)
        btnrow.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        btnrow.columnconfigure(0, weight=1)
        btnrow.columnconfigure(1, weight=1)
        btnrow.columnconfigure(2, weight=1)

        return {"frame": frame, "listbox": lb, "scroll": sb, "button_row": btnrow}

    def _make_outbox_buttons(self, btnrow: ttk.Frame) -> None:
        # Row 0: Main operations
        self.btn_relay_sel = ttk.Button(btnrow, text="Relay Selected", command=self.relay_selected)
        self.btn_relay_sel.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.btn_relay_next = ttk.Button(btnrow, text="Relay Next", command=self.relay_next)
        self.btn_relay_next.grid(row=0, column=1, sticky="ew", padx=6)

        ttk.Button(btnrow, text="Load to Form", command=self.load_selected_outbox).grid(row=0, column=2, sticky="ew", padx=(6, 0))

        # Row 1: Management operations
        ttk.Button(btnrow, text="Delete", command=lambda: self.delete_selected("outbox")).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(6, 0))
        ttk.Button(btnrow, text="Archive", command=lambda: self.move_selected("outbox", "archive")).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))

    def _make_pdf_buttons(self, btnrow: ttk.Frame, *, box: str) -> None:
        # Row 0: PDF operations
        ttk.Button(btnrow, text="Render PDF", command=lambda b=box: self.render_selected_pdf(b)).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(btnrow, text="Render All", command=lambda b=box: self.render_all_pdfs(b)).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(btnrow, text="Open PDF", command=lambda b=box: self.open_selected_pdf(b)).grid(row=0, column=2, sticky="ew", padx=(6, 0))

        # Row 1: Management operations
        ttk.Button(btnrow, text="Delete", command=lambda b=box: self.delete_selected(b)).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(6, 0))

        if box == "sent":
            ttk.Button(btnrow, text="Archive", command=lambda: self.move_selected("sent", "archive")).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
            ttk.Button(btnrow, text="To Outbox", command=lambda: self.move_selected("sent", "outbox")).grid(row=1, column=2, sticky="ew", padx=(6, 0), pady=(6, 0))
        elif box == "archive":
            ttk.Button(btnrow, text="To Sent", command=lambda: self.move_selected("archive", "sent")).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
            ttk.Button(btnrow, text="To Outbox", command=lambda: self.move_selected("archive", "outbox")).grid(row=1, column=2, sticky="ew", padx=(6, 0), pady=(6, 0))

    # ---------- form rows ----------
    def _row(self, parent, row: int, label: str, default: str) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        e = ttk.Entry(parent)
        e.insert(0, default)
        e.grid(row=row, column=1, sticky="ew", pady=2)
        return e

    def _row_combo(self, parent, row: int, label: str, values: list[str], default: str) -> ttk.Combobox:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
        c = ttk.Combobox(parent, values=values, state="readonly")
        c.set(default)
        c.grid(row=row, column=1, sticky="ew", pady=2)
        return c

    # ---------- compose helpers ----------
    def _gather_text_lines(self) -> list[str]:
        return [e.get() for e in self.text_entries]

    def _update_check_label(self):
        ck = word_groups_count(self._gather_text_lines())
        self.check_var.set(f"CHECK: {ck}")

    def _build_radiogram(self) -> Radiogram:
        lines = self._gather_text_lines()
        ck = self.e_check.get().strip()

        preamble = Preamble(
            number=self.e_number.get().strip(),
            precedence=self.c_precedence.get().strip(),
            hx=self.e_hx.get().strip() or None,
            check=ck,
            station_of_origin=self.e_station.get().strip().upper(),
            place_of_origin=self.e_place.get().strip().upper(),
            time_filed=self.e_time.get().strip().upper() or None,
            filed_date=self.e_date.get().strip().upper(),
        )

        address = Address(
            name=self.e_name.get().strip().upper() or None,
            street=self.e_street.get().strip().upper() or None,
            city_state_zip=self.e_citystzip.get().strip().upper() or None,
            phone=self.e_phone.get().strip() or None,
            email=self.e_mail.get().strip() or None,
        )

        text = RadiogramText(lines=lines)
        signature = self.e_sig.get().strip().upper()
        bk = BookKeeping(
            received_from = self.e_received_from.get().strip() or None,
            sent_to = self.e_sent_to.get().strip() or None,
        )
        return Radiogram(preamble=preamble, address=address, text=text, signature=signature, bookkeeping=bk)

    # ---------- storage ----------

    def _gather_json_relaxed(self) -> Dict[str, Any]:
        """Gather form fields without strict validation (suitable for drafts)."""
        def norm(v: str) -> Any:
            v = (v or '').strip()
            return v.upper() if v else None

        # number: best-effort int
        num_raw = (self.e_number.get() or '').strip()
        try:
            number = num_raw if num_raw else None
        except Exception:
            number = None

        lines = [e.get() for e in self.text_entries]
        lines = [re.sub(r'\s+', ' ', (ln or '').strip()).upper() for ln in lines]

        data: Dict[str, Any] = {
            'preamble': {
                'number': number,
                'precedence': (self.c_precedence.get() or 'R').strip().upper() or None,
                'hx': norm(self.e_hx.get()),
                'check': norm(self.e_check.get()),
                'station_of_origin': norm(self.e_station.get()),
                'place_of_origin': norm(self.e_place.get()),
                'time_filed': norm(self.e_time.get()),
                'filed_date': norm(self.e_date.get()),
            },
            'address': {
                'name': norm(self.e_name.get()),
                'street': norm(self.e_street.get()),
                'city_state_zip': norm(self.e_citystzip.get()),
                'phone': (self.e_phone.get() or '').strip() or None,
                'email': (self.e_mail.get() or '').strip() or None,
            },
            'text': {
                'lines': [ln for ln in lines if ln],
            },
            'signature': norm(self.e_sig.get()),
            'bookkeeping': {
                'received_from': (self.e_received_from.get() or '').strip() or None,
                'sent_to': (self.e_sent_to.get() or '').strip() or None,
            },
        }
        return data

    def save_draft(self) -> None:
        """Save the current form as a draft (does not require strict validation)."""
        try:
            data = self._gather_json_relaxed()
            self.draft_path.parent.mkdir(parents=True, exist_ok=True)
            self.draft_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
            self._log(f'Draft saved: {self.draft_path}')
            messagebox.showinfo('Draft saved', f'Saved draft to\n{self.draft_path}')
        except Exception as e:
            messagebox.showerror('Draft error', str(e))

    def load_draft(self, silent: bool = False) -> None:
        """Load the saved draft into the form."""
        try:
            if not self.draft_path.exists():
                if not silent:
                    messagebox.showinfo('No draft', f'No draft found at\n{self.draft_path}')
                return
            data = json.loads(self.draft_path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Draft file is not a JSON object')
            self._load_json_to_form(data)
            self._log(f'Draft loaded: {self.draft_path}')
            if not silent:
                messagebox.showinfo('Draft loaded', 'Draft loaded into the form.')
        except Exception as e:
            if silent:
                self._log(f'Draft load error: {e}')
            else:
                messagebox.showerror('Draft error', str(e))

    def save_outbox(self):
        self._save("outbox")

    def save_archive(self):
        self._save("archive")

    def _save(self, box: str):
        try:
            rg = self._build_radiogram()
        except Exception as e:
            messagebox.showerror("Validation error", str(e))
            return
        path = save_radiogram(rg, box=box)  # type: ignore[arg-type]
        messagebox.showinfo("Saved", f"Saved to {box}:\n{path}")
        self._refresh_lists()

    def clear_form(self):
        for e in [self.e_number, self.e_hx, self.e_station, self.e_check, self.e_place, self.e_time, self.e_date]:
            e.delete(0, tk.END)
        for e in [self.e_name, self.e_street, self.e_citystzip, self.e_phone, self.e_mail, self.e_sig]:
            e.delete(0, tk.END)
        for e in self.text_entries:
            e.delete(0, tk.END)
        for e in [self.e_received_from, self.e_sent_to]:
            e.delete(0, tk.END)

        try:
            self.c_precedence.set("R")
        except Exception:
            pass
        self._update_check_label()

    def _refresh_lists(self):
        self.outbox_paths = list_files("outbox")
        self.lb_outbox.delete(0, tk.END)
        for p in self.outbox_paths:
            self.lb_outbox.insert(tk.END, p.name)

        self.sent_paths = list_files("sent")
        self.lb_sent.delete(0, tk.END)
        for p in self.sent_paths:
            self.lb_sent.insert(tk.END, p.name)

        self.archive_paths = list_files("archive")
        self.lb_archive.delete(0, tk.END)
        for p in self.archive_paths:
            self.lb_archive.insert(tk.END, p.name)

    # ---------- logging ----------
    def _log(self, msg: str):
        self.log_q.put(msg)

    def _drain_log_queue(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.txt_log.insert(tk.END, msg + "\n")
                self.txt_log.see(tk.END)
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    # ---------- template ----------
    def browse_template(self):
        p = filedialog.askopenfilename(
            title="Select template PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*")],
        )
        if p:
            self.template_var.set(str(Path(p).resolve()))

    def _template_path(self) -> Path:
        return Path(self.template_var.get()).expanduser().resolve()

    # ---------- outbox: relay ----------
    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        self.btn_relay_sel.configure(state=state)
        self.btn_relay_next.configure(state=state)

    def relay_selected(self):
        sel = self.lb_outbox.curselection()
        if not sel:
            messagebox.showinfo("Outbox", "Select an outbox item first.")
            return
        idx = int(sel[0])
        self._start_relay(self.outbox_paths[idx])

    def relay_next(self):
        if not self.outbox_paths:
            messagebox.showinfo("Outbox", "Outbox is empty.")
            return
        self._start_relay(self.outbox_paths[-1])  # newest-first -> oldest last

    def _start_relay(self, path: Path):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Relay", "Already relaying. Please wait.")
            return

        host = self.e_host.get().strip()
        port = int(self.e_port.get().strip())
        my_call = self.e_mycall.get().strip().upper()

        self.status_var.set(f"Relaying {path.name} …")
        self._set_busy(True)

        def run():
            try:
                kiss = KissTcpClient(host, port)
                kiss.connect()
                try:
                    sent_path = relay_outbox_file(
                        kiss,
                        path=path,
                        my_call=my_call,
                        ax25_dest="APK005",
                        digis=["WIDE1-1", "WIDE2-1"],
                        log=self._log,
                        ask_user_decision=self._ask_user_decision,
                    )
                    if sent_path:
                        self._log("DONE: moved to sent.")
                    else:
                        self._log("DONE: failed (left in outbox).")
                finally:
                    kiss.close()
            except Exception as e:
                self._log(f"ERROR: {e}")
            finally:
                self.master.after(0, self._after_relay_done)

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _after_relay_done(self):
        self.status_var.set("Idle")
        self._set_busy(False)
        self._refresh_lists()
        self._hide_decision_panel()

    # ---------- ACK decision panel ----------
    def _show_decision_panel(self, info: dict):
        """Show the decision panel with info about the failed ACK."""
        self.decision_info = info

        # Update the frame title to reflect which phase failed so the operator
        # knows whether the QTC itself was missed or just the "Ready to copy".
        phase = info.get("phase", "")
        if phase == "ready_to_copy":
            self.decision_frame.configure(text="⚠ 'Ready to copy' not received")
        else:
            self.decision_frame.configure(text="⚠ ACK Not Received")

        self.decision_line_var.set(info.get("line", ""))
        self.decision_msgid_var.set(f"Message ID: {info.get('msgid', '')} (with braces)")
        self.decision_attempts_var.set(
            f"Tried {info.get('attempts', 0)} times with {info.get('timeout', 0)}s timeout"
        )
        self.decision_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.status_var.set("⚠ Waiting for user decision")

    def _hide_decision_panel(self):
        """Hide the decision panel."""
        self.decision_frame.grid_forget()

    def _decision_retry(self):
        """User chose to retry sending the line."""
        self._log("USER: Retry sending line")
        self.decision_result["action"] = "retry"
        self.decision_event.set()
        self._hide_decision_panel()
        self.status_var.set("Retrying...")

    def _decision_continue(self):
        """User chose to continue without ACK."""
        self._log("USER: Send next line anyway (no ACK)")
        self.decision_result["action"] = "continue"
        self.decision_event.set()
        self._hide_decision_panel()
        self.status_var.set("Continuing relay...")

    def _decision_abort(self):
        """User chose to abort the relay."""
        self._log("USER: Abort relay")
        self.decision_result["action"] = "abort"
        self.decision_event.set()
        self._hide_decision_panel()
        self.status_var.set("Aborting...")

    def _ask_user_decision(self, info: dict) -> str:
        """
        Called from relay thread when ACK fails.
        Shows decision panel and blocks until user clicks a button.
        Returns: "continue", "retry", or "abort"
        """
        # Reset event and result
        self.decision_event.clear()
        self.decision_result["action"] = None

        # Show panel on UI thread
        self.master.after(0, lambda: self._show_decision_panel(info))

        # Block until user clicks a button
        self.decision_event.wait()

        # Return user's choice
        return self.decision_result.get("action", "abort")

    # ---------- outbox: load ----------
    def load_selected_outbox(self):
        sel = self.lb_outbox.curselection()
        if not sel:
            messagebox.showinfo("Outbox", "Select an outbox item first.")
            return
        idx = int(sel[0])
        p = self.outbox_paths[idx]
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror("Load error", f"Failed to read {p.name}:\n{e}")
            return
        self._load_json_to_form(data)
        self._log(f"Loaded {p.name} into form")

    def _load_json_to_form(self, data: Dict[str, Any]) -> None:
        pre = data.get("preamble", {}) or {}
        addr = data.get("address", {}) or {}
        text = data.get("text", {}) or {}
    

        def set_entry(e: ttk.Entry, v: Any):
            e.delete(0, tk.END)
            if v is not None:
                e.insert(0, str(v))

        set_entry(self.e_number, pre.get("number", ""))
        try:
            self.c_precedence.set(str(pre.get("precedence", "R")))
        except Exception:
            pass
        set_entry(self.e_hx, pre.get("hx", ""))
        set_entry(self.e_station, pre.get("station_of_origin", ""))
        set_entry(self.e_check, pre.get("check", ""))
        set_entry(self.e_place, pre.get("place_of_origin", ""))
        set_entry(self.e_time, pre.get("time_filed", ""))
        set_entry(self.e_date, pre.get("filed_date", ""))

        set_entry(self.e_name, addr.get("name", ""))
        set_entry(self.e_street, addr.get("street", ""))
        set_entry(self.e_citystzip, addr.get("city_state_zip", ""))
        set_entry(self.e_phone, addr.get("phone", ""))
        set_entry(self.e_mail, addr.get("email", ""))

        lines = text.get("lines") or []
        for i, e in enumerate(self.text_entries):
            e.delete(0, tk.END)
            if i < len(lines) and lines[i] is not None:
                e.insert(0, str(lines[i]))

        set_entry(self.e_sig, data.get("signature", ""))
        set_entry(self.e_received_from, data.get("bookkeeping", {}).get("received_from", ""))
        set_entry(self.e_sent_to, data.get("bookkeeping", {}).get("sent_to", ""))
        self._update_check_label()

    # ---------- Delete and Move operations ----------
    def delete_selected(self, box: str):
        """Delete selected message from the specified box."""
        box = box.lower()
        if box == "outbox":
            lb, paths = self.lb_outbox, self.outbox_paths
        elif box == "sent":
            lb, paths = self.lb_sent, self.sent_paths
        elif box == "archive":
            lb, paths = self.lb_archive, self.archive_paths
        else:
            return

        sel = lb.curselection()
        if not sel:
            messagebox.showinfo("Delete", f"Select a message from {box} first.")
            return

        idx = int(sel[0])
        if idx < 0 or idx >= len(paths):
            return

        path = paths[idx]

        # Confirm deletion
        result = messagebox.askyesno(
            "Confirm Delete",
            f"Delete {path.name}?\n\nThis cannot be undone.",
            icon="warning"
        )

        if not result:
            return

        try:
            path.unlink()  # Delete the file
            self._log(f"Deleted: {path.name}")
            self._refresh_lists()
        except Exception as e:
            messagebox.showerror("Delete Error", f"Failed to delete {path.name}:\n{e}")

    def move_selected(self, from_box: str, to_box: str):
        """Move selected message from one box to another."""
        from_box = from_box.lower()
        to_box = to_box.lower()

        if from_box == "outbox":
            lb, paths = self.lb_outbox, self.outbox_paths
        elif from_box == "sent":
            lb, paths = self.lb_sent, self.sent_paths
        elif from_box == "archive":
            lb, paths = self.lb_archive, self.archive_paths
        else:
            return

        sel = lb.curselection()
        if not sel:
            messagebox.showinfo("Move", f"Select a message from {from_box} first.")
            return

        idx = int(sel[0])
        if idx < 0 or idx >= len(paths):
            return

        path = paths[idx]

        try:
            from radiogrammer.storage import move_file
            new_path = move_file(path, to_box)
            self._log(f"Moved {path.name} from {from_box} to {to_box}")
            self._refresh_lists()
        except Exception as e:
            messagebox.showerror("Move Error", f"Failed to move {path.name}:\n{e}")

    # ---------- PDF rendering buttons ----------
    def _selected_path(self, box: str) -> Path | None:
        box = box.lower()
        if box == "sent":
            lb, paths = self.lb_sent, self.sent_paths
        elif box == "archive":
            lb, paths = self.lb_archive, self.archive_paths
        else:
            return None

        sel = lb.curselection()
        if not sel:
            return None
        idx = int(sel[0])
        if idx < 0 or idx >= len(paths):
            return None
        return paths[idx]

    def _pdf_out_path(self, json_path: Path) -> Path:
        # Put PDFs in a sibling "pdf" folder inside the box folder.
        out_dir = json_path.parent / "pdf"
        return out_dir / (json_path.stem + ".pdf")

    def render_selected_pdf(self, box: str) -> None:
        p = self._selected_path(box)
        if not p:
            messagebox.showinfo("Select message", f"Select a {box} message first.")
            return
        self._render_pdf_threaded([p])

    def render_all_pdfs(self, box: str) -> None:
        paths = self.sent_paths if box.lower() == "sent" else self.archive_paths
        if not paths:
            messagebox.showinfo(box.title(), f"No {box} messages found.")
            return
        self._render_pdf_threaded(paths)

    def open_selected_pdf(self, box: str) -> None:
        p = self._selected_path(box)
        if not p:
            messagebox.showinfo("Select message", f"Select a {box} message first.")
            return
        pdf_path = self._pdf_out_path(p)
        if not pdf_path.exists():
            messagebox.showinfo("PDF not found", "No rendered PDF found yet. Click Render PDF first.")
            return
        try:
            open_file(pdf_path)
        except Exception:
            messagebox.showinfo("Open PDF", f"PDF saved at:\n{pdf_path}")

    def _render_pdf_threaded(self, json_paths: List[Path]) -> None:
        template = self._template_path()
        if not template.exists():
            messagebox.showerror("Template missing", f"Template PDF not found:\n{template}")
            return

        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Already working. Please wait.")
            return

        self.status_var.set("Rendering PDF(s) …")

        def run():
            ok = 0
            fail = 0
            try:
                for jp in json_paths:
                    outp = self._pdf_out_path(jp)
                    try:
                        render_radiogram_pdf(template_pdf=template, json_path=jp, out_pdf=outp, which="top", layout="single")
                        ok += 1
                        self._log(f"PDF: {jp.name} -> {outp}")
                    except Exception as e:
                        fail += 1
                        self._log(f"PDF ERROR ({jp.name}): {e}")
            finally:
                self.master.after(0, lambda: self._after_pdf_done(ok, fail))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _after_pdf_done(self, ok: int, fail: int) -> None:
        self.status_var.set("Idle")
        if fail == 0:
            messagebox.showinfo("PDF Render", f"Rendered {ok} PDF(s).")
        else:
            messagebox.showwarning("PDF Render", f"Rendered {ok} PDF(s); {fail} failed. See log.")


def main():
    root = tk.Tk()
    set_ui_font(root, size=11, scaling=1.0)
    root.title("Radiogrammer — Compose / Outbox / Sent / Archive")
    root.geometry("1200x960")
    RadiogramUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
