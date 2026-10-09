"""Write bom.csv (grouped by value + footprint) from design.py."""
import csv
import os
from collections import OrderedDict

import design as D

HERE = os.path.dirname(os.path.abspath(__file__))
rows = OrderedDict()
for p in D.PARTS:
    if not p["fp"] or p["ref"].startswith("#"):
        continue
    key = (p["value"], p["fp"], p.get("dnp", False))
    rows.setdefault(key, []).append(p["ref"])
path = os.path.join(HERE, "..", "bom.csv")
with open(path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Qty", "Value", "Footprint", "References", "Populate"])
    for (value, fp, dnp), refs in rows.items():
        w.writerow([len(refs), value, fp, " ".join(refs), "DNP" if dnp else "yes"])
print("wrote", os.path.normpath(path), len(rows), "lines")
