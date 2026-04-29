"""Momir Vig Machine — main application window."""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageTk

from detect_printer_port import find_target_port
from lookup_creature import random_creature
from printer import Printer


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BANNER_IMAGE = Path("image.png")
BANNER_FRAC  = 0.25      # banner occupies 25% of window height

GRID_COLS = 5
GRID_ROWS = 4
GRID_PAD  = 10           # px padding around/between buttons

BTN_RADIUS = 16
BORDER_PX  = 2

COLOR_TEXT    = "#FFFFFF"

# Cross-platform font: Segoe UI on Windows, DejaVu Sans on Linux, Helvetica Neue on macOS.
if sys.platform == "win32":
    FONT_FAMILY = "Segoe UI"
elif sys.platform == "darwin":
    FONT_FAMILY = "Helvetica Neue"
else:
    FONT_FAMILY = "DejaVu Sans"

# RGBA fills — buttons are white at 10% opacity, exit red at 20%
BTN_NORMAL_RGBA  = (255, 255, 255,  26)   # white 10%
BTN_HOVER_RGBA   = (255, 255, 255,  70)   # white 27%
EXIT_NORMAL_RGBA = (210,  50,  50,  70)   # red   27%
EXIT_HOVER_RGBA  = (210,  50,  50, 140)   # red   55%
BORDER_RGBA      = (  0,   0,   0, 190)

LOAD_DURATION_MS      = 1200   # ms to animate the loading bar
RESULT_DURATION_MS    = 3000   # ms to display the creature name
PRINTER_CHECK_MIN_MS  = 2000   # minimum ms to show the printer detection splash

# Braille spinner frames shown next to the printer emoji during detection
_PRINTER_SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


# ---------------------------------------------------------------------------
# PIL helper: build a single button RGBA image composited over its bg slice
# ---------------------------------------------------------------------------

def make_button_image(
    bg_slice: Image.Image,
    fill_rgba: tuple[int, int, int, int],
    radius: int = BTN_RADIUS,
    border: int = BORDER_PX,
) -> ImageTk.PhotoImage:
    """Return a PhotoImage of a rounded button composited over bg_slice."""
    w, h = bg_slice.size
    base = bg_slice.convert("RGBA")

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # Border ring
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=BORDER_RGBA)
    # Button fill
    draw.rounded_rectangle(
        [border, border, w - 1 - border, h - 1 - border],
        radius=max(1, radius - border),
        fill=fill_rgba,
    )
    composite = Image.alpha_composite(base, overlay).convert("RGB")
    return ImageTk.PhotoImage(composite)


# ---------------------------------------------------------------------------
# Button descriptor
# ---------------------------------------------------------------------------

class ButtonSlot(NamedTuple):
    label:   str
    cmc:     int | None   # None = exit or spacer
    is_exit: bool
    is_spacer: bool


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class MomirVigApp(tk.Tk):

    def __init__(self) -> None:
        super().__init__()

        self.title("Momir Vig Machine")
        if self.tk.call("tk", "windowingsystem") == "win32":
            self.state("zoomed")
        else:
            self.attributes("-zoomed", True)
        self.resizable(True, True)

        src = Image.open(BANNER_IMAGE)
        src_w, src_h = src.size
        self._banner_src = src.crop((0, 0, src_w, src_h // 2))

        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)

        # Kept to prevent GC
        self._bg_photo:   ImageTk.PhotoImage | None = None
        self._btn_photos: dict[str, ImageTk.PhotoImage] = {}

        self._button_items: list[dict] = []
        self._slots = self._build_slots()
        self._last_size = (0, 0)

        # State machine: "printer_check" | "buttons" | "loading" | "result"
        self._state: str = "printer_check"
        self._pending_creature: dict | None = None
        self._waiting_for_result: bool = False
        self._load_start_time: float = 0.0
        self._load_anim_id: str | None = None
        self._load_bar_id: int | None = None
        self._load_bar_bounds: tuple | None = None
        self._overlay_photo: ImageTk.PhotoImage | None = None

        # Printer
        self._printer: Printer | None = None

        # Printer detection state
        self._printer_port: str | None = None
        self._printer_check_start: float = time.monotonic()
        self._printer_anim_id: str | None = None
        self._printer_spin_idx: int = 0
        self._printer_spin_item: int | None = None

        self._canvas.bind("<Configure>", self._on_resize)

        # Detect the printer port in a background thread so the UI stays responsive.
        threading.Thread(target=self._run_printer_detect, daemon=True).start()

    # ------------------------------------------------------------------
    # Slot layout
    # ------------------------------------------------------------------

    def _build_slots(self) -> list[ButtonSlot]:
        last = GRID_COLS * GRID_ROWS - 1
        slots = []
        for i in range(GRID_COLS * GRID_ROWS):
            if i <= 16:
                slots.append(ButtonSlot(str(i),  cmc=i,    is_exit=False, is_spacer=False))
            elif i == last:
                slots.append(ButtonSlot("Exit",  cmc=None, is_exit=True,  is_spacer=False))
            else:
                slots.append(ButtonSlot("",      cmc=None, is_exit=False, is_spacer=True))
        return slots

    # ------------------------------------------------------------------
    # Resize
    # ------------------------------------------------------------------

    def _on_resize(self, event: tk.Event) -> None:
        w, h = event.width, event.height
        if w < 10 or h < 10 or (w, h) == self._last_size:
            return
        self._last_size = (w, h)
        # Cancel any running spinner before re-render (render restarts it).
        if self._state == "printer_check" and self._printer_anim_id is not None:
            self.after_cancel(self._printer_anim_id)
            self._printer_anim_id = None
        self._render(w, h)
        if self._state == "loading":
            if self._load_anim_id is not None:
                self.after_cancel(self._load_anim_id)
                self._load_anim_id = None
            self._animate_load()

    # ------------------------------------------------------------------
    # Background helper
    # ------------------------------------------------------------------

    def _build_bg(self, w: int, h: int) -> Image.Image:
        """Return a PIL Image scaled to cover the full window."""
        src_w, src_h = self._banner_src.size
        scale = max(w / src_w, h / src_h)
        scl_w = max(1, int(src_w * scale))
        scl_h = max(1, int(src_h * scale))
        scaled = self._banner_src.resize((scl_w, scl_h), Image.LANCZOS)
        left = (scl_w - w) // 2
        top  = (scl_h - h) // 2
        return scaled.crop((left, top, left + w, top + h))

    # ------------------------------------------------------------------
    # Full render pass — branches on current state
    # ------------------------------------------------------------------

    def _render(self, w: int, h: int) -> None:
        canvas = self._canvas
        canvas.delete("all")
        self._button_items.clear()
        self._btn_photos.clear()
        self._load_bar_id = None
        self._load_bar_bounds = None

        bg = self._build_bg(w, h)
        self._bg_photo = ImageTk.PhotoImage(bg)
        canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)

        if self._state == "printer_check":
            self._render_printer_check(w, h)
        elif self._state == "buttons":
            self._render_buttons(bg, w, h)
        elif self._state == "loading":
            self._render_loading(w, h)
        elif self._state == "result":
            self._render_result(w, h)

    def _render_printer_check(self, w: int, h: int) -> None:
        """Show a full-screen splash while the printer port is being detected."""
        canvas = self._canvas
        cx, cy = w // 2, h // 2

        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 200))
        self._overlay_photo = ImageTk.PhotoImage(overlay)
        canvas.create_image(0, 0, anchor="nw", image=self._overlay_photo)

        canvas.create_text(
            cx, cy + 70,
            text="Detecting printer…",
            font=(FONT_FAMILY, 28),
            fill=COLOR_TEXT,
        )

        # Printer emoji + braille spinner — updated in-place by the animator.
        self._printer_spin_item = canvas.create_text(
            cx, cy - 20,
            text=f"🖨  {_PRINTER_SPIN[self._printer_spin_idx]}",
            font=(FONT_FAMILY, 72),
            fill=COLOR_TEXT,
        )

        # Kick off the spinner loop.
        self._animate_printer_spinner()

    def _animate_printer_spinner(self) -> None:
        if self._state != "printer_check":
            return
        self._printer_spin_idx = (self._printer_spin_idx + 1) % len(_PRINTER_SPIN)
        if self._printer_spin_item is not None:
            try:
                self._canvas.itemconfig(
                    self._printer_spin_item,
                    text=f"🖨  {_PRINTER_SPIN[self._printer_spin_idx]}",
                )
            except tk.TclError:
                pass  # canvas was cleared during a resize — new item will be created
        self._printer_anim_id = self.after(100, self._animate_printer_spinner)

    # ------------------------------------------------------------------
    # Printer detection callbacks
    # ------------------------------------------------------------------

    def _run_printer_detect(self) -> None:
        """Runs in a background thread; posts result back to the main thread."""
        port = find_target_port()
        self.after(0, self._on_printer_detected, port)

    def _on_printer_detected(self, port: str | None) -> None:
        self._printer_port = port
        elapsed_ms   = (time.monotonic() - self._printer_check_start) * 1000
        remaining_ms = max(0, int(PRINTER_CHECK_MIN_MS - elapsed_ms))
        self.after(remaining_ms, self._finish_printer_check)

    def _finish_printer_check(self) -> None:
        if self._printer_anim_id is not None:
            self.after_cancel(self._printer_anim_id)
            self._printer_anim_id = None
        if self._printer_port is not None:
            try:
                self._printer = Printer()
                self._printer.open()
            except Exception as exc:
                print(f"[printer] Failed to open: {exc}")
                self._printer = None
        self._state = "buttons"
        w, h = self._last_size
        if w > 0 and h > 0:
            self._render(w, h)

    def _render_buttons(self, bg: Image.Image, w: int, h: int) -> None:
        canvas  = self._canvas
        pad     = GRID_PAD
        btn_w   = max(10, (w - pad * (GRID_COLS + 1)) // GRID_COLS)
        btn_h   = max(10, (h - pad * (GRID_ROWS + 1)) // GRID_ROWS)
        font_sz = max(9, int(btn_h * 0.28))

        for slot_idx, slot in enumerate(self._slots):
            if slot.is_spacer:
                continue

            row, col = divmod(slot_idx, GRID_COLS)
            bx = pad + col * (btn_w + pad)
            by = pad + row * (btn_h + pad)

            normal_rgba = EXIT_NORMAL_RGBA if slot.is_exit else BTN_NORMAL_RGBA
            hover_rgba  = EXIT_HOVER_RGBA  if slot.is_exit else BTN_HOVER_RGBA

            key_n = f"s{slot_idx}_n"
            key_h = f"s{slot_idx}_h"

            slice_x2 = min(w, bx + btn_w)
            slice_y2 = min(h, by + btn_h)
            bg_slice = bg.crop((bx, by, slice_x2, slice_y2))
            if bg_slice.size != (btn_w, btn_h):
                padded = Image.new("RGB", (btn_w, btn_h), (0, 0, 0))
                padded.paste(bg_slice, (0, 0))
                bg_slice = padded

            self._btn_photos[key_n] = make_button_image(bg_slice, normal_rgba)
            self._btn_photos[key_h] = make_button_image(bg_slice, hover_rgba)

            img_id = canvas.create_image(bx, by, anchor="nw",
                                         image=self._btn_photos[key_n])
            txt_id = canvas.create_text(
                bx + btn_w // 2, by + btn_h // 2,
                text=slot.label,
                font=(FONT_FAMILY, font_sz, "bold"),
                fill=COLOR_TEXT,
            )

            item = dict(img_id=img_id, txt_id=txt_id,
                        slot=slot, key_n=key_n, key_h=key_h)
            self._button_items.append(item)

            for iid in (img_id, txt_id):
                canvas.tag_bind(iid, "<Enter>",    lambda e, it=item: self._hover(it, True))
                canvas.tag_bind(iid, "<Leave>",    lambda e, it=item: self._hover(it, False))
                canvas.tag_bind(iid, "<Button-1>", lambda e, it=item: self._click(it))

        # Slot 18 (row 3, col 3) is a spacer used for printer status.
        slot18_bx  = pad + 3 * (btn_w + pad)
        slot18_by  = pad + 3 * (btn_h + pad)
        status_cx  = slot18_bx + btn_w // 2
        status_cy  = slot18_by + btn_h // 2
        status_fsz = max(8, int(btn_h * 0.18))
        if self._printer_port is None:
            canvas.create_text(
                status_cx, status_cy,
                text="🙅\nPRINTER PORT\nNOT FOUND",
                font=(FONT_FAMILY, status_fsz, "bold"),
                fill="#FF4444",
                anchor="center",
                justify="center",
            )
        else:
            canvas.create_text(
                status_cx, status_cy,
                text="👌\nPRINTER OK",
                font=(FONT_FAMILY, status_fsz, "bold"),
                fill="#44FF88",
                anchor="center",
            )

    def _render_loading(self, w: int, h: int) -> None:
        canvas = self._canvas
        cx, cy = w // 2, h // 2

        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 120))
        self._overlay_photo = ImageTk.PhotoImage(overlay)
        canvas.create_image(0, 0, anchor="nw", image=self._overlay_photo)

        canvas.create_text(cx, cy - 60, text="Searching...",
                           font=(FONT_FAMILY, 36, "bold"), fill=COLOR_TEXT)

        bar_w = int(w * 0.5)
        bar_h = 18
        bx    = cx - bar_w // 2
        by    = cy - bar_h // 2
        canvas.create_rectangle(
            bx - 2, by - 2, bx + bar_w + 2, by + bar_h + 2,
            outline="#ffffff", fill="", width=2,
        )
        self._load_bar_id = canvas.create_rectangle(
            bx, by, bx, by + bar_h, fill="#ffffff", outline=""
        )
        self._load_bar_bounds = (bx, by, bar_w, bar_h)

    def _render_result(self, w: int, h: int) -> None:
        canvas = self._canvas
        cx, cy = w // 2, h // 2

        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 150))
        self._overlay_photo = ImageTk.PhotoImage(overlay)
        canvas.create_image(0, 0, anchor="nw", image=self._overlay_photo)

        canvas.create_text(cx, cy, text="Printing...",
                           font=(FONT_FAMILY, 48, "bold"), fill=COLOR_TEXT)

    # ------------------------------------------------------------------
    # Lookup state machine
    # ------------------------------------------------------------------

    def _start_lookup(self, cmc: int) -> None:
        if self._load_anim_id is not None:
            self.after_cancel(self._load_anim_id)
            self._load_anim_id = None

        self._state = "loading"
        self._pending_creature = None
        self._waiting_for_result = False
        self._load_start_time = time.monotonic()

        threading.Thread(
            target=self._fetch_creature, args=(cmc,), daemon=True
        ).start()

        w, h = self._last_size
        self._render(w, h)
        self._animate_load()

    def _fetch_creature(self, cmc: int) -> None:
        creature = random_creature(cmc)
        self.after(0, self._on_creature_ready, creature)

    def _on_creature_ready(self, creature: dict | None) -> None:
        self._pending_creature = creature
        if self._waiting_for_result:
            self._show_result()

    def _animate_load(self) -> None:
        if self._state != "loading":
            return
        elapsed_ms = (time.monotonic() - self._load_start_time) * 1000
        progress   = min(1.0, elapsed_ms / LOAD_DURATION_MS)

        if self._load_bar_id is not None and self._load_bar_bounds is not None:
            bx, by, bar_w, bar_h = self._load_bar_bounds
            fill_x2 = bx + max(0, int(bar_w * progress))
            self._canvas.coords(self._load_bar_id, bx, by, fill_x2, by + bar_h)

        if progress < 1.0:
            self._load_anim_id = self.after(16, self._animate_load)
        else:
            self._load_anim_id = None
            if self._pending_creature is not None:
                self._show_result()
            else:
                self._waiting_for_result = True

    def _show_result(self) -> None:
        self._state = "result"
        w, h = self._last_size
        self._render(w, h)
        if self._pending_creature and self._printer is not None:
            try:
                self._printer.print_card(self._pending_creature)
            except Exception as exc:
                print(f"[printer] print_card failed: {exc}")
        self.after(1000, self._return_to_buttons)

    def _return_to_buttons(self) -> None:
        self._state = "buttons"
        w, h = self._last_size
        self._render(w, h)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _hover(self, item: dict, entering: bool) -> None:
        key = item["key_h"] if entering else item["key_n"]
        self._canvas.itemconfig(item["img_id"], image=self._btn_photos[key])
        self._canvas.config(cursor="hand2" if entering else "")

    def _click(self, item: dict) -> None:
        slot: ButtonSlot = item["slot"]
        if slot.is_exit:
            if self._printer is not None:
                self._printer.close()
            self.destroy()
        elif slot.cmc is not None:
            self._start_lookup(slot.cmc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = MomirVigApp()
    app.mainloop()


if __name__ == "__main__":
    main()
