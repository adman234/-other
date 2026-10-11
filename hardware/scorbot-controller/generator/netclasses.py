"""Write the board's net classes into scorbot_controller.kicad_pro.

pcbnew's Board.Save() rewrites the project file with only the Default class,
so gen_pcb.py and gen_route.py call this after every save. Keep in step with
CLASSES in gen_route.py.
"""
import json
import os

PRO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scorbot_controller.kicad_pro"))

# +3V3 and ENC*_VLED (about 80 mA) stay Default: +3V3 has to leave 0.65 mm-pitch
# pins, and the VLED lines fan out through the crowded DB50 pin field.
# GND is routed at 0.4 mm (it must leave 0.65 mm-pitch pins) and poured on both layers.
CLASSES = {  # name: (track mm, clearance mm, via dia, via drill, patterns)
    "Power": (1.5, 0.3, 1.0, 0.5, ["VIN_RAW", "VIN", "VM"]),
    "Motor": (0.8, 0.3, 1.0, 0.5, ["MOT*"]),
    "Ground": (0.4, 0.2, 0.8, 0.4, ["GND"]),
    "Supply": (0.5, 0.25, 0.8, 0.4, ["+5V", "+5V_DK", "VREF*"]),
}


def write():
    with open(PRO) as f:
        p = json.load(f)
    ns = p.setdefault("net_settings", {})
    default = next(c for c in ns["classes"] if c["name"] == "Default")
    default.update(track_width=0.25, clearance=0.2, via_diameter=0.8, via_drill=0.4)
    ns["classes"] = [default]
    for name, (w, clr, vd, vdr, _) in CLASSES.items():
        c = dict(default)
        c.update(name=name, track_width=w, clearance=clr, via_diameter=vd, via_drill=vdr)
        ns["classes"].append(c)
    ns["netclass_patterns"] = [{"netclass": n, "pattern": pat} for n, c in CLASSES.items() for pat in c[4]]
    with open(PRO, "w") as f:
        json.dump(p, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    write()
