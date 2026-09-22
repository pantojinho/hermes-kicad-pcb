#!/usr/bin/env python3
"""CI gate for esp32_example.py output (same rules on Linux and Windows).

Must hold:  DRC 0 errors / 0 unconnected / 0 schematic-parity items.
Allowed, documented findings (anything else fails the job):
  * DRC hole_clearance inside J1: the GCT USB4105 library footprint puts its NPTH
    pegs 0.194 mm from its own GND pads (maker's land pattern) — fab must confirm.
  * ERC on U1 pin 3 (EN): left open on purpose, a real board needs an EN RC.

Usage: python example_gate.py OUT_DIR
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/kicad-pcb/scripts"))
import kicad_paths as kp  # noqa: E402

out = Path(sys.argv[1])
fail = []

drc = json.loads((out / "esp32-drc.json").read_text(encoding="utf-8"))
for v in drc.get("violations", []):
    refs = " ".join(i.get("description", "") for i in v.get("items", []))
    if v.get("severity") == "error":
        fail.append(f"DRC error {v['type']}: {refs[:120]}")
    elif not (v["type"] == "hole_clearance" and "J1" in refs):
        fail.append(f"DRC warning {v['type']}: {refs[:120]}")
for key in ("unconnected_items", "schematic_parity"):
    if drc.get(key):
        fail.append(f"DRC {key}: {len(drc[key])}")

erc_file = out / "esp32-erc.json"
subprocess.run([str(kp.kicad_cli()), "sch", "erc", "--format", "json", "-o", str(erc_file),
                str(out / "esp32-devboard.kicad_sch")], capture_output=True, timeout=300)
erc = json.loads(erc_file.read_text(encoding="utf-8"))
for sheet in erc.get("sheets", []):
    for v in sheet.get("violations", []):
        items = " ".join(i.get("description", "") for i in v.get("items", []))
        if not ("U1" in items and ("[EN" in items or " 3 " in items)):
            fail.append(f"ERC {v['type']}: {items[:120]}")

print(f"DRC {len(drc.get('violations', []))} violation(s) | ERC checked | "
      f"{'FAIL' if fail else 'PASS'}")
for f in fail:
    print("  -", f)
sys.exit(1 if fail else 0)
