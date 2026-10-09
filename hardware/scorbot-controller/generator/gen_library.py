"""Generate the project-local KiCad library: lib/Scorbot.kicad_sym and lib/Scorbot.pretty."""
import os
import uuid

import design as D

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "lib")
FONT = "(effects (font (size 1.27 1.27)))"
FONT_HIDE = "(effects (font (size 1.27 1.27)) hide)"


def u():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------
def symbol(name, ref, value, footprint, left, right, width=20.32, desc=""):
    """left/right: lists of (number, name, type); drawn top to bottom."""
    rows = max(len(left), len(right))
    top = (rows - 1) * 2.54 / 2
    top = round(top / 1.27) * 1.27
    half = width / 2
    body_top, body_bot = top + 2.54, top - rows * 2.54
    out = [f'  (symbol "{name}" (in_bom yes) (on_board yes)',
           f'    (property "Reference" "{ref}" (at {-half:.2f} {body_top + 1.27:.2f} 0) (effects (font (size 1.27 1.27)) (justify left)))',
           f'    (property "Value" "{value}" (at {-half:.2f} {body_bot - 1.27:.2f} 0) (effects (font (size 1.27 1.27)) (justify left)))',
           f'    (property "Footprint" "{footprint}" (at 0 0 0) {FONT_HIDE})',
           f'    (property "Datasheet" "" (at 0 0 0) {FONT_HIDE})',
           f'    (property "ki_description" "{desc}" (at 0 0 0) {FONT_HIDE})',
           f'    (symbol "{name}_0_1"',
           f'      (rectangle (start {-half:.2f} {body_top:.2f}) (end {half:.2f} {body_bot:.2f}) (stroke (width 0.254) (type default)) (fill (type background)))',
           '    )',
           f'    (symbol "{name}_1_1"']
    for side, pins in (("L", left), ("R", right)):
        for k, (num, pname, ptype) in enumerate(pins):
            y = top - k * 2.54
            if side == "L":
                x, ang = -half - 2.54, 0
            else:
                x, ang = half + 2.54, 180
            out.append(f'      (pin {ptype} line (at {x:.2f} {y:.2f} {ang}) (length 2.54) '
                       f'(name "{pname}" {FONT}) (number "{num}" {FONT}))')
    out += ['    )', '  )']
    return "\n".join(out)


def devkit_symbol():
    def typ(n):
        return {"3V3": "power_out", "5V": "power_in", "GND": "power_in"}.get(n, "bidirectional")
    names = D.DEVKIT_PINS
    left = [(i + 1, n, typ(n)) for i, n in enumerate(names[:19])]
    right = [(i + 20, n, typ(n)) for i, n in enumerate(names[19:])]
    return symbol("ESP32_DevKitC_V4", "U", "ESP32-DevKitC-32E", "Scorbot:ESP32_DevKitC_V4_Socket",
                  left, right, width=15.24,
                  desc="ESP32-DevKitC V4, 38 pin, in two 1x19 female headers. Pin 1-19 = J2, 20-38 = J3")


def carrier_symbol():
    types = {"VIN": "power_in", "GND": "power_in", "VM": "passive", "OUT1": "passive",
             "OUT2": "passive", "PMODE": "input", "IMODE": "input", "EN": "input",
             "PH": "input", "SLEEP": "input", "FAULT": "open_collector", "CS": "output",
             "VREF": "input"}
    pins = D.carrier_pins()
    n0 = len(D.CARRIER_ROWS[0])
    left = [(n, pins[n], types[pins[n]]) for n in range(1, n0 + 1)]
    right = [(n, pins[n], types[pins[n]]) for n in range(n0 + 1, len(pins) + 1)]
    return symbol("Pololu_DRV8874_Carrier", "U", "Pololu DRV8874 carrier",
                  "Scorbot:Pololu_DRV8874_Carrier_Socket", left, right, width=15.24,
                  desc="Pololu #4035 DRV8874 single brushed DC motor driver carrier. PIN ORDER IS A PLACEHOLDER")


def db50_symbol():
    names = D.db50_pin_names()
    left = [(n, names[n], "passive") for n in range(1, 26)]
    right = [(n, names[n], "passive") for n in range(26, 51)]
    return symbol("DD50_Female", "J", "DD-50 female", "Scorbot:DD50_Female_Horizontal_P2.77x2.84mm",
                  left, right, width=25.4,
                  desc="DD-50 (3-row D-sub) female, robot cable. Pin names follow the SCORBOT-ER 4u manual ch. 8")


def write_symbols():
    text = ["(kicad_symbol_lib (version 20220914) (generator scorbot_gen)",
            devkit_symbol(), carrier_symbol(), db50_symbol(), ")"]
    with open(os.path.join(LIB, "Scorbot.kicad_sym"), "w") as f:
        f.write("\n".join(text) + "\n")


# ---------------------------------------------------------------------------
# Footprints
# ---------------------------------------------------------------------------
def fp_header(name, descr, tags, ref_xy, val_xy):
    return [f'(footprint "{name}" (version 20221018) (generator scorbot_gen)',
            '  (layer "F.Cu")',
            f'  (descr "{descr}")',
            f'  (tags "{tags}")',
            '  (attr through_hole)',
            f'  (fp_text reference "REF**" (at {ref_xy[0]:.3f} {ref_xy[1]:.3f}) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))) (tstamp {u()}))',
            f'  (fp_text value "{name}" (at {val_xy[0]:.3f} {val_xy[1]:.3f}) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))) (tstamp {u()}))']


def fp_rect(x1, y1, x2, y2, layer, w):
    return f'  (fp_rect (start {x1:.3f} {y1:.3f}) (end {x2:.3f} {y2:.3f}) (stroke (width {w}) (type solid)) (fill none) (layer "{layer}") (tstamp {u()}))'


def fp_line(x1, y1, x2, y2, layer, w):
    return f'  (fp_line (start {x1:.3f} {y1:.3f}) (end {x2:.3f} {y2:.3f}) (stroke (width {w}) (type solid)) (layer "{layer}") (tstamp {u()}))'


def fp_text(txt, x, y, layer, size=1.0):
    return f'  (fp_text user "{txt}" (at {x:.3f} {y:.3f}) (layer "{layer}") (effects (font (size {size} {size}) (thickness 0.15))) (tstamp {u()}))'


def pad(num, x, y, size=1.7, drill=1.0, first=False):
    shape = "rect" if first else "circle"
    return (f'  (pad "{num}" thru_hole {shape} (at {x:.3f} {y:.3f}) (size {size} {size}) (drill {drill}) '
            f'(layers "*.Cu" "*.Mask") (tstamp {u()}))')


def write_fp(name, lines):
    with open(os.path.join(LIB, "Scorbot.pretty", name + ".kicad_mod"), "w") as f:
        f.write("\n".join(lines + [")"]) + "\n")


def devkit_fp():
    s = D.DEVKIT_ROW_SPACING
    n = 19
    span = (n - 1) * 2.54
    name = "ESP32_DevKitC_V4_Socket"
    L = fp_header(name, "Two 1x19 female 2.54mm headers for an ESP32-DevKitC V4 (38-pin). Row spacing must match your DevKit.",
                  "ESP32 DevKitC socket", (s / 2, -9), (s / 2, span + 5))
    for k in range(n):
        L.append(pad(k + 1, 0, k * 2.54, first=(k == 0)))
        L.append(pad(k + 20, s, k * 2.54))
    # socket strips
    L.append(fp_rect(-1.27, -1.27, 1.27, span + 1.27, "F.SilkS", 0.12))
    L.append(fp_rect(s - 1.27, -1.27, s + 1.27, span + 1.27, "F.SilkS", 0.12))
    # DevKit body (approx. 54.4 x 27.9 mm), antenna at the pin-1 end
    bx1, bx2 = s / 2 - 13.95, s / 2 + 13.95
    by1, by2 = -6.2, span + 2.5
    L.append(fp_rect(bx1, by1, bx2, by2, "F.Fab", 0.1))
    L.append(fp_line(bx1, -1.6, bx2, -1.6, "F.Fab", 0.1))
    L.append(fp_text("ANTENNA - past board edge", s / 2, -3.9, "F.Fab", 0.8))
    L.append(fp_text("USB", s / 2, span + 1.2, "F.Fab", 0.8))
    L.append(fp_text("3V3", -2.8, 0, "F.SilkS", 0.8))
    L.append(fp_text("5V", -2.6, span, "F.SilkS", 0.8))
    L.append(fp_rect(bx1 - 0.25, by1 - 0.25, bx2 + 0.25, by2 + 0.25, "F.CrtYd", 0.05))
    write_fp(name, L)


def carrier_fp():
    name = "Pololu_DRV8874_Carrier_Socket"
    rows = D.CARRIER_ROWS
    s = D.CARRIER_ROW_SPACING
    longest = max(len(r) for r in rows)
    L = fp_header(name, "PLACEHOLDER pin order: socket for Pololu #4035 DRV8874 carrier. Verify against the carrier pinout.",
                  "Pololu DRV8874 carrier socket", (s / 2, -3.5), (s / 2, longest * 2.54 + 2))
    n = 1
    for c, row in enumerate(rows):
        for k, pname in enumerate(row):
            L.append(pad(n, c * s, k * 2.54, first=(n == 1)))
            L.append(fp_text(pname, c * s + (2.6 if c == 0 else -2.6), k * 2.54, "F.Fab", 0.6))
            n += 1
    x1, x2 = -1.27 - 1.27, s + 2.54
    y1, y2 = -2.54, (longest - 1) * 2.54 + 2.54
    L.append(fp_rect(x1, y1, x2, y2, "F.SilkS", 0.12))
    L.append(fp_text("PINOUT UNVERIFIED", s / 2, (longest - 1) * 2.54 / 2, "F.SilkS", 0.8))
    L.append(fp_rect(x1 - 0.25, y1 - 0.25, x2 + 0.25, y2 + 0.25, "F.CrtYd", 0.05))
    write_fp(name, L)


def db50_fp():
    """3-row DD-50 female, right angle. Pin 1 at origin, row 1 runs -X like the
    KiCad DSUB-xx_Female_Horizontal_P2.77x2.84mm footprints; mating face +Y."""
    name = "DD50_Female_Horizontal_P2.77x2.84mm"
    p, r = 2.77, 2.84
    edge = 9.4 + 2 * r  # PCB edge (connector flange) relative to row 1
    L = fp_header(name, "DD-50 (3-row D-sub, 50 pin) female, right angle, THT, pitch 2.77x2.84mm. "
                  "Check against your connector's drawing (row offset to edge, holes).",
                  "DD50 DB50 D-sub 50 female horizontal", (-22.16, -3.5), (-22.16, edge + 8))
    for k in range(17):
        L.append(pad(k + 1, -k * p, 0, size=1.6, first=(k == 0)))
    for k in range(16):
        L.append(pad(k + 18, -p / 2 - k * p, r, size=1.6))
    for k in range(17):
        L.append(pad(k + 34, -k * p, 2 * r, size=1.6))
    cx = -8 * p
    half_b = 61.11 / 2  # D-shell mounting hole spacing
    for hx in (cx - half_b, cx + half_b):
        L.append(f'  (pad "" thru_hole circle (at {hx:.3f} {r:.3f}) (size 4 4) (drill 3.2) (layers "*.Cu" "*.Mask") (tstamp {u()}))')
    fw = 66.93 / 2
    L.append(fp_rect(cx - fw, -1.8, cx + fw, edge, "F.Fab", 0.1))
    L.append(fp_line(cx - fw, edge, cx + fw, edge, "Dwgs.User", 0.1))
    L.append(fp_text("PCB EDGE", cx, edge - 1, "Dwgs.User", 0.8))
    L.append(fp_rect(cx - fw, -2.0, cx + fw, edge + 1.0, "F.SilkS", 0.12))
    L.append(fp_rect(cx - fw - 0.5, -2.5, cx + fw + 0.5, edge + 9.0, "F.CrtYd", 0.05))
    write_fp(name, L)


def main():
    os.makedirs(os.path.join(LIB, "Scorbot.pretty"), exist_ok=True)
    write_symbols()
    devkit_fp()
    carrier_fp()
    db50_fp()
    print("library written to", os.path.normpath(LIB))


if __name__ == "__main__":
    main()
