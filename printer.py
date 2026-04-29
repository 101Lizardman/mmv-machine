"""Serial interface for the EPSON TM-U220D receipt printer (ESC/POS)."""

from __future__ import annotations

import configparser
from pathlib import Path

import serial
import serial.tools.list_ports

from detect_printer_port import find_target_port

# ---------------------------------------------------------------------------
# ESC/POS command constants
# ---------------------------------------------------------------------------

ESC_INIT      = b"\x1b\x40"            # ESC @      — initialize printer
# GS V 65 0 (Function B) — feed paper to cutter position, then full cut.
# Function A (GS V 0) cuts immediately without feeding, leaving text above
# the blade uncut. Function B is required for the TM-U220D.
CUT_FULL      = b"\x1d\x56\x41\x00"   # GS V 65 0  — feed-and-full-cut
LF            = b"\x0a"                # line feed
CR            = b"\x0d"                # carriage return

_CONFIG_FILE = Path(__file__).with_name("printer_config.ini")


# ---------------------------------------------------------------------------
# Printer class
# ---------------------------------------------------------------------------

class Printer:
    """Thin wrapper around a serial connection to the TM-U220D."""

    def __init__(self, config_path: Path = _CONFIG_FILE) -> None:
        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        section = cfg["printer"]

        self._port     = section["port"]
        self._baud     = int(section["baud"])
        self._bytesize = int(section["bytesize"])
        self._parity   = section["parity"]
        self._stopbits = float(section["stopbits"])
        # DTR/DSR flow control — the TM-U220D asserts DSR to signal ready;
        # without this, bytes are dropped when the printer buffer is full.
        self._dsrdtr   = section.getboolean("dsrdtr", fallback=False)
        self._conn: serial.Serial | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the serial port, falling back to auto-detection if the
        configured port is not present in the system."""
        port = self._resolve_port()
        try:
            self._conn = serial.Serial(
                port=port,
                baudrate=self._baud,
                bytesize=self._bytesize,
                parity=self._parity,
                stopbits=self._stopbits,
                timeout=2,
                write_timeout=2,
                dsrdtr=self._dsrdtr,
                rtscts=False,
            )
        except serial.SerialException as exc:
            if getattr(exc, 'args', None) and '31' in str(exc):
                raise RuntimeError(
                    f"COM port '{port}' opened but the driver refused configuration "
                    f"(Windows error 31 — CH340 adapter is in a bad state).\n"
                    f"Fix: unplug the USB-to-serial adapter, wait a second, then plug it back in."
                ) from exc
            raise
        self._conn.write(ESC_INIT)
        self._conn.flush()

    def _resolve_port(self) -> str:
        """Return the port to use, auto-detecting if the config value is
        absent or not enumerated by the OS."""
        available = {p.device.upper() for p in serial.tools.list_ports.comports()}

        if self._port and self._port.upper() in available:
            return self._port

        # Configured port not visible — try the USB adapter auto-detection.
        detected = find_target_port()
        if detected:
            print(f"[printer] Configured port '{self._port}' not found; "
                  f"using auto-detected port '{detected}'.")
            return detected

        # Nothing found — give a helpful diagnostic before raising.
        if available:
            ports_str = ", ".join(sorted(available))
            raise RuntimeError(
                f"Configured port '{self._port}' not found and auto-detection "
                f"found no matching adapter.\n"
                f"Available ports: {ports_str}\n"
                f"Check that the printer is on and the USB cable is connected."
            )
        raise RuntimeError(
            f"No serial ports detected at all. "
            f"Check that the USB-to-serial adapter is plugged in and drivers are installed."
        )

    def close(self) -> None:
        """Close the serial port if open."""
        if self._conn and self._conn.is_open:
            self._conn.close()
        self._conn = None

    def __enter__(self) -> "Printer":
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, data: bytes | str) -> None:
        """Send raw bytes (or a string) to the printer.

        Strings are encoded as latin-1 (single-byte), which maps directly to
        the printer's built-in character table and avoids multi-byte sequences
        that would confuse the ESC/POS parser.
        """
        if self._conn is None or not self._conn.is_open:
            raise RuntimeError("Printer is not open. Call open() first.")
        if isinstance(data, str):
            data = data.encode("latin-1")
        self._conn.write(data)
        self._conn.flush()

    def cut(self) -> None:
        """Issue a full paper cut."""
        self.write(CUT_FULL)

    # ------------------------------------------------------------------
    # Card formatting
    # ------------------------------------------------------------------

    _LINE_WIDTH = 40   # characters per line at standard font on TM-U220D

    def print_card(self, card: dict) -> None:
        """Print a card-layout receipt for *card*.

        Layout mirrors a physical MTG card (minus artwork):

            Name                   {Mana}
            ----------------------------------------
            Type - Subtype
            ----------------------------------------
            Card text wrapped to line width,
            with blank line between paragraphs.
            ----------------------------------------
                                        P/T
        """
        W = self._LINE_WIDTH
        rule = "-" * W

        import json as _json

        name      = (card.get("name")      or "").strip()
        mana      = (card.get("mana_cost") or "").strip()
        text      = (card.get("text")      or "").strip()
        power     = (card.get("power")     or "").strip()
        toughness = (card.get("toughness") or "").strip()

        # types and subtypes are stored as JSON arrays e.g. '["Creature"]'
        def _parse_list(raw) -> list[str]:
            if not raw:
                return []
            try:
                val = _json.loads(raw)
                return val if isinstance(val, list) else [str(val)]
            except (ValueError, TypeError):
                return [str(raw)]

        types_str    = " ".join(_parse_list(card.get("types")))
        subtypes_str = " ".join(_parse_list(card.get("subtypes")))

        # --- name / mana line -------------------------------------------
        # Right-align mana cost; truncate name if needed to leave room.
        mana_col = len(mana)
        gap      = 1  # minimum space between name and mana
        max_name = W - mana_col - gap
        if len(name) > max_name:
            name = name[: max_name - 1] + ">"
        header = name.ljust(W - mana_col) + mana

        # --- type line --------------------------------------------------
        type_line = f"{types_str} - {subtypes_str}" if subtypes_str else types_str

        # --- body text — word-wrap each paragraph -----------------------
        def wrap(paragraph: str) -> list[str]:
            words, lines, current = paragraph.split(), [], ""
            for word in words:
                candidate = f"{current} {word}".lstrip()
                if len(candidate) <= W:
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)
            return lines or [""]

        # Paragraphs are separated by \n in the card text.
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        body_lines: list[str] = []
        for i, para in enumerate(paragraphs):
            if i > 0:
                body_lines.append("")          # blank line between paragraphs
            body_lines.extend(wrap(para))

        # --- power / toughness ------------------------------------------
        pt = f"{power}/{toughness}" if power or toughness else ""

        # --- assemble & send --------------------------------------------
        lines: list[str] = [
            header,
            rule,
            type_line,
            rule,
            *body_lines,
            rule,
        ]
        if pt:
            lines.append(pt.rjust(W))

        for line in lines:
            self.write(line.encode("latin-1", errors="replace") + CR + LF)

        self.write(LF * 2)
        self.cut()

    def printer_test(self) -> None:
        """Print a short test pattern and cut the paper."""
        self.write(CR + LF)
        self.write(b"...printer test..." + CR + LF)
        # Feed a few blank lines so the text clears the print head before
        # the cut command feeds to the cutter position.
        self.write(LF * 3)
        self.cut()


# ---------------------------------------------------------------------------
# Quick manual test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    with Printer() as p:
        p.printer_test()
    print("Test page sent.")
