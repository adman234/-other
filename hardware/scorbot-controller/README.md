# Scorbot ER-4u ESP32 master board (KiCad)

KiCad project for the replacement controller: an ESP32 DevKit, six Pololu
DRV8874 carriers, a DROK mini 5 V buck module and two ADS1115 ADC
modules plug into a master board, and the robot's DD-50 cable plugs straight
into the board edge. The only SMD chip left for JLCPCB to place is the
TCA9555; everything else on the board is passives, LEDs and diodes.

**Status: v0.2, generated: schematic complete, PCB placed and autorouted (needs a human review before ordering).**

| File | What it is |
| --- | --- |
| `scorbot_controller.kicad_pro/.kicad_sch/.kicad_pcb` | The KiCad project (opens in KiCad 7, 8 or 9) |
| `lib/Scorbot.kicad_sym`, `lib/Scorbot.pretty/` | Custom parts: DevKit socket, DRV8874 carrier socket, DD-50 female R/A |
| `docs/schematic.pdf`, `docs/pcb_placement.png`, `docs/pcb_routed_F.png`, `docs/pcb_routed_B.png` | Previews (B side is mirrored) |
| `bom.csv` | Grouped BOM |
| `generator/design.py` | **Single source of truth**: every part, footprint, pin-to-net map and placement |
| `generate.sh` | Regenerates everything from `design.py`, then checks it |

## Regenerating

```sh
pip install kiutils cairosvg   # plus KiCad 7+ (kicad-cli and its pcbnew Python module)
# optional autorouting: Java 17+, xvfb-run and the Freerouting 1.9.0 jar
# (https://github.com/freerouting/freerouting/releases/tag/v1.9.0)
FREEROUTING_JAR=/path/freerouting-1.9.0.jar ./generate.sh
```

Routing takes about 3 minutes. `gen_route.py` retries up to `FREEROUTING_TRIES`
times (default 3) because Freerouting's result varies between runs, and it
keeps the run with the fewest unconnected items.

`generate.sh` writes the library, schematic, PCB and BOM, then:

- exports KiCad's own netlist from the schematic and checks it against
  `design.py` pin by pin (currently 354 connections, 0 mismatches);
- runs KiCad DRC on the board. Current result: 0 unconnected items, 0
  clearance or courtyard errors, and a few silkscreen and thermal-relief
  warnings. `lib_footprint_issues` only appears when the
  standard KiCad libraries aren't in the global library table.

Editing the `.kicad_sch` / `.kicad_pcb` by hand is fine once you take over
the design; just stop running the generator after that, or it overwrites your
changes.

## Must check before ordering boards

Blockers (the board is wrong or unbuildable until these are done):

1. **Pololu carrier pin order is a placeholder, and probably wrong.** The
   #4035 board is 0.6" x 0.7", which only fits 7 pins per side, but
   `CARRIER_ROWS` has 6 + 8. IMODE is most likely not broken out (the
   carrier pulls it to GND through 20 k), leaving 7 logic pins. Copy the
   real order and row spacing from Pololu's pinout drawing (or a carrier in
   hand) into `CARRIER_ROWS` / `CARRIER_ROW_SPACING`, then re-run
   `generate.sh`; it re-places and re-routes the board.
2. **DevKit row spacing.** `DEVKIT_ROW_SPACING = 25.4` mm. Measure your
   38-pin DevKitC between pin centres; clones vary.
3. **DD-50 footprint** is generated from KiCad's D-sub geometry (pitch
   2.77 x 2.84 mm, rows 1 and 3 aligned, D-shell hole spacing 61.11 mm).
   Print the board 1:1 and lay the connector on it, or check the drawing of
   the exact right-angle part you buy.
4. **JLC part numbers.** `fab/jlc_bom.csv` holds LCSC numbers from memory
   and search snapshots. Look each one up in JLCPCB's parts library (value,
   package, Basic vs Extended, stock) before ordering.

Check in KiCad / the JLC preview:

5. **Module pin orders**: the DROK buck socket (`BUCK_PINS`: IN+, IN-,
   OUT-, OUT+; the module's EN pin hangs off the end) and the ADS1115
   breakout (`ADS1115_PINS`, Adafruit #1085 order; other sellers differ).
6. **Autorouted copper.** Freerouting's result is DRC-clean but not pretty:
   long parallel runs across the middle, and motor/VM traces near the
   encoder lines. Look over the VM and MOT paths in particular.
7. **Copper weight.** VM carries up to the 5 A fuse current on 1.5 mm
   traces, and the motor outputs up to about 2.9 A (carrier limit) on
   0.8 mm. On 1 oz copper that is roughly a 20-25 C rise at those peaks.
   Order 2 oz copper, or widen VM/MOT in `netclasses.py` / `gen_route.py`.
8. **Part orientation in JLC's placement preview**: diode and LED
   polarity (D1, D3-D7) and pin 1 of U5. KiCad-to-JLC rotations sometimes
   need a 90/180 degree fix in `jlc_cpl.csv`.
9. **Screw terminal wire entry** (J2, J3, J7) should face the board edge.
   Check in KiCad's 3D viewer.
10. **Mechanical fit**: board is 185 x 128 mm with M3 holes in the
    corners; check the enclosure and that the DevKit's USB port and
    antenna end are reachable/clear.

## Ordering at JLCPCB

`generator/gen_fab.py` (run by `generate.sh`) writes `fab/`:

- `scorbot_controller_gerbers.zip`: upload as the PCB (2 layers, 1.6 mm,
  2 oz copper recommended).
- `jlc_bom.csv` and `jlc_cpl.csv`: upload for PCB Assembly, top side only.
  These list only the SMD parts JLC places. Everything else is
  hand-soldered: see the buy list in the plan doc.

## Design notes

- **DB50 pinout**: SCORBOT-ER 4u User Manual, Chapter 8 (identical to the
  ER III manual Table D-1). Pins 37-44 unused; pin 22 (gripper switch) has no
  connection in the arm. Motor +/- labels differ between manuals; firmware
  sets direction.
- **Encoders**: bare IR LEDs + phototransistors. 47 R per axis from 5 V for
  VLED (ER III reference value; check against the ER-4u's upgraded PC510
  encoders). P0/P1 have 4.7 k pull-ups to 3.3 V and 1 nF, straight into the
  ESP32 (no buffer); the PCNT glitch filter handles noise.
- **Power input, two options**: barrel jack J1 or screw terminal J2 (same
  nets; use one).
- **Fuse, two options**: fit EITHER the on-board mini blade holder F1
  (Keystone 3568, 5 A ATM fuse) OR the screw terminal J7 with an inline
  fuse holder wired across it. They are in parallel: two fuses in at once
  double the trip current, and a bare wire in J7 bypasses F1.
- **No reverse-polarity FET**: a reversed supply forward-biases the
  unidirectional SMBJ18A and blows the fuse.
- **E-stop**: no relay; the NC contacts sit in the VM line (J3), so they must
  be rated for total motor current. Logic stays powered.
- **Carrier current limit**: VREF is pulled to SLEEP (3.3 V) by 10 k on the
  carrier and CS has 2.49 k, giving about 2.9 A. R21-R26 (DNP) lower it.
- **Current sense**: two ADS1115 modules (0x48: CS1-4, 0x49: CS5, CS6 and
  VM/5.7). CS reads 1.12 V/A; use the ADS1115's +/-4.096 V range.
- Plan, firmware architecture and API: see the "Scorbot ER-4u ESP32
  Controller Plan" doc.
