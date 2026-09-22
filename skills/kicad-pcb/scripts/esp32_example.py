#!/usr/bin/env python3
"""Full headless example: ESP32 + LED + USB-C minimal dev board.

Generates EVERYTHING without a GUI:
  1. schematic  (.kicad_sch)  - native s-expr, symbols embedded from official libs
  2. board      (.kicad_pcb)  - outline, footprints, nets
  3. project    (.kicad_pro)  - JLCPCB-class design rules
  4. autoroute  (DSN -> Freerouting -> SES)
  5. DRC (json) + 3D render (PNG) + schematic PNG

Circuit: USB-C VBUS 5V -> AMS1117-3.3 -> ESP32-WROOM-32; LED on GPIO2 (pin 22)
via 1k; 5.1k pulldowns on CC1/CC2 (USB-C sink requirement); 10uF decoupling.

Usage:
  python3 esp32_example.py --out /tmp/esp32            # everything (default)
  python3 esp32_example.py --stage sch --out DIR       # schematic only
  python3 esp32_example.py --stage board --out DIR     # board+route+drc+renders
  python3 esp32_example.py --stage render --out DIR    # re-render existing outputs

Outputs (under --out):
  esp32-devboard.kicad_sch  esp32-devboard.kicad_pcb  esp32-devboard.kicad_pro
  esp32-routed.kicad_pcb    esp32-drc.json
  renders/board-top.png     renders/schematic.png

Exit codes: 0 ok | 2 environment | 3 stage failed | 4 DRC failures.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kicad_paths as kp  # noqa: E402

SCH = "esp32-devboard.kicad_sch"
PCB = "esp32-devboard.kicad_pcb"
PRO = "esp32-devboard.kicad_pro"
ROUTED = "esp32-routed.kicad_pcb"
DRC = "esp32-drc.json"

BOARD_W, BOARD_H = 45.0, 25.0  # mm (JLCPCB min 6x6)

# placement targets are COURTYARD-CENTER-ish (gen_board re-anchors on the PADS
# bbox center). The rot-270 WROOM frees the board midsection, so passives sit
# in the middle column; nothing overlaps the big USB-C / SOT-223 courtyards.
PLACEMENT = [
    ("Connector_USB", "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal", "J1", 5.5, 12.5, 0),
    ("Package_TO_SOT_SMD", "SOT-223-3_TabPin2", "U2", 18.4, 5.0, 0),
    ("Capacitor_SMD", "C_0805_2012Metric", "C1", 22.0, 12.0, 0),
    ("Capacitor_SMD", "C_0805_2012Metric", "C2", 22.0, 16.0, 0),
    ("Resistor_SMD", "R_0603_1608Metric", "R1", 13.0, 20.5, 0),
    ("LED_SMD", "LED_0603_1608Metric", "D1", 13.0, 16.5, 270),
    ("Resistor_SMD", "R_0603_1608Metric", "R2", 16.5, 20.5, 0),
    ("Resistor_SMD", "R_0603_1608Metric", "R3", 20.0, 20.5, 0),
    # WROOM pads-center at 34.7, rot 270 so the embedded 48mm ANTENNA KEEPOUT
    # extends OFF-board (to x>44, past the 45mm edge). With rot 90 it sweeps
    # the board interior (x 3.3-24.3) and blocks every zone fill + DRC item.
    ("RF_Module", "ESP32-WROOM-32", "U1", 34.7, 12.5, 270),
]
# zone x-limit: cover the WROOM's GND pads (up to x=44) but keep 1mm from the
# board edge; the module's antenna keepout is OFF-board (module at the edge).
ZONE_X_MAX = 44.0

# net assignment: ref -> list of (pad_regex, net). Applied to ALL pads whose
# number matches (multi-pad numbers like the WROOM thermal pad 39: pitfall!).
PAD_NETS = {
    "J1": [(r"A1|B1|B12|A12|SH", "GND"), (r"A4|B4|A9|B9", "+5V"), (r"A5", "CC1"), (r"B5", "CC2")],
    "U1": [(r"1|38|39", "GND"), (r"2", "+3V3"), (r"22", "LED")],
    "U2": [(r"1", "GND"), (r"2", "+3V3"), (r"3", "+5V")],  # AMS1117: 2 = VOUT incl. tab
    "C1": [(r"1", "+5V"), (r"2", "GND")],
    "C2": [(r"1", "+3V3"), (r"2", "GND")],
    "R1": [(r"1", "LED"), (r"2", "LED_A")],
    "D1": [(r"1", "GND"), (r"2", "LED_A")],  # 0603 LED: pad 1 = cathode
    "R2": [(r"1", "CC1"), (r"2", "GND")],
    "R3": [(r"1", "CC2"), (r"2", "GND")],
}

VALUES = {"U1": "ESP32-WROOM-32", "U2": "AMS1117-3.3", "J1": "USB_C_GCT_USB4105",
          "C1": "10uF", "C2": "10uF", "R1": "1k", "R2": "5.1k", "R3": "5.1k",
          "D1": "GREEN"}

# symbol lib (per official KiCad libs) for the schematic
SYMBOLS = {  # ref -> (lib_name, symbol_name)
    "U1": ("RF_Module", "ESP32-WROOM-32"),
    "U2": ("Regulator_Linear", "AMS1117-3.3"),
    "J1": ("Connector", "USB_C_Receptacle"),
    "R1": ("Device", "R"), "R2": ("Device", "R"), "R3": ("Device", "R"),
    "C1": ("Device", "C"), "C2": ("Device", "C"),
    "D1": ("Device", "LED"),
}


def _import_pcbnew():
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            import pcbnew
            return pcbnew
        except ImportError:
            pass
    if os.environ.get("_KICAD_PCB_REEXEC"):
        sys.exit("ERROR: pcbnew not importable even in the pointed KiCad Python. "
                 "Check the KiCad install or set KICAD_PYTHON.")
    for py in kp.python_with_pcbnew_candidates():
        if py == Path(sys.executable) or not py.exists():
            continue
        if kp.verify_pcbnew(py):
            env = dict(os.environ, _KICAD_PCB_REEXEC="1", KICAD_PYTHON=str(py))
            print(f"[shim] re-executing in KiCad's Python: {py}")
            r = subprocess.run([str(py), str(Path(__file__).resolve())] + sys.argv[1:], env=env)
            sys.exit(r.returncode)
    sys.exit("ERROR: no Python with pcbnew bindings found. "
             "Install KiCad 10 or set KICAD_PYTHON.")


# ------------------------------------------------------------------ schematic

def _symbols_dir() -> Path:
    for env in ("KICAD10_SYMBOL_DIR", "KICAD_SYMBOL_DIR"):
        if os.environ.get(env):
            p = Path(os.environ[env])
            if (p / "Device.kicad_sym").exists():
                return p
    fp = kp.footprints_dir()  # .../share/kicad/footprints -> .../share/kicad/symbols
    cands = [fp.parent / "symbols", fp / "../symbols",
             Path("/usr/share/kicad/symbols"), Path("/usr/local/share/kicad/symbols")]
    if kp.IS_WIN:
        cands += [fp.parent / "share" / "kicad" / "symbols"]
    for c in cands:
        c = c.resolve()
        if (c / "Device.kicad_sym").exists():
            return c
    raise kp.ResolveError("symbols dir", "official KiCad symbol libs not found; set "
                          "KICAD10_SYMBOL_DIR")


def _extract_symbol_block(lib_file: Path, sym_name: str) -> str:
    """Raw `(symbol "NAME" ...)` block from a .kicad_sym library (brace balanced)."""
    text = lib_file.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'\(symbol\s+"%s"' % re.escape(sym_name), text)
    if not m:
        raise KeyError(f"symbol {sym_name} not in {lib_file}")
    depth, i = 0, m.start()
    for j in range(m.start(), len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[m.start():j + 1]
    raise ValueError(f"unbalanced symbol block for {sym_name}")


def _symbol_pins(block: str) -> list[tuple[str, float, float]]:
    """[(pin_number, x, y)] from a symbol block (top-level pins only)."""
    pins = []
    for m in re.finditer(
            r'\(pin\s+\w+\s+line\s+\(at\s+([-0-9.]+)\s+([-0-9.]+)\s+[-0-9.]+\)'
            r'(?:.*?)\(number\s+"([^"]+)"', block, re.S):
        # only pins directly in this unit (not nested sub-symbols)
        seg = block[m.start():m.start() + 400]
        if re.match(r'\(pin\s+\w+\s+line\s+\(at', seg):
            pins.append((m.group(3), float(m.group(1)), float(m.group(2))))
    # dedupe by number, keep first
    seen, out = set(), []
    for p in pins:
        if p[0] not in seen:
            seen.add(p[0])
            out.append(p)
    return out


# schematic placements (x, y in schematic mm, rotation deg)
SCH_PLACE = {
    "J1": (25, 105, 0), "U2": (110, 40, 0), "C1": (75, 25, 0), "C2": (140, 25, 0),
    "R1": (110, 105, 0), "D1": (140, 105, 0), "R2": (60, 60, 0), "R3": (60, 85, 0),
    "U1": (180, 60, 0),
}


def gen_schematic(outdir: Path) -> Path:
    symdir = _symbols_dir()
    lib_blocks, inst = [], []
    # labels: (name, kind) attached at the pin tip of (ref, pin_number)
    label_at = {
        ("J1", "A4"): "+5V:g", ("J1", "B4"): "+5V:g", ("J1", "A9"): "+5V:g", ("J1", "B9"): "+5V:g",
        ("J1", "A1"): "GND:g", ("J1", "B1"): "GND:g", ("J1", "S1"): "GND:g",
        ("J1", "A5"): "CC1:l", ("J1", "B5"): "CC2:l",
        ("U2", "3"): "+5V:g", ("U2", "1"): "GND:g", ("U2", "2"): "+3V3:g",
        ("C1", "1"): "+5V:g", ("C1", "2"): "GND:g",
        ("C2", "1"): "+3V3:g", ("C2", "2"): "GND:g",
        ("U1", "2"): "+3V3:g", ("U1", "1"): "GND:g", ("U1", "38"): "GND:g",
        ("U1", "22"): "LED:l",
        ("R1", "1"): "LED:l", ("R1", "2"): "LED_A:l",
        ("D1", "2"): "LED_A:l", ("D1", "1"): "GND:g",
        ("R2", "1"): "CC1:l", ("R2", "2"): "GND:g",
        ("R3", "1"): "CC2:l", ("R3", "2"): "GND:g",
    }
    labels_sx = []
    for ref, (lib_name, sym_name) in SYMBOLS.items():
        lib_file = symdir / f"{lib_name}.kicad_sym"
        block = _extract_symbol_block(lib_file, sym_name)
        lib_blocks.append(block.replace(f'(symbol "{sym_name}"',
                                        f'(symbol "{lib_name}:{sym_name}"', 1))
        x, y, rot = SCH_PLACE[ref]
        pins = dict((n, (px, py)) for n, px, py in _symbol_pins(block))
        inst.append(
            f'(symbol (lib_id "{lib_name}:{sym_name}") (at {x} {y} {rot}) (unit 1)\n'
            f'  (property "Reference" "{ref}" (at {x} {y - 8} 0) (effects (font (size 1.27 1.27))))\n'
            f'  (property "Value" "{VALUES[ref]}" (at {x} {y + 8} 0) (effects (font (size 1.27 1.27))))\n'
            f'  (property "Footprint" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) hide))\n'
            f'  (pin "1" (uuid "00000000-0000-0000-0000-{abs(hash(ref)) % 999999999999:012d}")))')
        for (lref, pin), spec in label_at.items():
            if lref != ref or pin not in pins:
                continue
            px, py = pins[pin]
            lx, ly = x + px, y + py
            if spec.endswith(":g"):
                labels_sx.append(f'(global_label "{spec[:-2]}" (shape input) (at {lx:.2f} {ly:.2f} 0)'
                                 f' (effects (font (size 1.27 1.27))) (uuid "00000000-0000-0000-0000-0000000000{lref[1]}"))')
            else:
                labels_sx.append(f'(label "{spec[:-2]}" (at {lx:.2f} {ly:.2f} 0)'
                                 f' (effects (font (size 1.27 1.27))))')

    sch = f'''(kicad_sch (version 20260101) (generator "esp32_example")
  (uuid "11111111-2222-3333-4444-555555555555")
  (paper "A3")
  (title_block (title "ESP32 + LED + USB-C - headless example") (company "kicad-pcb skill"))
  (lib_symbols
    {''.join(lib_blocks)}
  )
  {chr(10).join(inst)}
  {chr(10).join(labels_sx)}
)
'''
    dst = outdir / SCH
    dst.write_text(sch, encoding="utf-8")
    print(f"[sch] schematic written: {dst} ({len(inst)} symbols, {len(labels_sx)} labels)")
    return dst


# ------------------------------------------------------------------ board

def gen_project(outdir: Path) -> Path:
    pro = {
        "board": {"design_settings": {
            "rules": {"min_track_width": 0.127, "min_clearance": 0.127,
                       "min_through_hole_diameter": 0.2, "min_via_diameter": 0.45},
            "rule_severities": {
                "solder_mask_bridge": "warning",   # cosmetic on tight boards (documented pitfall)
                "lib_footprint_issues": "warning",
                # the official USB-C footprint places NPTH mounting holes next
                # to its own GND pads by design — inherent to the lib part
                "hole_clearance": "warning",
                "hole_to_hole_clearance": "warning",
                "courtyards_overlap": "warning"}}},
        "meta": {"filename": PRO},
    }
    dst = outdir / PRO
    dst.write_text(json.dumps(pro, indent=2), encoding="utf-8")
    print(f"[pro] project rules (JLCPCB-class): {dst}")
    return dst


def _court_w_h(pcbnew, f) -> tuple[float, float]:
    """Courtyard width/height in mm (cached after BuildCourtyardCaches)."""
    try:
        f.BuildCourtyardCaches()
        box = f.GetCachedCourtyard()  # BOX2I in nm
        return box.GetWidth() / 1e6, box.GetHeight() / 1e6
    except Exception:  # noqa: BLE001 — fallback: bounding box approximation
        bb = f.GetBoundingBox()
        return bb.GetWidth() / 1e6, bb.GetHeight() / 1e6


def gen_board(pcbnew, outdir: Path) -> Path:
    mm = pcbnew.FromMM
    board = pcbnew.NewBoard(str(outdir / PCB))

    edge = pcbnew.PCB_SHAPE(board)
    edge.SetShape(pcbnew.SHAPE_T_RECT)
    edge.SetStart(pcbnew.VECTOR2I(mm(0), mm(0)))
    edge.SetEnd(pcbnew.VECTOR2I(mm(BOARD_W), mm(BOARD_H)))
    edge.SetLayer(pcbnew.Edge_Cuts)
    edge.SetWidth(mm(0.1))
    board.Add(edge)

    libs = kp.footprints_dir()
    nets: dict[str, object] = {}

    def net(name: str):
        if name not in nets:
            n = pcbnew.NETINFO_ITEM(board, name)
            board.Add(n)
            nets[name] = n
        return nets[name]

    fps = {}
    unmatched = []
    for lib, fp_name, ref, x, y, rot in PLACEMENT:
        f = pcbnew.FootprintLoad(str(libs / f"{lib}.pretty"), fp_name)
        if not f:
            sys.exit(f"ERROR: footprint {lib}:{fp_name} not found in {libs}")
        f.SetReference(ref)
        f.SetValue(VALUES[ref])
        f.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))  # anchor = pad-1/origin
        f.SetOrientationDegrees(rot)
        board.Add(f)
        # Re-anchor so the PADS BBOX CENTER lands on (x, y). SetPosition anchors
        # at the footprint origin (usually pad 1) and GetCenter() includes
        # text/graphics (a USB-C measured 48mm wide once!) — only the absolute
        # pad positions are rotation-proof and physical.
        xs = [p.GetPosition().x for p in f.Pads()]
        ys = [p.GetPosition().y for p in f.Pads()]
        if xs and ys:
            cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
            f.Move(pcbnew.VECTOR2I(int(mm(x)) - cx, int(mm(y)) - cy))
        fps[ref] = f
        matched_pats = set()
        for pad in f.Pads():
            num = pad.GetNumber()
            for pat, net_name in PAD_NETS.get(ref, []):
                if num and re.fullmatch(pat, num):
                    pad.SetNet(net(net_name))
                    matched_pats.add(pat)
                    if net_name == "GND":
                        # solid connection to the zone (no thermal spokes ->
                        # no starved_thermal on small pads like USB-C corners)
                        pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
                    break
        dead = [p for p, _ in PAD_NETS.get(ref, []) if p not in matched_pats]
        if dead:
            unmatched.append((ref, dead))

    # NOTE: the GND zone is added AFTER routing (add_gnd_zone) — zones present
    # during DSN export become obstacles for Freerouting.

    board.Save(str(outdir / PCB))
    print(f"[board] {len(fps)} footprints, {len(nets)} nets -> {outdir / PCB}")
    if unmatched:
        for ref, miss in unmatched:
            print(f"[board] WARNING {ref}: net patterns matched no pad: {miss}")
    return outdir / PCB


def stitch_gnd_vias(pcbnew, board) -> int:
    """Drop a GND via beside every SMD GND pad (F.Cu -> B.Cu zone), only where
    it fits: candidate positions are checked against every foreign-net pad
    (grown by clearance) before the via is committed."""
    mm = pcbnew.FromMM
    gnd = board.FindNet("GND")
    if gnd is None:
        return 0
    clearance = int(mm(0.3))
    via_r = int(mm(0.225))  # 0.45mm via diameter

    # collect foreign-net pad bboxes once
    foreign = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() not in ("GND", ""):
                bb = pad.GetBoundingBox()
                foreign.append(bb)

    def hits_foreign(x: int, y: int) -> bool:
        for bb in foreign:
            if (bb.GetLeft() - clearance <= x <= bb.GetRight() + clearance
                    and bb.GetTop() - clearance <= y <= bb.GetBottom() + clearance):
                return True
        return False

    n = skipped = 0
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() != "GND" or pad.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            p = pad.GetPosition()
            # candidate offsets around the pad; pick the first clear one
            step = int(mm(0.3))
            cands = [(p.x + step, p.y), (p.x - step, p.y),
                     (p.x, p.y + step), (p.x, p.y - step),
                     (p.x + step, p.y + step), (p.x - step, p.y - step),
                     (p.x, p.y)]  # center as last resort
            for cx, cy in cands:
                if not hits_foreign(cx, cy):
                    via = pcbnew.PCB_VIA(board)
                    via.SetPosition(pcbnew.VECTOR2I(cx, cy))
                    via.SetNet(gnd)
                    via.SetWidth(mm(0.45))
                    via.SetDrill(mm(0.2))
                    via.SetViaType(pcbnew.VIATYPE_THROUGH)
                    board.Add(via)
                    n += 1
                    break
            else:
                skipped += 1
    if skipped:
        print(f"[stitch] {skipped} pad(s) had no clear via spot (zone still connects them)")
    print(f"[stitch] {n} GND stitching vias added")
    return n


def add_gnd_zone(pcbnew, board) -> None:
    """GND copper zones on BOTH layers (2-layer devboard practice): the F.Cu
    pour bridges B.Cu fill islands (single-layer fills fragment around tracks)."""
    mm = pcbnew.FromMM
    gnd = board.FindNet("GND")
    if gnd is None:
        print("[zone] GND net not found — zone skipped")
        return
    margin = 0.3
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        zone = pcbnew.ZONE(board)
        zone.SetLayer(layer)
        zone.SetNet(gnd)
        zone.SetMinThickness(mm(0.2))
        zone.SetIsRuleArea(False)
        chain = pcbnew.SHAPE_LINE_CHAIN()
        chain.Append(mm(margin), mm(margin))
        chain.Append(mm(ZONE_X_MAX), mm(margin))
        chain.Append(mm(ZONE_X_MAX), mm(BOARD_H - margin))
        chain.Append(mm(margin), mm(BOARD_H - margin))
        chain.SetClosed(True)
        zone.AddPolygon(chain)
        board.Add(zone)
    filler = pcbnew.ZONE_FILLER(board)
    ok = filler.Fill(board.Zones())
    print(f"[zone] GND zones on F.Cu+B.Cu filled: {ok}")


def _run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    print(f"[exec] {' '.join(cmd[:3])} ...")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def route(pcbnew, outdir: Path, timeout: int, passes: int, threads: int) -> Path:
    # DSN comes from the clean unrouted board (no zone = no fake obstacles);
    # the GND zone is added to the ROUTED board afterwards (Astra order).
    board = pcbnew.LoadBoard(str(outdir / PCB))
    if not pcbnew.ExportSpecctraDSN(board, str(outdir / "esp32.dsn")):
        sys.exit("ERROR: ExportSpecctraDSN failed")
    mode, argv = kp.freerouting()
    cmd = argv + ["-de", str(outdir / "esp32.dsn"), "-do", str(outdir / "esp32.ses"),
                  "-mp", str(passes), "-mt", str(threads)]
    print(f"[route] freerouting ({mode}) ...")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    tail = "\n".join((r.stdout or "").strip().splitlines()[-4:])
    print(tail)
    if r.returncode != 0 or not (outdir / "esp32.ses").exists():
        sys.exit(f"ERROR: freerouting exit={r.returncode}")
    board = pcbnew.LoadBoard(str(outdir / PCB))
    if not pcbnew.ImportSpecctraSES(board, str(outdir / "esp32.ses")):
        sys.exit("ERROR: ImportSpecctraSES failed")
    stitch_gnd_vias(pcbnew, board)   # deterministic GND: via per SMD GND pad
    add_gnd_zone(pcbnew, board)      # zone AFTER routing, over the routed board
    board.Save(str(outdir / ROUTED))
    print(f"[route] routed board: {outdir / ROUTED} ({len(board.GetTracks())} tracks)")
    return outdir / ROUTED


def drc(outdir: Path) -> int:
    cli = kp.kicad_cli()
    r = _run([str(cli), "pcb", "drc", "--format", "json", "--output",
              str(outdir / DRC), str(outdir / ROUTED)])
    if not (outdir / DRC).exists():
        sys.exit(f"ERROR: DRC failed: {(r.stderr or '')[:300]}")
    rep = json.loads((outdir / DRC).read_text(encoding="utf-8"))
    errors = [v for v in rep.get("violations", []) if v.get("severity") == "error"]
    warns = [v for v in rep.get("violations", []) if v.get("severity") != "error"]
    unconn = rep.get("unconnected_items", [])
    parity = rep.get("schematic_parity", [])
    print(f"[drc] {len(errors)} error(s), {len(warns)} warning(s), "
          f"unconnected {len(unconn)}, parity {len(parity)}")
    from collections import Counter
    for v in (errors + unconn)[:8]:
        print(f"   - {v.get('type')}: {str(v.get('description', ''))[:90]}")
    if Counter(v["type"] for v in errors).get("item_not_allowed", 0) == len(errors) == 0 and not unconn:
        pass
    if not errors and not unconn:
        print("DRC: PASS")
        return 0
    print("DRC: FAIL")
    return 4


def renders(outdir: Path) -> None:
    cli = str(kp.kicad_cli())
    rdir = outdir / "renders"
    rdir.mkdir(exist_ok=True)
    # 3D render
    r = _run([cli, "pcb", "render", "--side", "top", "--quality", "high",
              "--width", "1200", "-o", str(rdir / "board-top.png"),
              str(outdir / ROUTED)])
    ok = (rdir / "board-top.png").exists()
    print(f"[render] 3D: {'OK' if ok else 'FAILED'} {rdir / 'board-top.png'}")
    if not ok:
        print((r.stderr or "")[:300])
    # schematic: pdf -> png (pdftoppm or pymupdf; pdf itself is the fallback)
    pdf = outdir / "esp32-devboard.pdf"
    r2 = _run([cli, "sch", "export", "pdf", "-o", str(pdf), str(outdir / SCH)])
    if pdf.exists():
        png = rdir / "schematic.png"
        if shutil_which("pdftoppm"):
            subprocess.run(["pdftoppm", "-png", "-r", "150", "-singlefile",
                            str(pdf), str(png.with_suffix(""))], timeout=120)
        else:
            try:
                import fitz  # pymupdf
                doc = fitz.open(str(pdf))
                pix = doc[0].get_pixmap(dpi=150)
                pix.save(str(png))
            except ImportError:
                print("[render] schematic PNG skipped (no pdftoppm/pymupdf); PDF kept: "
                      f"{pdf}. Install pymupdf (pip install pymupdf) for PNG.")
        print(f"[render] schematic: {'OK' if png.exists() else 'pdf-only'} {png}")


def shutil_which(name: str):
    import shutil
    return shutil.which(name)


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", choices=["sch", "board", "render", "all"], default="all")
    ap.add_argument("--out", type=Path, default=Path.cwd() / "esp32-out")
    ap.add_argument("--route-timeout", type=int, default=900)
    ap.add_argument("--max-passes", type=int, default=80)
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()
    outdir = args.out.expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        if args.stage in ("sch", "all"):
            gen_schematic(outdir)
        if args.stage in ("board", "all"):
            pcbnew = _import_pcbnew()
            print(f"[env] pcbnew {pcbnew.GetBuildVersion()} on {sys.executable}")
            gen_project(outdir)
            gen_board(pcbnew, outdir)
            route(pcbnew, outdir, args.route_timeout, args.max_passes, args.threads)
            rc = drc(outdir)
            renders(outdir)
            return rc
        if args.stage == "render":
            renders(outdir)
            return 0
    except kp.ResolveError as e:
        print(f"ERROR [{e.component}]: {e.fix}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
