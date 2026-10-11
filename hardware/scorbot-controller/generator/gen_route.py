"""Autoroute scorbot_controller.kicad_pcb with Freerouting.

    FREEROUTING_JAR=/path/freerouting-1.9.0.jar python3 gen_route.py

Exports a Specctra DSN, rewrites its net classes (KiCad's Python board loader
ignores the project's netclass patterns), runs Freerouting headless under
xvfb-run, imports the session, refills the GND pours and saves.
GND is routed as traces (Freerouting would treat the B.Cu pour as an unbroken
plane), then poured on both layers and stitched with vias.
"""
import os
import re
import shutil
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
    ("Ground", 400, 200, "Via_800:400", lambda n: n == "GND"),
    ("Supply", 500, 250, "Via_800:400", lambda n: n in ("+5V", "+5V_DK") or n.startswith("VREF")),
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
    # GND is routed as real traces: Freerouting assumes a plane stays whole,
    # but its own tracks cut the B.Cu pour into islands.
    text = re.sub(r'\(plane GND \(polygon [^)]*\)\)\s*', '', text, count=1)
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
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)
    z.SetLocalClearance(src.GetLocalClearance())
    z.SetMinThickness(src.GetMinThickness())
    poly = z.Outline()
    poly.NewOutline()
    o = src.Outline().Outline(0)
    for i in range(o.PointCount()):
        poly.Append(o.CPoint(i).x, o.CPoint(i).y)
    board.Add(z)
    for zone in board.Zones():
        if not zone.GetIsRuleArea():
            zone.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    board.Save(path)
    board = pcbnew.LoadBoard(path)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    added = stitch(board)
    board.Save(path)
    board = pcbnew.LoadBoard(path)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pruned = prune_floating_vias(board)
    print(f"stitching vias: {added - pruned} ({pruned} floating ones removed)")
    board.Save(path)
    board = pcbnew.LoadBoard(path)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(path)
    netclasses.write()


def prune_floating_vias(board):
    """Remove stitching vias that only join pour islands to each other.

    A pour piece is anchored when a GND pad or GND track touches it; anchoring
    spreads through vias. Vias between two unanchored pieces are removed, and
    the refill then drops those islands."""
    fills = {z.GetLayer(): z.GetFilledPolysList(z.GetLayer()) for z in board.Zones()
             if z.GetNetname() == "GND" and not z.GetIsRuleArea()}

    def piece(layer, p):
        f = fills[layer]
        for i in range(f.OutlineCount()):
            if f.Contains(p, i):
                return (layer, i)
        return None

    anchored = set()
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() == "GND":
                for layer in fills:
                    if pad.IsOnLayer(layer):
                        k = piece(layer, pad.GetPosition())
                        if k:
                            anchored.add(k)
    vias = []
    for t in board.GetTracks():
        if t.GetNetname() != "GND":
            continue
        if t.GetClass() == "PCB_VIA":
            vias.append((t, [piece(l, t.GetPosition()) for l in fills]))
        else:
            for p in (t.GetStart(), t.GetEnd()):
                k = piece(t.GetLayer(), p)
                if k:
                    anchored.add(k)
    # routed GND vias (from the session) anchor both pieces they sit in
    changed = True
    while changed:
        changed = False
        for v, ks in vias:
            ks = [k for k in ks if k]
            if any(k in anchored for k in ks) and not all(k in anchored for k in ks):
                anchored.update(ks)
                changed = True
    removed = 0
    for v, ks in vias:
        ks = [k for k in ks if k]
        if not any(k in anchored for k in ks):
            board.Remove(v)
            removed += 1
    return removed


def stitch(board, pitch_mm=6.0, via_mm=0.8, drill_mm=0.4, keep_mm=0.6):
    """Drop GND vias on a grid wherever both pours have solid copper around the
    point, tying the F.Cu and B.Cu pours (and their islands) together."""
    MM = pcbnew.FromMM
    gnd = board.FindNet("GND")
    fills = {}
    for z in board.Zones():
        if z.GetNetname() == "GND" and not z.GetIsRuleArea():
            fills[z.GetLayer()] = z.GetFilledPolysList(z.GetLayer())
    r = MM(via_mm / 2 + keep_mm)
    box = board.GetBoardEdgesBoundingBox()
    n = 0
    y = box.GetTop() + MM(pitch_mm / 2)
    while y < box.GetBottom():
        x = box.GetLeft() + MM(pitch_mm / 2)
        while x < box.GetRight():
            ring = [pcbnew.VECTOR2I(x + int(r * dx), y + int(r * dy))
                    for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (.7, .7), (-.7, .7), (.7, -.7), (-.7, -.7))]
            if all(all(f.Contains(p) for p in ring) for f in fills.values()):
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pcbnew.VECTOR2I(x, y))
                v.SetWidth(MM(via_mm))
                v.SetDrill(MM(drill_mm))
                v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
                v.SetNet(gnd)
                board.Add(v)
                n += 1
            x += MM(pitch_mm)
        y += MM(pitch_mm)
    return n


def unconnected(path):
    board = pcbnew.LoadBoard(path)
    report = path + ".drc.txt"
    pcbnew.WriteDRCReport(board, report, pcbnew.EDA_UNITS_MILLIMETRES, True)
    n = len(re.findall(r"^\[unconnected_items\]", open(report).read(), re.M))
    os.remove(report)
    return n


def route_once(jar, passes, tmp):
    dsn, ses = os.path.join(tmp, "board.dsn"), os.path.join(tmp, "board.ses")
    for f in (dsn, ses):
        if os.path.exists(f):
            os.remove(f)
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
    subprocess.run(["xvfb-run", "-a", "java", "-jar", jar, "-de", dsn, "-do", ses, "-mp", passes,
                    "-oit", "100"],  # skip the optimizer: it can hang for hours on this board
                   check=True, cwd=tmp, timeout=1800, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.exists(ses):
        sys.exit("Freerouting produced no session")
    board = pcbnew.LoadBoard(PCB)
    tracks, vias = import_ses(board, ses)
    print(f"imported {tracks} track segments, {vias} vias")
    board.Save(PCB)
    netclasses.write()
    add_top_ground(PCB)
    return unconnected(PCB)


def main():
    jar = os.environ.get("FREEROUTING_JAR")
    if not jar:
        sys.exit("set FREEROUTING_JAR to freerouting-1.9.0.jar")
    passes = os.environ.get("FREEROUTING_PASSES", "100")
    tries = int(os.environ.get("FREEROUTING_TRIES", "3"))
    tmp = tempfile.mkdtemp()
    best = None
    # Freerouting is not fully deterministic: retry and keep the best result.
    for attempt in range(1, tries + 1):
        n = route_once(jar, passes, tmp)
        print(f"attempt {attempt}: {n} unconnected")
        if best is None or n < best[0]:
            best = (n, os.path.join(tmp, "best.kicad_pcb"))
            shutil.copy(PCB, best[1])
        if n == 0:
            break
    shutil.copy(best[1], PCB)
    netclasses.write()
    print(f"routed {PCB}: {best[0]} unconnected")


if __name__ == "__main__":
    main()
