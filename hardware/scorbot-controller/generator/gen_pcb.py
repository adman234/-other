"""Generate scorbot_controller.kicad_pcb from design.py (placement only, unrouted).

Run with the Python that ships KiCad's pcbnew module (KiCad 7+).
Edge-critical parts (power jacks, E-stop terminal, DB50, DevKit, driver
sockets) are placed explicitly; everything else is shelf-packed into the
zone of its functional block, following the layout mock-up.
"""
import os
import uuid

import pcbnew

import design as D

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.normpath(os.path.join(HERE, ".."))
STD_FP = "/usr/share/kicad/footprints"
NS = uuid.UUID("5c0b0a7e-4e5d-4c6a-9f00-5c0b0a7e4e5d")  # same namespace as gen_schematic.py
OX, OY = 30.0, 30.0  # board origin on the page (mm)
W, H = D.BOARD_W, D.BOARD_H
MM = pcbnew.FromMM


def symbol_uuid(ref, unit):
    return str(uuid.uuid5(NS, f"sym:{ref}:{unit}"))


def pt(x, y):
    return pcbnew.VECTOR2I(MM(OX + x), MM(OY + y))


def load_fp(fpid):
    nick, name = fpid.split(":")
    path = os.path.join(PROJ, "lib", "Scorbot.pretty") if nick == "Scorbot" else os.path.join(STD_FP, nick + ".pretty")
    fp = pcbnew.FootprintLoad(path, name)
    if fp is None:
        raise SystemExit(f"footprint not found: {fpid}")
    fp.SetFPID(pcbnew.LIB_ID(nick, name))
    return fp


def bbox(fp):
    """Courtyard-ish bounding box in board mm (relative to board origin)."""
    b = fp.GetBoundingBox(False, False)
    return (pcbnew.ToMM(b.GetLeft()) - OX, pcbnew.ToMM(b.GetTop()) - OY,
            pcbnew.ToMM(b.GetRight()) - OX, pcbnew.ToMM(b.GetBottom()) - OY)


def move_bbox_to(fp, left=None, top=None, right=None, bottom=None):
    l, t, r, b = bbox(fp)
    dx = (left - l) if left is not None else (right - r) if right is not None else 0
    dy = (top - t) if top is not None else (bottom - b) if bottom is not None else 0
    fp.Move(pcbnew.VECTOR2I(MM(dx), MM(dy)))


def shelf_pack(fps, x0, y0, x1, y1, gap=1.2):
    x, y, row_h = x0, y0, 0
    for fp in fps:
        l, t, r, b = bbox(fp)
        w, h = r - l, b - t
        if x + w > x1 and x > x0:
            x, y, row_h = x0, y + row_h + gap, 0
        move_bbox_to(fp, left=x, top=y)
        x += w + gap
        row_h = max(row_h, h)
    if y + row_h > y1:
        print(f"warning: zone ({x0},{y0})-({x1},{y1}) overflows by {y + row_h - y1:.1f} mm")


def build():
    board = pcbnew.CreateEmptyBoard() if hasattr(pcbnew, "CreateEmptyBoard") else pcbnew.BOARD()
    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(2)
    ds.m_TrackMinWidth = MM(0.2)
    ds.m_MinClearance = MM(0.2)

    nets = {}
    for p in D.PARTS:
        for n in p["nets"].values():
            if n and n not in nets:
                ni = pcbnew.NETINFO_ITEM(board, n)
                board.Add(ni)
                nets[n] = ni

    fps = {}
    for p in D.PARTS:
        if not p["fp"]:
            continue  # power flags
        fp = load_fp(p["fp"])
        fp.SetReference(p["ref"])
        fp.SetValue(p["value"])
        fp.SetPath(pcbnew.KIID_PATH("/" + symbol_uuid(p["ref"], 1)))
        if p.get("dnp"):
            fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_EXCLUDE_FROM_BOM)
        for pad in fp.Pads():
            n = p["nets"].get(pad.GetNumber())
            if n:
                pad.SetNet(nets[n])
        x, y, rot = p["pcb"] if p["pcb"] else (W / 2, H / 2, 0)
        fp.SetOrientationDegrees(rot)
        fp.SetPosition(pt(x, y))
        board.Add(fp)
        fps[p["ref"]] = fp

    # ---- explicit, edge-critical placements ----------------------------------
    # Power entry on the left edge, connectors opening to the left.
    fps["J1"].SetOrientationDegrees(0)  # KiCad's barrel jack opens toward -X
    move_bbox_to(fps["J1"], left=-2.0, top=10)
    fps["J2"].SetOrientationDegrees(270)
    move_bbox_to(fps["J2"], left=-1.0, top=bbox(fps["J1"])[3] + 3)
    fps["J3"].SetOrientationDegrees(270)
    move_bbox_to(fps["J3"], left=-1.0, top=58)
    # DB50 on the right edge, mating face out.
    fps["J4"].SetOrientationDegrees(90)
    l, t, r, b = bbox(fps["J4"])
    # the footprint's "PCB EDGE" line sits 9 mm inside its courtyard's far edge
    move_bbox_to(fps["J4"], right=W + 9.0, top=44)
    # DevKit along the bottom edge, antenna past the left edge.
    fps["U2"].SetOrientationDegrees(90)
    # courtyard starts 6.45 mm before pin 1: leave pin 1 2 mm inside the edge,
    # so the antenna end of the module hangs past the board.
    move_bbox_to(fps["U2"], left=-4.45, bottom=H - 1.5)
    # Driver sockets along the top.
    for i in range(1, 7):
        fp = fps[f"U{9 + i}"]
        fp.SetOrientationDegrees(0)
        move_bbox_to(fp, left=58 + (i - 1) * 21.0, top=3)
        cl, ct, cr, cb = bbox(fp)
        move_bbox_to(fps[f"C{20 + i}"], left=cl + 1, top=cb + 1.5)
        move_bbox_to(fps[f"R{20 + i}"], left=cl + 10, top=cb + 2.0)
    # Mounting holes in the corners.
    for ref, (x, y) in zip(("H1", "H2", "H3", "H4"), ((4, 4), (W - 4, 37), (W - 4, H - 4), (62, H - 4))):
        fps[ref].SetPosition(pt(x, y))

    # ---- zones for everything else ---------------------------------------------
    def refs(group, exclude=()):
        return [fps[p["ref"]] for p in D.PARTS
                if p["group"] == group and p["ref"] in fps and p["ref"] not in exclude]

    explicit = {"J1", "J2", "J3", "J4", "U2", "H1", "H2", "H3", "H4"} | {f"U{9 + i}" for i in range(1, 7)} \
        | {f"C{20 + i}" for i in range(1, 7)} | {f"R{20 + i}" for i in range(1, 7)}
    for fp in fps.values():
        fp.SetOrientationDegrees(fp.GetOrientationDegrees())
    shelf_pack(refs("power", explicit), 14, 22, 56, 60)
    shelf_pack(refs("buck", explicit), 14, 62, 56, 88)
    shelf_pack(refs("i2c", explicit), 60, 48, 118, 88)
    shelf_pack(refs("mcu", explicit), 66, 100, 118, 124)
    shelf_pack(refs("encoders", explicit), 120, 48, 166, 108)
    shelf_pack(refs("switches", explicit), 120, 110, 166, 126)

    # ---- outline, text, ground pour ------------------------------------------------
    corners = [(0, 0), (W, 0), (W, H), (0, H)]
    for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetStart(pt(x1, y1))
        seg.SetEnd(pt(x2, y2))
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetWidth(MM(0.1))
        board.Add(seg)
    for txt, x, y, size in (("SCORBOT ER-4u ESP32 MASTER BOARD v0.1", 92, 91, 1.5),
                            ("UNROUTED - placement only. Carrier socket pinout UNVERIFIED.", 92, 94.5, 1.0)):
        t = pcbnew.PCB_TEXT(board)
        t.SetText(txt)
        t.SetPosition(pt(x, y))
        t.SetLayer(pcbnew.F_SilkS)
        t.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size)))
        t.SetTextThickness(MM(size * 0.15))
        board.Add(t)

    zone = pcbnew.ZONE(board)
    zone.SetLayer(pcbnew.B_Cu)
    zone.SetNet(nets["GND"])
    zone.SetZoneName("GND")
    zone.SetLocalClearance(MM(0.3))
    zone.SetMinThickness(MM(0.25))
    poly = zone.Outline()
    poly.NewOutline()
    for x, y in ((0.5, 0.5), (W - 0.5, 0.5), (W - 0.5, H - 0.5), (0.5, H - 0.5)):
        poly.Append(MM(OX + x), MM(OY + y))
    board.Add(zone)

    # No copper pour near the DevKit antenna end.
    ul, ut, ur, ub = bbox(fps["U2"])
    keep = pcbnew.ZONE(board)
    keep.SetIsRuleArea(True)
    keep.SetDoNotAllowCopperPour(True)
    keep.SetDoNotAllowTracks(True)
    keep.SetDoNotAllowVias(True)
    keep.SetDoNotAllowPads(False)
    keep.SetDoNotAllowFootprints(False)
    cu = pcbnew.LSET()
    cu.AddLayer(pcbnew.F_Cu)
    cu.AddLayer(pcbnew.B_Cu)
    keep.SetLayerSet(cu)
    kp = keep.Outline()
    kp.NewOutline()
    for x, y in ((0, ut), (1.5, ut), (1.5, ub), (0, ub)):
        kp.Append(MM(OX + x), MM(OY + y))
    keep.SetZoneName("ANTENNA_KEEPOUT")
    board.Add(keep)

    path = os.path.join(PROJ, "scorbot_controller.kicad_pcb")
    board.Save(path)
    # Fill the pour on a freshly loaded board (filling the in-memory board segfaults headless).
    board = pcbnew.LoadBoard(path)
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(path)
    return path


if __name__ == "__main__":
    print("wrote", build())
