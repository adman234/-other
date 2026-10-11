"""Write JLCPCB fabrication + assembly files into ../fab/.

    gerbers/ + scorbot_controller_gerbers.zip   2-layer Gerbers and drill files
    jlc_bom.csv                                 Comment, Designator, Footprint, LCSC Part #
    jlc_cpl.csv                                 Designator, Mid X, Mid Y, Layer, Rotation

Only SMD parts on the top side go into the JLC BOM/CPL; through-hole parts,
sockets and DNP resistors are hand-soldered or left empty.

LCSC numbers below are from memory/search snapshots (JLCPCB's site was not
reachable when this was written): check each one in JLCPCB's parts library
before ordering, and check part rotations in JLCPCB's placement preview.
"""
import csv
import os
import shutil
import subprocess
import tempfile
import zipfile

import design as D

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.normpath(os.path.join(HERE, ".."))
PCB = os.path.join(PROJ, "scorbot_controller.kicad_pcb")
FAB = os.path.join(PROJ, "fab")

# (value prefix, footprint) -> LCSC part (JLC Basic unless noted)
LCSC = {
    ("10k", D.R0805): "C17414",
    ("4.7k", D.R0805): "C17673",
    ("1k", D.R0805): "C17513",
    ("22k", D.R0805): "C17560",
    ("47k", D.R0805): "C17713",
    ("10k", D.R1206): "C17902",
    ("47R", D.R1206): "C17714",
    ("100nF", D.C0805): "C49678",
    ("1nF", D.C0805): "C46653",
    ("VM on (green)", D.LED0805): "C2297",
    ("5V (green)", D.LED0805): "C2297",
    ("3V3 (green)", D.LED0805): "C2297",
    ("STATUS (blue)", D.LED0805): "C84256",
    ("SS14", "Diode_SMD:D_SMA"): "C2480",
    ("SMBJ18A", "Diode_SMD:D_SMB"): "C168805",            # Extended
    ("TCA9555PWR", "Package_SO:TSSOP-24_4.4x7.8mm_P0.65mm"): "C465732",  # Extended
}


def lcsc(p):
    for (val, fp), part in LCSC.items():
        if p["fp"] == fp and p["value"].startswith(val):
            return part
    return ""


def smd_parts():
    out = []
    for p in D.PARTS:
        fp = p["fp"] or ""
        if p.get("dnp") or not fp or p["ref"].startswith(("#", "H")):
            continue
        if fp.split(":")[0] in ("Resistor_SMD", "Capacitor_SMD", "LED_SMD", "Diode_SMD", "Package_SO"):
            out.append(p)
    return out


def main():
    os.makedirs(FAB, exist_ok=True)
    gdir = os.path.join(FAB, "gerbers")
    shutil.rmtree(gdir, ignore_errors=True)
    os.makedirs(gdir)
    layers = "F.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"
    subprocess.run(["kicad-cli", "pcb", "export", "gerbers", "--layers", layers, "--subtract-soldermask",
                    "-o", gdir + "/", PCB], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["kicad-cli", "pcb", "export", "drill", "--format", "excellon", "--excellon-separate-th",
                    "--generate-map", "--map-format", "gerberx2", "-o", gdir + "/", PCB],
                   check=True, stdout=subprocess.DEVNULL)
    zpath = os.path.join(FAB, "scorbot_controller_gerbers.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(gdir)):
            z.write(os.path.join(gdir, f), f)

    parts = smd_parts()
    groups = {}
    for p in parts:
        key = (p["value"], p["fp"].split(":")[1], lcsc(p))
        groups.setdefault(key, []).append(p["ref"])
    missing = [refs for (v, fp, l), refs in groups.items() if not l]
    with open(os.path.join(FAB, "jlc_bom.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (value, fp, part), refs in groups.items():
            w.writerow([value, ",".join(refs), fp, part])

    # placement: KiCad's position file, renamed to JLC's columns
    tmp = tempfile.mkdtemp()
    pos = os.path.join(tmp, "pos.csv")
    subprocess.run(["kicad-cli", "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "front",
                    "--smd-only", "-o", pos, PCB], check=True, stdout=subprocess.DEVNULL)
    wanted = {p["ref"] for p in parts}
    with open(pos) as f, open(os.path.join(FAB, "jlc_cpl.csv"), "w", newline="") as g:
        w = csv.writer(g)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        n = 0
        for row in csv.DictReader(f):
            if row["Ref"] in wanted:
                w.writerow([row["Ref"], row["PosX"] + "mm", row["PosY"] + "mm", "Top", row["Rot"]])
                n += 1
    print(f"fab: gerbers -> {os.path.relpath(zpath, PROJ)}, {len(parts)} SMD parts in {len(groups)} BOM lines,"
          f" {n} placements" + (f"; NO LCSC number for {missing}" if missing else ""))


if __name__ == "__main__":
    main()
