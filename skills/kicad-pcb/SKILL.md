---
name: kicad-pcb
description: Use when building PCBs with KiCad headless (pcbnew/CLI).
---

# KiCad PCB automation (headless, Astra-style)

Design and manipulate PCBs without the GUI: pcbnew Python for board manipulation,
kicad-cli for DRC/ERC/export, Freerouting headless for autorouting (DSN/SES loop).

## Toolchain (verified 2026-09-21 on omarchy 4.0.4)
- KiCad 10.0.6: `kicad-cli` + pcbnew Python bindings (in /usr/lib/python3.14/site-packages)
- Freerouting 2.4.1: `~/Work/tools/freerouting-2.4.1.jar` (needs jre-openjdk — installed)
- Official libs: `/usr/share/kicad/{symbols,footprints}`
- Validated reference implementation: `scripts/demo_autoroute.py`

## CRITICAL pitfalls (learned the hard way)
1. pcbnew import MUST use `/usr/bin/python` (system 3.14), never the Hermes venv python. Import prints 2 harmless PROPERTY_ENUM asserts on stderr — ignore them.
2. Python 3.14 compat patch was applied to `/usr/lib/python3.14/site-packages/pcbnew.py` (3× `item = it.next()` → `item = it.__next__() if hasattr(it, '__next__') else it.next()`). If a kicad update overwrites the file and iteration raises `'SwigPyIterator' object has no attribute 'next'`, RE-APPLY the patch — without it, `len(board.GetTracks())` / `list(board.GetFootprints())` crash.
3. `pcbnew.NewBoard(path)` requires the filename arg (KiCad 10).
4. Board outline on Edge.Cuts is REQUIRED before DRC and autorouting. Use `pcbnew.SHAPE_T_RECT` (uppercase) + `SHAPE_T_SEGMENT` etc. Coordinates: `pcbnew.VECTOR2I` in nanometers; `mm = pcbnew.FromMM`.
5. `kicad-cli` 10.0.6 does NOT export DSN or import SES — use `pcbnew.ExportSpecctraDSN(board, path)` / `pcbnew.ImportSpecctraSES(board, path)` (both exist and work).
6. solder_mask_bridge DRC warnings on tight small boards are cosmetic mask apertures, not shorts — real connectivity checks are `unconnected_items` + `schematic_parity`.
7. Multi-pad numbers: footprints like ESP32-WROOM-32 have SEVERAL sub-pads sharing one number (thermal pad 39). `FindPadByNumber` returns one — iterate `fp.Pads()` and set the net on ALL pads with that number, else same-number pads short (GND vs no-net).
8. Symbol pin names ≠ pad names: map via the symbol's pin number (`pins_of`); names come composed ("RXD0/IO3") and derived symbols (`extends`) have NO own pins — resolve the parent and rename nested unit blocks "Parent_0_1" -> "Child_0_1" when embedding in lib_symbols.
9. Footprint anchor is often pad 1, NOT the center (PinHeader_1x06!). Placement math MUST use real bounding boxes: `fab_bbox` = union of pads + F.Fab graphics. NEVER use `GetBoundingBox()` for collision — it includes text and the ESP32 antenna keepout zone (48mm wide!) and destroys any packer.
10. ESP32/RF modules: keep the antenna keepout ENTIRELY off the board edge (place by the keepout zone bbox: zone bottom at -0.5mm). Copper under the antenna raises items_not_allowed and kills RF.
11. Design rules (min track width, min drill) live in the .kicad_pro, NOT the board: with no project file, DRC uses defaults (0.2mm) and Freerouting's 0.15mm tracks/drls fail. Write a .kicad_pro with rules (min_track_width 0.127, min_through_hole_diameter 0.2) before DRC.
12. Auto-placement with pad+F.Fab bbox packing + 1.0mm gap gives collision-free layouts and lets Freerouting reach 0 unrouted / 0 DRC violations on a 13-part 2-layer board (validated: esp32-dev, 177 tracks).
13. Schematic (.kicad_sch) generation: format version 20260101 works on KiCad 10.0.6; labels at pin-tip coordinates provide connectivity (pin `at` IS the connection point). Wire-by-label ERC reports pin_not_driven noise — expected without power flags.

## The Astra workflow (distilled from JLCPCB/NextPCB reviews of GPT-6 Astra)
What actually worked for Astra is orchestration of specialist tools (KiCad GUI + Freerouting via DSN/SES), NOT raw computer-use routing (GUI-only did almost nothing in 1 hour). Adapted to headless:
1. One project folder with datasheet + reference designs.
2. One complete brief: supply voltage, channel count, load impedance, component tech, size limit.
3. Verify the reference design against the CURRENT datasheet revision — never copy blindly.
4. Schematic as native KiCad objects (multi-sheet OK), not images.
5. Verify every footprint before layout: pin 1, pad numbering, polarity, rotation, land dims vs manufacturer drawing.
6. Set design rules to the values you will order against (fab capabilities); fixed board outline; then place.
7. Define and LOCK critical copper first (supply, bootstrap, switching).
8. Autorouter only for low-current nets; refill zones; confirm locked copper survived.
9. Ship libraries with the project (sym-lib-table / fp-lib-table via ${KIPRJMOD}), UUID links symbol↔footprint.
10. Run ERC + DRC + unconnected + schematic parity TOGETHER; fix every issue at source (never add exclusions just to pass); only then export gerbers/drill/BOM/pos.
Note: Astra's clean DRC ignored 5 DRC + 4 ERC categories and still had 46 parity issues fixed in source data — parity is the check that catches what DRC misses.

## Headless pipeline (validated end-to-end 2026-09-21)
```python
import pcbnew
mm = pcbnew.FromMM
board = pcbnew.NewBoard("out.kicad_pcb")            # arg REQUIRED
edge = pcbnew.PCB_SHAPE(board)
edge.SetShape(pcbnew.SHAPE_T_RECT)
edge.SetStart(pcbnew.VECTOR2I(mm(0), mm(0)))
edge.SetEnd(pcbnew.VECTOR2I(mm(30), mm(20)))
edge.SetLayer(pcbnew.Edge_Cuts); edge.SetWidth(mm(0.1)); board.Add(edge)
f = pcbnew.FootprintLoad("/usr/share/kicad/footprints/Resistor_SMD.pretty", "R_0603_1608Metric")
f.SetReference("R1"); f.SetPosition(pcbnew.VECTOR2I(mm(8), mm(10))); board.Add(f)
net = pcbnew.NETINFO_ITEM(board, "N$1"); board.Add(net)
f.FindPadByNumber("2").SetNet(net)                  # assign pads to nets
board.Save("out.kicad_pcb")
pcbnew.ExportSpecctraDSN(board, "out.dsn")
# shell: java -jar ~/Work/tools/freerouting-2.4.1.jar -de out.dsn -do out.ses -mp 50 -mt 4
board = pcbnew.LoadBoard("out.kicad_pcb")
pcbnew.ImportSpecctraSES(board, "out.ses")
board.Save("out-routed.kicad_pcb")
```

## Verification + export commands
```bash
kicad-cli pcb drc --format json --output drc.json board.kicad_pcb   # parse violations/unconnected_items/schematic_parity
kicad-cli sch erc --format json --output erc.json board.kicad_sch
kicad-cli pcb export gerbers --output gerbers/ board.kicad_pcb
kicad-cli pcb export drill   --output gerbers/ board.kicad_pcb
kicad-cli pcb export pos     --output pos.csv board.kicad_pcb
kicad-cli pcb render --output top.png board.kicad_pcb               # 3D preview PNG
kicad-cli sch export netlist board.kicad_sch
kicad-cli fp export svg --output dir/ FootprintFile.kicad_mod        # footprint preview
```

## Design-rule values that pass JLCPCB-class fabs (2-layer, default)
track ≥ 0.127mm (use 0.2mm), clearance ≥ 0.127mm (use 0.2mm), via 0.3/0.6mm drill/Ø, board min 2mm×2mm. Design rules live in the .kicad_pro / board setup — set them BEFORE routing.
