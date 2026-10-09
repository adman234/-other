"""Run KiCad DRC on the generated board and summarise (unrouted nets expected)."""
import collections
import os
import re
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
pcb = os.path.join(HERE, "..", "scorbot_controller.kicad_pcb")
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "drc_report.txt")
board = pcbnew.LoadBoard(pcb)
pcbnew.WriteDRCReport(board, out, pcbnew.EDA_UNITS_MILLIMETRES, True)
text = open(out).read()
kinds = collections.Counter(re.findall(r"^\[(\w+)\]", text, re.M))
print(dict(kinds))
