# Momir Vig Machine

A fullscreen MTG "Momir Vig" simulator. Click a CMC button to draw a random creature at that mana value from a local SQLite database populated from MTGJson data. When a creature is drawn, its card details are printed to an EPSON TM-U220D receipt printer via a USB-to-serial adapter.

---

## Files

| File | Purpose |
|---|---|
| `momir_vig_machine.py` | Main application — fullscreen tkinter UI |
| `lookup_creature.py` | DB lookup module (also runnable as a CLI tool) |
| `import_creatures.py` | One-time importer: streams `AtomicCards.json` into `creatures.db` |
| `detect_printer_port.py` | Detects the CH340 USB-serial adapter and writes its port to `printer_config.ini` |
| `printer.py` | Serial interface to the EPSON TM-U220D (also runnable as a test) |
| `printer_config.ini` | Serial port settings (port, baud, flow control) |
| `creatures.db` | SQLite database of creature cards, partitioned by CMC |
| `AtomicCards.json` | Source data from [MTGJson](https://mtgjson.com/downloads/all-files/) |
| `image.png` | Background artwork (Momir Vig) — must be present at runtime |

---

## System Requirements

### Python

Python **3.10 or newer** is required (`X | Y` union type syntax is used in annotations).

### Python packages

| Package | Version | Used by |
|---|---|---|
| `pillow` | 12.2.0 | `momir_vig_machine.py` — UI image compositing |
| `ijson` | 3.5.0 | `import_creatures.py` — streaming JSON parse |
| `pyserial` | 3.5 | `printer.py`, `detect_printer_port.py` — serial communication |

Install all at once:

```bash
pip install pillow==12.2.0 ijson==3.5.0 pyserial==3.5
```

### Raspberry Pi / Debian system packages

```bash
sudo apt update
sudo apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-tk \
    libopenjp2-7 \
    libtiff6
```

> `python3-tk` provides tkinter, which is **not** included in the standard Python package on most Debian/Ubuntu systems. `libopenjp2-7` and `libtiff6` are optional Pillow dependencies for JPEG2000/TIFF support.

For a headless Raspberry Pi (no desktop), you also need a display server. The easiest option is to run under the desktop environment (LXDE/Pixel) or configure a framebuffer. The app calls `root.state("zoomed")` which requires a running display.

---

## Hardware

- **Printer**: EPSON TM-U220D (9-pin dot matrix, ESC/POS)
- **Connection**: USB-to-serial adapter with CH340 chipset (VID `1A86`, PID `7523`)
- **Cable**: Standard RS-232 serial cable (no hardware flow control required)

### Printer configuration (`printer_config.ini`)

```ini
[printer]
port = COM3        ; Windows COM port (Linux: /dev/ttyUSB0 etc.)
baud = 9600
bytesize = 8
parity = N
stopbits = 1
dsrdtr = no        ; set to yes only if cable has DTR/DSR wired
```

Run `detect_printer_port.py` to auto-detect the adapter port and write it to the config:

```bash
python detect_printer_port.py
```

### CH340 driver note (Windows)

The CH340 driver can enter a bad state after an abnormal process exit, causing `SetCommState` to fail with Windows error 31. The fix is to **unplug and replug the USB adapter**. To avoid this, keep the application running rather than opening and closing the port repeatedly — `momir_vig_machine.py` opens the port once at startup and closes it only on exit.

---

## Setup

```bash
git clone <repo-url>
cd mmv-machine

python3 -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install pillow==12.2.0 ijson==3.5.0 pyserial==3.5
```

---

## Populating the Database

Download `AtomicCards.json` from [https://mtgjson.com/downloads/all-files/](https://mtgjson.com/downloads/all-files/) and place it in the project root, then run:

```bash
python import_creatures.py --max-entries 0
```

| Flag | Default | Description |
|---|---|---|
| `--input PATH` | `AtomicCards.json` | Path to MTGJson AtomicCards file |
| `--db PATH` | `creatures.db` | Output SQLite database path |
| `--max-entries N` | `100` | Limit rows imported; `0` = no limit (full import) |
| `--no-clear` | — | Append to existing DB instead of rebuilding |

A full import takes ~3 seconds and loads ~18,200 creatures.

---

## Running

```bash
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
python detect_printer_port.py   # first time only — writes port to printer_config.ini
python momir_vig_machine.py
```

The app starts maximized. Click any CMC button (0–16) to draw a random creature at that cost. The card details are printed automatically. The Exit button closes the app and the printer connection.

### Printer test

```bash
python printer.py
```

Prints a short test line and cuts the paper.

### CLI lookup tool

```bash
python lookup_creature.py 5
# CMC 5: Mulldrifter  2/2  {4}{U}
```

---

## Autostart on Boot (Raspberry Pi)

To launch on desktop login, create `~/.config/autostart/momir.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Momir Vig Machine
Exec=/home/pi/mmv-machine/.venv/bin/python /home/pi/mmv-machine/momir_vig_machine.py
```

Adjust the paths to match your install location.
