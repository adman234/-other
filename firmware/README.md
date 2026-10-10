# Scorbot ER-4u controller firmware

ESP-IDF firmware and web UI for the ESP32 replacement controller in `hardware/scorbot-controller/`.

It runs on an ESP32-DevKitC with either the WROOM-32D or the WROOM-32E module. Both use the same chip, the same GPIOs and the same 38-pin layout, so no pin changes are needed.

## What it does

The `control` task runs on core 1 at 1 kHz. It does the following:
- Reads the six quadrature encoders (PCNT, x4 counting).
- Runs PID with synchronized trapezoid moves.
- Drives the six DRV8874 boards with 20 kHz PWM.
- Handles the wrist as a differential pair: pitch = (m4+m5)/2, roll = (m4-m5)/2.

The `io` task runs on core 0 at 500 Hz. It reads these over I2C:
- TCA9555 (0x20): home switches, DRV nFAULT, VM sense, PH lines.
- 2× ADS1115 (0x48 and 0x49): motor currents and VM voltage.

Safety checks:
- The E-stop removes VM. The firmware latches an `estop` mode until you clear it.
- It also faults on DRV nFAULT, over-current (filtered over 50 ms) and following error.

Networking:
- WiFi station mode. If it can't connect within 60 s, it also opens the AP `Scorbot-XXXX` (password `scorbot1`) with a captive portal at 192.168.4.1.
- The device answers at `http://scorbot.local` (mDNS).

The web UI is one file, `web/index.html`, embedded in the firmware. It has four tabs:
- **Control:** live 3D view, hold-to-jog, typed targets, home all, gripper.
- **Test:** self-test, home-switch lamps, motor direction/current test with Flip, encoder activity.
- **Calibrate:** homing, a measure-travel routine (drives into both hard stops and computes counts/deg, home offset and soft limits), invert, set zero, roll one-turn method.
- **Settings:** WiFi, API key, 3D geometry, joint/motor tuning, import/export.

## Build and flash

You need ESP-IDF 5.3 or newer.

```
cd firmware
idf.py set-target esp32
idf.py build flash monitor
```

On first boot there are no WiFi settings, so join `Scorbot-XXXX` and set WiFi under Settings. Then:
1. Run **Test → Self-test**. Fix any motor reported as "disagree" with **Flip**.
2. Check each home switch with the switch lamps.
3. Run **Calibrate → Measure travel** per joint and **Apply**.
4. Run **Home all**.

## HTTP / WebSocket API

| Endpoint | |
|---|---|
| `GET /api/state` | Live state JSON. The same JSON is pushed on `/ws` at 20 Hz. |
| `POST /api/cmd` | `{"cmd": ...}` (see below). WebSocket messages take the same form, plus an optional `id`. |
| `GET/POST /api/config` | Joint/motor configuration. A POST may be a partial update and is saved to NVS. |
| `GET/POST /api/wifi` | WiFi, hostname and API key. A POST saves and reboots. |
| `POST /api/reboot` | Reboots the controller. |

If an API key is set, send it as the `X-API-Key` header, or as `"key"` in WebSocket messages.

Commands for `POST /api/cmd`:
- `enable`, `disable`, `stop`, `reset`
- `jog {joint, speed}`: `speed` is -1..1 of vmax; resend it within 300 ms to keep moving.
- `move {pos:[deg|null ×6], speed 0..1}`
- `home {joint}`: -1 = all.
- `calibrate {joint}`, `apply_calibration {joint}`: -1 = all.
- `test_motor {motor}`, `self_test`, `flip_motor {motor}`
- `invert_joint {joint}`, `zero_here {joint}`, `set_cpu {joint, value}`
- `reset_switch_test`, `reset_encoder_test`, `defaults`

Joint order is base, shoulder, elbow, pitch, roll, gripper. Angles are in degrees; the gripper is in mm.

## Testing without hardware

The robot core (`main/core/`), `api.cpp` and `settings.cpp` are portable C++. They are tested on a PC against a physics simulation of the arm (`test/sim_hal.h`). The sim models friction, hard stops, switches, the differential wrist, reversed wiring, jams and loss of VM.

```
make -C test run                                  # core unit tests
git clone https://github.com/DaveGamble/cJSON /tmp/cJSON
make -C test sim_server CJSON=/tmp/cJSON
pip install aiohttp playwright
python3 test/sim_bridge.py 8080                   # real UI + real core on the sim -> http://localhost:8080
python3 test/ui_test.py                           # end-to-end browser test
```

To try the UI on its own, open `web/index.html?demo`. It also opens directly from disk, and in that case runs a built-in mock arm.

## Caveats

- `hal_esp32.cpp`, `net.cpp`, `web.cpp` and `main.cpp` passed a cross syntax check against the ESP-IDF 5.3.2 headers. They have not yet been built with the real Xtensa toolchain or run on hardware, so expect the first `idf.py build` to need some small fixes.
- The defaults for counts per degree, gear ratios and the 3D geometry are estimates from the ER-4u manual. Run the calibration routine and adjust the geometry in Settings.
- PID gains and minimum duty are starting points. The Scorbot motors need a fairly high minimum PWM.
