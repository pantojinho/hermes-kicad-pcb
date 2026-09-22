---
name: kicad-pcb
description: 'Headless KiCad PCB automation for Linux/Windows/macOS — pcbnew Python + kicad-cli + Freerouting (DSN/SES). Also imports EasyEDA / EasyEDA Pro PCBs into KiCad. Use when building, routing, autorouting, validating (DRC/ERC) or exporting (gerber, drill, BOM, CPL) a PCB without the KiCad GUI. Triggered by KiCad, pcbnew, kicad-cli, PCB, placa, schematic, esquemático, autorouter, Freerouting, DSN, SES, DRC, ERC, gerber, BOM, pick-and-place, CPL, JLCPCB, PCBWay, EasyEDA, JLCEDA, LCSC, footprint, ESP32 board, kicad_pcb, kicad_sch.'
---

# KiCad PCB automation (headless, Astra-style, cross-platform)

Design, route, validate and export PCBs without the GUI: pcbnew Python for board
manipulation, `kicad-cli` for DRC/ERC/export, Freerouting headless for autorouting
(DSN/SES loop), and a headless bridge that imports EasyEDA / EasyEDA Pro boards into
KiCad. **Validated end-to-end on Linux** (KiCad 10.0.6 + Freerouting 2.4.1);
Windows and macOS are covered by runtime path resolvers + a re-exec shim and
smoke-tested on CI — the same commands run on any OS, every path is resolved at
runtime by `scripts/kicad_paths.py` (env var > known location > PATH).

## Toolchain

| Component | Requirement | Notes |
|---|---|---|
| KiCad | 10+ | `kicad-cli` + pcbnew Python bindings (come with KiCad) |
| Freerouting | 2.4+ | **bundle with embedded runtime preferred** — no system Java needed; plain jar needs **Java 25+** (jar 2.4.1 = class file 69) |
| Java | only for the jar | bundle linux-x64.zip / windows-x64.msi / macos .dmg embed their own runtime |
| easyeda2kicad | optional | LCSC part → KiCad symbol+footprint+3D (`pipx install easyeda2kicad`) — used by the bridge for components missing from the official libs |

Per-OS install:
- **Linux**: distro package or PPA — bindings import from system `python3`.
- **Windows**: KiCad installer (includes python + pcbnew) + Freerouting MSI. Resolvers find `C:\Program Files\...` automatically; MSI path may vary by version — `FREEROUTING_EXE` overrides anything.
- **macOS**: KiCad app bundle + Freerouting .dmg.
- Bundles: https://github.com/freerouting/freerouting/releases — unzip to `~/Work/tools/` (auto-detected default) or anywhere.

Environment overrides (all optional):

| Var | Purpose |
|---|---|
| `KICAD_PYTHON` | python interpreter that has pcbnew |
| `KICAD_CLI` | path to `kicad-cli` |
| `KICAD10_FOOTPRINT_DIR` / `KICAD_FOOTPRINT_DIR` | official footprints dir |
| `FREEROUTING_EXE` | launcher binary of a bundle |
| `FREEROUTING_JAR` | freerouting.jar (then system Java 25+ is required) |

## Preflight + pipelines (one command each)

```bash
# 0) preflight: lists what is missing and HOW to fix it (exit 1 only with --strict)
python3 skills/kicad-pcb/scripts/check_env.py

# 1) reference pipeline: board -> DSN -> Freerouting -> SES -> DRC (exit 0 = PASS)
python3 skills/kicad-pcb/scripts/demo_autoroute.py --out /tmp/pcb-demo

# 2) full example (ESP32 + LED + USB-C): schematic + board + autoroute + renders
python3 skills/kicad-pcb/scripts/esp32_example.py --out /tmp/esp32
#    outputs: esp32-devboard.kicad_pcb/.kicad_sch, renders/*.png, drc json

# 3) import an EasyEDA / EasyEDA Pro PCB into KiCad
python3 skills/kicad-pcb/scripts/easyeda_bridge.py import-pro project.epro --out outdir/
#    LCSC part -> KiCad symbol/footprint/3D (needs easyeda2kicad installed)
python3 skills/kicad-pcb/scripts/easyeda_bridge.py lcsc C2040 --out outdir/lcsc-lib

# isolated demo stages
python3 demo_autoroute.py --stage create   # board + DSN
python3 demo_autoroute.py --stage route    # autoroute (uses FREEROUTING_EXE/JAR)
python3 demo_autoroute.py --stage import   # imports SES -> routed board
python3 demo_autoroute.py --stage drc      # DRC of an existing board (no pcbnew needed)

# flags: --route-timeout 600  --max-passes 50  --threads 4
# exit codes: 0 ok | 2 environment | 3 stage | 4 DRC FAIL
```

On Windows/macOS, `demo_autoroute.py` / `esp32_example.py` re-exec themselves in
KiCad's bundled Python when `import pcbnew` fails in the current interpreter —
nothing to configure.

## EasyEDA bridge (what it does and does not)

- `import-pro PROJECT.epro` — converts a whole **EasyEDA Pro** PCB to `.kicad_pcb`
  using KiCad's own parser via `pcbnew.PCB_IO_MGR` (headless, no GUI). Validated with
  a real 45-footprint/1068-track board: geometry, nets, zones and outline come
  through (0 unconnected / 0 parity in DRC). Schematics are NOT converted — re-draw
  them or import via KiCad GUI (File → Import Non-KiCad Project → EasyEDA Pro).
- `import-std` — EasyEDA **Standard** JSON (`.../easyeda/sources/pcb/document.json`
  inside the project zip): same `PCB_IO_MGR` route with the `EASYEDA` plugin.
- `lcsc Cxxxxx` — downloads symbol + footprint + 3D model from LCSC via
  `easyeda2kicad` (optional dependency; graceful error with install hint if absent).
- After importing: run `--stage drc` on the output, then route missing nets with the
  autorouter pipeline.

## CRITICAL pitfalls (learned the hard way)

1. **Python interpreter**: any python that can `import pcbnew`. Linux distro/PPA: system `python3`. Windows/macOS: KiCad's bundled python (the scripts re-exec automatically). Override with `KICAD_PYTHON`. The 2–3 `PROPERTY_ENUM` asserts on stderr at import are harmless C++ noise.
2. **py3.14 SWIG patch is CONDITIONAL** (Arch/omarchy + KiCad pkg with python 3.14): only if iteration crashes with `'SwigPyIterator' object has no attribute 'next'` — patch `pcbnew.py` (find via `python3 -c "import pcbnew; print(pcbnew.__file__)"`), 3× `item = it.next()` → `item = it.__next__() if hasattr(it, '__next__') else it.next()`. On Ubuntu 24.04 + KiCad 10.0.6 (python 3.12) NO patch is needed.
3. **Freerouting java version**: the plain jar 2.4.1 needs Java 25+ (Java 17 = `UnsupportedClassVersionError` class file 69). Preferred: OS bundle with embedded runtime — zero Java dependency.
4. `pcbnew.NewBoard(path)` requires the filename arg (KiCad 10).
5. Board outline on Edge.Cuts is REQUIRED before DRC/autorouting. `SHAPE_T_RECT` (uppercase) etc.; coords `VECTOR2I` in nm; `mm = pcbnew.FromMM`.
6. `kicad-cli` 10.0.6 does NOT export DSN / import SES — use `pcbnew.ExportSpecctraDSN(board, path)` / `pcbnew.ImportSpecctraSES(board, path)`. `kicad-cli pcb import` does NOT know EasyEDA — that import lives in Python (`PCB_IO_MGR`).
7. solder_mask_bridge DRC warnings on tight boards are cosmetic mask apertures — real gates are `unconnected_items` + `schematic_parity`.
8. Multi-pad numbers (e.g. ESP32-WROOM-32 thermal pad): several sub-pads share one number — set the net on ALL of them via `fp.Pads()`, else same-number pads short.
9. Symbol pin names ≠ pad names: map via pin number; derived symbols (`extends`) have no own pins — resolve parent and rename nested unit blocks.
10. Footprint anchor is often pad 1, NOT the center. Placement math MUST use real bounding boxes (pads + F.Fab); NEVER `GetBoundingBox()` (includes text + antenna keepout, destroys packers).
11. ESP32/RF modules: keep the antenna keepout entirely off the board edge; copper under the antenna kills RF and trips items_not_allowed.
12. Design rules (min track/drill) live in the `.kicad_pro`, NOT the board: write a `.kicad_pro` (min_track_width 0.127, min_through_hole_diameter 0.2) before DRC, else defaults (0.2mm) reject Freerouting's 0.15mm tracks.
13. Auto-placement with pad+F.Fab bbox packing + 1.0mm gap gives collision-free layouts and lets Freerouting reach 0 unrouted / 0 violations (validated: esp32-dev, 177 tracks).
14. Schematic generation: format version 20260101 works on KiCad 10.0.6; labels at pin-tip coordinates provide connectivity; pin_not_driven ERC noise is expected without power flags.
15. Imported EasyEDA boards keep the ORIGINAL design rules of the source project — expect DRC violations against KiCad/JLCPCB defaults (a real import showed 498). Triage them; don't blanket-fix.

## JLCPCB-class fab rules (2-layer)

track ≥ 0.127mm (use 0.2mm), clearance ≥ 0.127mm, via 0.45mm Ø / 0.2mm drill,
annular ring 0.125mm, **board min 6×6mm**, thickness 0.4–2.4mm (default 1.6).
4+ layers: 0.09mm track, via 0.25/0.15. Full tables, assembly (Economic vs
Standard), PCBA BOM/CPL upload workflow and rotation-offset cautions:
**`references/jlcpcb-rules.md`** (adapted from aklofas/kicad-happy, MIT).

## The Astra workflow (distilled from JLCPCB/NextPCB reviews of GPT-6 Astra)

What worked was orchestrating specialist tools (KiCad + Freerouting via DSN/SES),
NOT raw GUI computer-use. Headless version:
1. One project folder with datasheet + reference designs.
2. One complete brief: supply voltage, channel count, load impedance, component tech, size limit.
3. Verify the reference design against the CURRENT datasheet revision.
4. Schematic as native KiCad objects (multi-sheet OK), not images.
5. Verify every footprint before layout: pin 1, pad numbering, polarity, land dims.
6. Set design rules to the fab values you will order against; fixed outline; then place.
7. Define and LOCK critical copper first (supply, bootstrap, switching).
8. Autorouter only for low-current nets; refill zones; confirm locked copper survived.
9. Ship libraries with the project (sym-lib-table / fp-lib-table via ${KIPRJMOD}).
10. Run ERC + DRC + unconnected + schematic parity TOGETHER; fix every issue at source (never add exclusions just to pass); only then export gerbers/drill/BOM/pos.
Parity is the check that catches what DRC misses (Astra shipped 46 parity issues with a "clean" DRC).

## Raw pcbnew calls (quick reference)

```python
import pcbnew
mm = pcbnew.FromMM
board = pcbnew.NewBoard("out.kicad_pcb")            # arg REQUIRED
edge = pcbnew.PCB_SHAPE(board); edge.SetShape(pcbnew.SHAPE_T_RECT)
edge.SetStart(pcbnew.VECTOR2I(mm(0), mm(0))); edge.SetEnd(pcbnew.VECTOR2I(mm(30), mm(20)))
edge.SetLayer(pcbnew.Edge_Cuts); edge.SetWidth(mm(0.1)); board.Add(edge)
f = pcbnew.FootprintLoad(FP_DIR + "/Resistor_SMD.pretty", "R_0603_1608Metric")
f.SetReference("R1"); f.SetPosition(pcbnew.VECTOR2I(mm(8), mm(10))); board.Add(f)
net = pcbnew.NETINFO_ITEM(board, "N$1"); board.Add(net)
f.FindPadByNumber("2").SetNet(net)
board.Save("out.kicad_pcb")
pcbnew.ExportSpecctraDSN(board, "out.dsn")
# freerouting: bundle launcher or java -jar (Java 25+), flags -de out.dsn -do out.ses -mp 50 -mt 4
board = pcbnew.LoadBoard("out.kicad_pcb"); pcbnew.ImportSpecctraSES(board, "out.ses")
# import EasyEDA Pro PCB (headless):
board = pcbnew.PCB_IO_MGR.Load(pcbnew.PCB_IO_MGR.EASYEDAPRO, "proj.epro")
pcbnew.PCB_IO_MGR.Save(pcbnew.PCB_IO_MGR.KICAD_SEXP, "out.kicad_pcb", board)
```

## Verification + export commands

```bash
kicad-cli pcb drc --format json --output drc.json board.kicad_pcb   # violations/unconnected_items/schematic_parity
kicad-cli sch erc --format json --output erc.json board.kicad_sch
kicad-cli pcb export gerbers --output gerbers/ board.kicad_pcb
kicad-cli pcb export drill   --output gerbers/ board.kicad_pcb
kicad-cli pcb export pos     --output pos.csv board.kicad_pcb
kicad-cli pcb render --side top --quality high -o top.png board.kicad_pcb   # 3D render PNG
kicad-cli sch export pdf -o sch.pdf board.kicad_sch                          # schematic PDF
kicad-cli sch export netlist board.kicad_sch
kicad-cli fp export svg --output dir/ FootprintFile.kicad_mod
```

## Companion skills

For design **review**, BOM/sourcing and manufacturing (not covered here): install
[aklofas/kicad-happy](https://github.com/aklofas/kicad-happy) (MIT) — KiCad project
analysis/DFM, JLCPCB/LCSC/DigiKey BOM workflows, PCBA upload translator.
