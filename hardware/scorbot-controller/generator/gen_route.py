"""Autoroute scorbot_controller.kicad_pcb with Freerouting.

    FREEROUTING_JAR=/path/freerouting-1.9.0.jar python3 gen_route.py

Exports a Specctra DSN, rewrites its net classes (KiCad's Python board loader
ignores the project's netclass patterns), runs Freerouting headless under
xvfb-run, imports the session, refills the GND pours and saves.
GND is a pour on both layers; Freerouting drops GND vias into the B.Cu plane.
"""
import os
import re
import subprocess
import sys
import tempfile

import pcbnew

import design as D

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.normpath(os.path.join(HERE, "..", "scorbot_controller.kicad_pcb"))

# (name, width um, clearance um, via, net matcher) -- keep in step with the
# netclasses in scorbot_controller.kicad_pro
CLASSES = [
    ("Power", 1500, 300, "Via_1000:500", lambda n: n in ("VIN_RAW", "VIN", "VM")),
    ("Motor", 800, 300, "Via_1000:500", lambda n: n.startswith("MOT")),
    ("Supply", 500, 250, "Via_800:400", lambda n: n in ("+5V", "+3V3", "+5V_DK") or n.endswith("_VLED")),
]
DEFAULT = (250, 200, "Via_800:400")
VIAS = {"Via_800:400": 800, "Via_1000:500": 1000}


def rewrite_dsn(text):
    # via padstacks
    stacks = "".join(f'    (padstack "{name}"\n      (shape (circle F.Cu {d}))\n      (shape (circle B.Cu {d}))\n'
                     f'      (attach off)\n    )\n' for name, d in VIAS.items())
    text = re.sub(r'(\(library\n)', r'\1' + stacks.replace("\\", "\\\\"), text, count=1)
    text = re.sub(r'\(via "Via\[0-1\]_600:300_um"\)', '(via "Via_800:400" "Via_1000:500")', text)
    text = re.sub(r'\(rule\n\s*\(width 200\)\n\s*\(clearance 200.1\)',
                  f'(rule\n      (width {DEFAULT[0]})\n      (clearance {DEFAULT[1]})', text, count=1)
    # replace the single kicad_default class with ours
    m = re.search(r'\(class kicad_default "" (.*?)\(circuit', text, re.S)
    nets = m.group(1).split()
    groups = {c[0]: [] for c in CLASSES}
    rest = []
    for n in nets:
        for name, *_, match in CLASSES:
            if match(n):
                groups[name].append(n)
                break
        else:
            rest.append(n)

    def cls(name, members, w, clr, via):
        return (f'    (class {name} {" ".join(members)}\n      (circuit\n        (use_via "{via}")\n      )\n'
                f'      (rule\n        (width {w})\n        (clearance {clr})\n      )\n    )\n')
    block = cls("kicad_default", ['""'] + rest, *DEFAULT)
    for name, w, clr, via, _ in CLASSES:
        block += cls(name, groups[name], w, clr, via)
    start = m.start()
    end = text.index("\n    )\n", text.index("(rule", m.end())) + len("\n    )\n")
    return text[:start] + block.lstrip() + text[end:]


def main():
    jar = os.environ.get("FREEROUTING_JAR")
    if not jar:
        sys.exit("set FREEROUTING_JAR to freerouting-1.9.0.jar")
    passes = os.environ.get("FREEROUTING_PASSES", "40")
    tmp = tempfile.mkdtemp()
    dsn, ses = os.path.join(tmp, "board.dsn"), os.path.join(tmp, "board.ses")
    board = pcbnew.LoadBoard(PCB)
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        sys.exit("DSN export failed")
    with open(dsn) as f:
        text = rewrite_dsn(f.read())
    with open(dsn, "w") as f:
        f.write(text)
    subprocess.run(["xvfb-run", "-a", "java", "-jar", jar, "-de", dsn, "-do", ses, "-mp", passes],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.exists(ses):
        sys.exit("Freerouting produced no session")
    board = pcbnew.LoadBoard(PCB)
    if not pcbnew.ImportSpecctraSES(board, ses):
        sys.exit("SES import failed")
    board.Save(PCB)
    board = pcbnew.LoadBoard(PCB)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(PCB)
    print("routed", PCB, "tracks:", len(board.GetTracks()))


if __name__ == "__main__":
    main()
