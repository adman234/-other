# Scorbot ER-4u ESP32 master board (KiCad)

KiCad project for the replacement controller: an ESP32 DevKit and six Pololu
DRV8874 carriers plug into a master board, and the robot's DD-50 cable plugs
straight into the board edge.

**Status: v0.1, generated, schematic complete, PCB placed but NOT routed.**

| File | What it is |
| --- | --- |
| `scorbot_controller.kicad_pro/.kicad_sch/.kicad_pcb` | The KiCad project (opens in KiCad 7, 8 or 9) |
| `lib/Scorbot.kicad_sym`, `lib/Scorbot.pretty/` | Custom parts: DevKit socket, DRV8874 carrier socket, DD-50 female R/A |
| `docs/schematic.pdf`, `docs/pcb_placement.png` | Previews |
| `bom.csv` | Grouped BOM |
| `generator/design.py` | **Single source of truth**: every part, footprint, pin-to-net map and placement |
| `generate.sh` | Regenerates everything from `design.py`, then checks it |

## Regenerating

```sh
pip install kiutils cairosvg   # plus KiCad 7+ (kicad-cli and its pcbnew Python module)
./generate.sh
```

`generate.sh` writes the library, schematic, PCB and BOM, then:

- exports KiCad's own netlist from the schematic and checks it against
  `design.py` pin by pin (currently 412 connections, 0 mismatches);
- runs KiCad DRC on the board. Current result: no courtyard overlaps or
  clearance errors; 278 unconnected items (expected, the board is unrouted)
  and a few silkscreen warnings. `lib_footprint_issues` only appears when the
  standard KiCad libraries aren't in the global library table.

Editing the `.kicad_sch` / `.kicad_pcb` by hand is fine once you take over
the design; just stop running the generator after that, or it overwrites your
changes.

## Must check before ordering boards

1. **Pololu carrier pin order is a placeholder.** The pin *names* are right
   (VIN, VM, GND, OUT1, OUT2, PMODE, IMODE, EN, PH, SLEEP, FAULT, CS, VREF), but
   their physical order and the row spacing were guessed. Fix
   `CARRIER_ROWS` / `CARRIER_ROW_SPACING` in `design.py` from Pololu's #4035
   pinout drawing and regenerate.
2. **DevKit row spacing.** `DEVKIT_ROW_SPACING = 25.4` mm. Measure your
   38-pin ESP32-DevKitC-32E; clones vary.
3. **DD-50 footprint** is generated from KiCad's D-sub geometry (pitch
   2.77 x 2.84 mm, rows 1 and 3 aligned, D-shell hole spacing 61.11 mm). Check it
   against the drawing of the exact right-angle connector you buy.
4. **Routing.** Not done. The project's net classes are set up: Motor
   (VM, VIN*, MOT*, GND) 1.0 mm, Power (+5V*, +3V3, BUCK_SW, ENC*_VLED) 0.6 mm,
   Default 0.25 mm. A GND pour on B.Cu is defined; keep copper out from
   under the DevKit antenna (rule area `ANTENNA_KEEPOUT`).

## Design notes

- **DB50 pinout**: SCORBOT-ER 4u User Manual, Chapter 8 (identical to the
  ER III manual Table D-1). Pins 37-44 unused; pin 22 (gripper switch) has no
  connection in the arm. Motor +/- labels differ between manuals; firmware
  sets direction.
- **Encoders**: bare IR LEDs + phototransistors. 47 R per axis from 5 V for
  VLED (ER III reference value; check against the ER-4u's upgraded PC510
  encoders), 10 k pull-ups to 5 V, 1 nF, then 74LVC14 at 3.3 V into the ESP32.
- **E-stop**: no relay; the NC contacts sit in the VM line (J3), so they must
  be rated for total motor current. Logic stays powered.
- **Carrier current limit**: VREF is pulled to SLEEP (3.3 V) by 10 k on the
  carrier and CS has 2.49 k, giving about 2.9 A. R21-R26 (DNP) lower it.
- **ADS7828** uses its internal 2.5 V reference: CS reads 1.12 V/A, so
  readings clip above about 2.2 A.
- Plan, firmware architecture and API: see the "Scorbot ER-4u ESP32
  Controller Plan" doc.
