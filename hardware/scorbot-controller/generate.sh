#!/usr/bin/env bash
# Regenerate the whole KiCad project from generator/design.py and check it.
# Needs KiCad 7+ (kicad-cli + the pcbnew Python module) and `pip install kiutils`.
set -euo pipefail
cd "$(dirname "$0")/generator"
python3 gen_library.py
python3 gen_schematic.py
python3 gen_pcb.py
python3 gen_bom.py
tmp=$(mktemp -d)
kicad-cli sch export netlist --format kicadxml -o "$tmp/net.xml" ../scorbot_controller.kicad_sch >/dev/null
python3 check_netlist.py "$tmp/net.xml"
python3 run_drc.py "$tmp/drc.txt"
mkdir -p ../docs
kicad-cli sch export pdf -o ../docs/schematic.pdf ../scorbot_controller.kicad_sch >/dev/null
kicad-cli pcb export svg --layers F.Cu,F.SilkS,F.Fab,F.CrtYd,Edge.Cuts,Dwgs.User --page-size-mode 2 \
  --exclude-drawing-sheet -o "$tmp/pcb.svg" ../scorbot_controller.kicad_pcb >/dev/null
python3 -c "import cairosvg, sys; cairosvg.svg2png(url=sys.argv[1], write_to='../docs/pcb_placement.png', output_width=2000, background_color='white')" "$tmp/pcb.svg" \
  || echo "(pip install cairosvg for the PNG preview)"
echo "done"
