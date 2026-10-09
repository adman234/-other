"""Compare KiCad's exported netlist (kicadxml) with design.py, pin by pin."""
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
import design as D

tree = ET.parse(sys.argv[1])
got = {}
for net in tree.getroot().iter("net"):
    name = net.get("name").lstrip("/")
    for node in net.iter("node"):
        got[(node.get("ref"), node.get("pin"))] = name
want = {}
for p in D.PARTS:
    for pin, n in p["nets"].items():
        if n and not p["ref"].startswith("#"):
            want[(p["ref"], pin)] = n
bad = 0
for k, n in sorted(want.items()):
    if got.get(k) != n:
        bad += 1
        print("MISMATCH", k, "want", n, "got", got.get(k))
for k, n in sorted(got.items()):
    if k not in want and not n.startswith("unconnected-"):
        bad += 1
        print("EXTRA", k, n)
print(f"checked {len(want)} connections, {bad} problems")
sys.exit(1 if bad else 0)
