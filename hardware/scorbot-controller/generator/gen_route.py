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

import netclasses

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


EDGE_MARGIN = 500  # um: keep routed copper this far inside the board edge


def rewrite_dsn(text):
    # shrink the routing boundary so tracks keep off the board edge
    def shrink(m):
        xy = [int(v) for v in m.group(2).split()]
        xs, ys = xy[0::2], xy[1::2]
        x0, x1, y0, y1 = min(xs) + EDGE_MARGIN, max(xs) - EDGE_MARGIN, min(ys) + EDGE_MARGIN, max(ys) - EDGE_MARGIN
        return f"{m.group(1)}{x1} {y0}  {x0} {y0}  {x0} {y1}  {x1} {y1}  {x1} {y0})"
    text = re.sub(r'(\(boundary\s*\(path pcb 0\s+)([-\d\s]+)\)', shrink, text, count=1)
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


def _sexp(text):
    tokens = re.findall(r'\(|\)|"[^"]*"|[^\s()]+', text)
    stack = [[]]
    for t in tokens:
        if t == "(":
            stack.append([])
        elif t == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(t.strip('"'))
    return stack[0][0]


def _find(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def import_ses(board, ses_path):
    """Add the session's wires and vias to the board.

    pcbnew.ImportSpecctraSES only works inside the KiCad GUI in KiCad 7, so
    this reads the Specctra session directly (units: um / resolution)."""
    ses = _sexp(open(ses_path).read())
    routes = _find(ses, "routes")[0]
    res = float(_find(routes, "resolution")[0][2])
    to_nm = 1000.0 / res  # session unit -> nm
    layers = {"F.Cu": pcbnew.F_Cu, "B.Cu": pcbnew.B_Cu}
    count = [0, 0]
    for net in _find(_find(routes, "network_out")[0], "net"):
        ni = board.FindNet(net[1])
        for wire in _find(net, "wire"):
            for path in _find(wire, "path"):
                layer, width = layers[path[1]], int(float(path[2]) * to_nm)
                xy = [float(v) for v in path[3:] if not isinstance(v, list)]
                pts = [pcbnew.VECTOR2I(int(xy[i] * to_nm), int(-xy[i + 1] * to_nm)) for i in range(0, len(xy), 2)]
                for a, b in zip(pts, pts[1:]):
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(a)
                    t.SetEnd(b)
                    t.SetWidth(width)
                    t.SetLayer(layer)
                    t.SetNet(ni)
                    board.Add(t)
                    count[0] += 1
        for via in _find(net, "via"):
            d = VIAS.get(via[1], 800)
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pcbnew.VECTOR2I(int(float(via[2]) * to_nm), int(-float(via[3]) * to_nm)))
            v.SetWidth(int(d * 1000))
            v.SetDrill(int(d * 500))
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetNet(ni)
            board.Add(v)
            count[1] += 1
    return count


def strip_routing(board):
    """Remove earlier tracks, vias and the top GND pour (re-route from scratch)."""
    for t in list(board.GetTracks()):
        board.Remove(t)
    for z in [z for z in board.Zones() if z.GetZoneName() == "GND_TOP"]:
        board.Remove(z)


def add_top_ground(path):
    """GND pour on F.Cu as well, added after routing (Freerouting only knows the
    B.Cu plane). Stitches GND pads that the B.Cu pour leaves as islands."""
    board = pcbnew.LoadBoard(path)
    gnd = board.FindNet("GND")
    src = next(z for z in board.Zones() if not z.GetIsRuleArea() and z.GetLayer() == pcbnew.B_Cu)
    z = pcbnew.ZONE(board)
    z.SetLayer(pcbnew.F_Cu)
    z.SetNet(gnd)
    z.SetZoneName("GND_TOP")
    z.SetLocalClearance(src.GetLocalClearance())
    z.SetMinThickness(src.GetMinThickness())
    poly = z.Outline()
    poly.NewOutline()
    o = src.Outline().Outline(0)
    for i in range(o.PointCount()):
        poly.Append(o.CPoint(i).x, o.CPoint(i).y)
    board.Add(z)
    board.Save(path)
    board = pcbnew.LoadBoard(path)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(path)
    netclasses.write()


def main():
    jar = os.environ.get("FREEROUTING_JAR")
    if not jar:
        sys.exit("set FREEROUTING_JAR to freerouting-1.9.0.jar")
    passes = os.environ.get("FREEROUTING_PASSES", "40")
    tmp = tempfile.mkdtemp()
    dsn, ses = os.path.join(tmp, "board.dsn"), os.path.join(tmp, "board.ses")
    board = pcbnew.LoadBoard(PCB)
    strip_routing(board)
    board.Save(PCB)
    board = pcbnew.LoadBoard(PCB)
    if not pcbnew.ExportSpecctraDSN(board, dsn):
        sys.exit("DSN export failed")
    with open(dsn) as f:
        text = rewrite_dsn(f.read())
    with open(dsn, "w") as f:
        f.write(text)
    subprocess.run(["xvfb-run", "-a", "java", "-jar", jar, "-de", dsn, "-do", ses, "-mp", passes],
                   check=True, cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.exists(ses):
        sys.exit("Freerouting produced no session")
    board = pcbnew.LoadBoard(PCB)
    tracks, vias = import_ses(board, ses)
    print(f"imported {tracks} track segments, {vias} vias")
    board.Save(PCB)
    board = pcbnew.LoadBoard(PCB)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(PCB)
    netclasses.write()
    add_top_ground(PCB)
    print("routed", PCB, "tracks:", len(board.GetTracks()))


if __name__ == "__main__":
    main()
