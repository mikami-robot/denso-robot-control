# DENSO Robot Control System
### Built with Claude Code Opus 4.7 — in 4.5 hours

> *"Weeks of industrial integration, done in an afternoon."*

A fully functional industrial robot control system built from scratch using **Claude Code Opus 4.7** as the primary development partner. What would typically take 2–4 weeks of engineering was completed in a single **4.5-hour session**.

---

## Hardware

| Device | Interface | Protocol |
|---|---|---|
| DENSO 6-axis Robot (RC8) | Gigabit Ethernet | b-CAP TCP |
| IAI Electric Hand (Cylinder) | USB Serial (RS-485) | Modbus RTU |
| Basler GigE Camera (4K, a2A3840) | Gigabit Ethernet | GigE Vision / pypylon |
| USB Webcam | USB | OpenCV / AVFoundation |
| PS5 DualSense Gamepad | Bluetooth | pygame HID |

---

## Features

- **Dual control modes** — PC mode (discrete `robot_move()`) and Gamepad mode (125Hz slave streaming via `slvMove`)
- **Real-time MJPEG streaming** — Dual camera feeds (4K Basler + USB) in the browser
- **6-DOF teleoperation** — Full X/Y/Z translation and RX/RY/RZ rotation via gamepad or web UI
- **Electric hand control** — Proportional open/close with dedicated Modbus thread (no gamepad blocking)
- **Velocity buffer architecture** — Jitter-free gamepad control via overwrite-style velocity commands
- **Auto error recovery** — Slave mode fault detection with automatic Motor ON re-initialization
- **Cyberpunk Web UI** — Real-time coordinate display, animated gradients, Web Audio sound effects, tab-based mode switching

---

## Architecture

```
Browser (Flask Web UI)
    │  REST API / MJPEG stream
    ▼
app.py (Flask)
    ├── DensoRobot ──── b-CAP TCP ────▶ DENSO RC8
    │     ├── Normal mode: robot_move()   (PC buttons)
    │     └── Slave mode:  slvMove 125Hz  (Gamepad)
    │           └── Velocity Buffer (decoupled from gamepad thread)
    ├── IAIHand ──────── Modbus RTU ───▶ IAI Electric Hand
    │     └── hand-ctrl thread (non-blocking, instant stop)
    ├── BaslerCamera ─── GigE Vision ──▶ Basler 4K Camera
    ├── USBCamera ─────── OpenCV ───────▶ USB Webcam
    └── GamepadDriver ─── pygame HID ──▶ PS5 DualSense
          └── 125Hz polling → Velocity Buffer write
```

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure hardware IPs in config.py
# DENSO_HOST = "192.168.127.13"
# BASLER_CAMERA_IP (set via pypylon / GigE Vision)
# IAI_SERIAL_PORT = "/dev/cu.usbserial-XXXXXXXX"

# Run
python app.py
```

Open `http://localhost:8080` in your browser.

---

## Key Technical Challenges Solved

**1. Wrist Singularity (b-CAP slave mode crash)**
The robot's default coordinate frame placed the TCP near a wrist singularity (rx≈180°, ry≈0°). Any slave mode movement caused immediate joint velocity violations. Fixed by calling `robot_change(handle, "Tool1")` before CurPos acquisition.

**2. Gamepad Jitter (delta buffer vs velocity buffer)**
Accumulating deltas caused double-application when gamepad and slave loop cycles drifted. Replaced with an overwrite-style velocity buffer — the gamepad writes "current velocity" every frame, the slave loop applies it at a fixed rate regardless of timing.

**3. Modbus Blocking (IAI hand latency)**
Modbus RTU writes (~15ms) were blocking the gamepad thread, delaying button release detection. Isolated into a dedicated `hand-ctrl` thread — the gamepad only writes a velocity integer, achieving instant stop on button release.

**4. Basler Camera IP Configuration**
Camera shipped with no IP assigned (link-local 169.254.x.x). Configured via raw GVCP register writes (PersistentIP 0x064C, ForceIP command) since pypylon NodeMap had no IP configuration API on this firmware.

---

## Built With

- **Claude Code Opus 4.7** — primary development partner
- Python 3.12 / Flask
- pypylon, pymodbus, pygame, OpenCV
- pybcapclient (DENSO b-CAP)

---

## Hackathon

Built for **"Built with Opus 4.7" Hackathon** — April 2026.

*One AI. Five machines. 4.5 hours. Zero prior code.*

---

## License

MIT
