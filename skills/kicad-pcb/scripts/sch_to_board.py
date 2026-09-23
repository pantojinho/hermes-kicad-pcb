#!/usr/bin/env python3
"""Existing KiCad schematic -> starting-point board (headless), for ANY project.

What it does (every step is generic, nothing is project-specific):
  1. copies the project folder to --out (the source project is never modified);
  2. reads the schematic netlist (kicad-cli): footprint, nets and the hierarchical
     symbol path of every part, so schematic parity holds by construction;
  3. loads each footprint through the project's fp-lib-table / official libraries;
  4. places parts with a deterministic shelf packing on their courtyards
     (largest first) inside an automatic or given outline;
  5. optionally routes with Freerouting (DSN/SES), then pours GND on both layers;
  6. runs DRC with schematic parity and prints the result.

The placement is a STARTING POINT for a human or an agent to refine: it knows
nothing about connectors at edges, antennas, decoupling next to pins, thermal or RF
rules. Parts whose footprint is empty or cannot be found are listed, not guessed.

Usage:
  sch_to_board.py ROOT.kicad_sch --out DIR [--outline 84,60] [--layers 2|4]
                  [--fab jlcpcb|kicad] [--gap 1.5] [--no-route] [--route-timeout 600]

Runs in KiCad's Python (re-execs itself when `import pcbnew` fails).
Exit codes: 0 board with clean DRC | 2 environment | 3 failed | 4 DRC findings.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kicad_paths as kp  # noqa: E402

MARGIN = 2.0  # mm between parts and the board edge

# Board minimums per fab class (references/jlcpcb-rules.md). A new board otherwise gets
# KiCad's generic defaults (0.2 mm track, 0.3 mm hole), which reject perfectly
# manufacturable vendor footprints (e.g. 0.2 mm thermal vias in module pads) and the
# 0.15 mm neck-downs Freerouting makes at fine-pitch pads.
FAB_RULES = {  # layers -> (track, clearance, via diameter, via/min drill, annular ring)
    ("jlcpcb", 2): (0.127, 0.127, 0.45, 0.20, 0.125),
    ("jlcpcb", 4): (0.09, 0.09, 0.25, 0.15, 0.05),
}


def read_netlist(cli: str, sch: Path, work: Path) -> tuple[list[dict], dict[tuple[str, str], str]]:
    xml = work / "_netlist.xml"
    subprocess.run([cli, "sch", "export", "netlist", "--format", "kicadxml", "-o", str(xml), str(sch)],
                   capture_output=True, text=True, timeout=300)
    if not xml.exists():
        raise RuntimeError("netlist export failed")
    root = ET.parse(xml).getroot()
    comps = []
    for c in root.iter("comp"):
        sp = c.find("sheetpath")
        fields = {f.get("name"): (f.text or "") for f in c.iter("field")}
        comps.append({"ref": c.get("ref"), "value": c.findtext("value") or "",
                      "datasheet": fields.get("Datasheet", "") or (c.findtext("datasheet") or ""),
                      "description": fields.get("Description", "") or (c.findtext("description") or ""),
                      "footprint": (c.findtext("footprint") or "").strip(),
                      "path": (sp.get("tstamps") if sp is not None else "/") + (c.findtext("tstamps") or "").strip()})
    pad_nets = {}
    for n in root.iter("net"):
        name = n.get("name")
        if name.startswith(("unconnected-(", "Net-(")):  # KiCad stores "/" there as {slash}
            head, body = name.split("(", 1)
            name = f"{head}({body.replace('/', '{slash}')}"
        for node in n.iter("node"):
            pad_nets[(node.get("ref"), node.get("pin"))] = name
    xml.unlink()
    return comps, pad_nets


def courtyard(pcbnew, f) -> tuple[int, int, int, int]:
    f.BuildCourtyardCaches()
    bb = f.GetCourtyard(pcbnew.F_CrtYd).BBox()
    if bb.GetWidth() > 0 and bb.GetHeight() > 0:
        return bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom()
    boxes = [p.GetBoundingBox() for p in f.Pads()]
    m = pcbnew.FromMM(0.5)
    return (min(b.GetLeft() for b in boxes) - m, min(b.GetTop() for b in boxes) - m,
            max(b.GetRight() for b in boxes) + m, max(b.GetBottom() for b in boxes) + m)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sch", type=Path, help="root .kicad_sch of the project")
    ap.add_argument("--out", type=Path, required=True, help="new folder (project copy + board)")
    ap.add_argument("--outline", help="W,H in mm (default: sized from the parts)")
    ap.add_argument("--layers", type=int, choices=(2, 4), default=2)
    ap.add_argument("--gap", type=float, default=1.5, help="mm between courtyards")
    ap.add_argument("--fab", choices=("jlcpcb", "kicad"), default="jlcpcb",
                    help="board minimum rules: JLCPCB class (default) or KiCad's generic defaults")
    ap.add_argument("--no-route", action="store_true")
    ap.add_argument("--route-timeout", type=int, default=600)
    args = ap.parse_args()
    pcbnew = kp.import_pcbnew(__file__)
    mm = pcbnew.FromMM
    src = args.sch.resolve()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        print(f"ERROR: {out} is not empty (the source project is never modified)", file=sys.stderr)
        return 3
    shutil.copytree(src.parent, out, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "*-backups"))
    sch = out / src.name
    board_path = sch.with_suffix(".kicad_pcb")
    try:
        cli = str(kp.kicad_cli())
        comps, pad_nets = read_netlist(cli, sch, out)
    except (kp.ResolveError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    board = pcbnew.NewBoard(str(board_path))
    if args.layers == 4:
        board.SetCopperLayerCount(4)
    if args.fab != "kicad":
        trk, clr, via, drl, ring = FAB_RULES[(args.fab, args.layers)]
        ds = board.GetDesignSettings()
        ds.m_TrackMinWidth, ds.m_MinClearance = mm(trk), mm(clr)
        ds.m_ViasMinSize, ds.m_MinThroughDrill, ds.m_ViasMinAnnularWidth = mm(via), mm(drl), mm(ring)
        print(f"[rules] {args.fab} {args.layers}-layer minimums: track {trk}, clearance {clr}, "
              f"via {via}/{drl}, annular {ring} mm — confirm against the fab's current capabilities")
    nets: dict[str, object] = {}

    def net(name: str):
        if name not in nets:
            nets[name] = pcbnew.NETINFO_ITEM(board, name)
            board.Add(nets[name])
        return nets[name]

    placed, skipped = [], []
    for c in comps:
        if c["ref"].startswith("#"):
            continue
        if not c["footprint"]:
            skipped.append(f"{c['ref']} ({c['value']}): no footprint assigned")
            continue
        nick, _, name = c["footprint"].partition(":")
        lib = kp.footprint_lib(nick, out)
        f = pcbnew.FootprintLoad(str(lib), name) if lib else None
        if not f:
            skipped.append(f"{c['ref']}: footprint {c['footprint']} not found")
            continue
        f.SetFPID(pcbnew.LIB_ID(nick, name))
        f.SetPath(pcbnew.KIID_PATH(c["path"]))
        f.SetReference(c["ref"])
        f.SetValue(c["value"])
        # parity also compares these fields with the symbol
        for ftype, key in ((pcbnew.FIELD_T_DATASHEET, "datasheet"), (pcbnew.FIELD_T_DESCRIPTION, "description")):
            fld = f.GetField(ftype)
            if fld is not None and c[key] and c[key] != "~":
                fld.SetText(c[key])
        board.Add(f)
        for pad in f.Pads():
            n = pad_nets.get((c["ref"], pad.GetNumber()))
            if n:
                pad.SetNet(net(n))
                if n == "GND":  # solid pour connection: thermal spokes starve on small pads
                    pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
        placed.append(f)

    # ---- deterministic shelf packing on courtyards, largest first
    boxes = {f.GetReference(): courtyard(pcbnew, f) for f in placed}
    size = {r: ((b[2] - b[0]) / 1e6, (b[3] - b[1]) / 1e6) for r, b in boxes.items()}
    order = sorted(placed, key=lambda f: -size[f.GetReference()][0] * size[f.GetReference()][1])
    if args.outline:
        W, H = (float(v) for v in args.outline.split(","))
    else:
        area = sum((w + args.gap) * (h + args.gap) for w, h in size.values())
        W = max(max((w for w, _ in size.values()), default=10) + 2 * MARGIN, math.sqrt(area * 1.6) + 2 * MARGIN)
        H = None
    x, y, row_h = MARGIN, MARGIN, 0.0
    for f in order:
        w, h = size[f.GetReference()]
        if x + w > W - MARGIN and x > MARGIN:
            x, y, row_h = MARGIN, y + row_h + args.gap, 0.0
        b = boxes[f.GetReference()]
        f.Move(pcbnew.VECTOR2I(int(mm(x)) - b[0], int(mm(y)) - b[1]))
        x += w + args.gap
        row_h = max(row_h, h)
    used_h = y + row_h + MARGIN
    if H is None:
        H = used_h
    elif used_h > H:
        print(f"ERROR: parts need {used_h:.1f} mm of height, outline gives {H} mm", file=sys.stderr)
        return 3
    edge = pcbnew.PCB_SHAPE(board)
    edge.SetShape(pcbnew.SHAPE_T_RECT)
    edge.SetStart(pcbnew.VECTOR2I(0, 0))
    edge.SetEnd(pcbnew.VECTOR2I(int(mm(W)), int(mm(H))))
    edge.SetLayer(pcbnew.Edge_Cuts)
    edge.SetWidth(mm(0.1))
    board.Add(edge)
    board.Save(str(board_path))
    print(f"[board] {len(placed)} footprints placed on {W:.1f} x {H:.1f} mm, {len(nets)} nets, "
          f"{args.layers} layers -> {board_path.name}")
    for s in skipped:
        print(f"[board] not placed: {s}")

    # ---- routing (optional) + GND pours
    if not args.no_route:
        try:
            mode, argv = kp.freerouting()
            dsn, ses = out / "board.dsn", out / "board.ses"
            board = pcbnew.LoadBoard(str(board_path))
            # route with +0.01 mm clearance: Freerouting rounds some gaps to just under
            # the rule (0.1981 vs 0.2 mm seen), which DRC then flags. Netclasses live in
            # the PROJECT settings shared by every loaded board: restore right after export.
            nc = board.GetDesignSettings().m_NetSettings.GetDefaultNetclass()
            rule = nc.GetClearance()
            nc.SetClearance(rule + mm(0.01))
            pcbnew.ExportSpecctraDSN(board, str(dsn))
            nc.SetClearance(rule)
            r = subprocess.run(argv + ["-de", str(dsn), "-do", str(ses), "-mp", "50", "-mt", "4"],
                               capture_output=True, text=True, timeout=args.route_timeout)
            if ses.exists():
                board = pcbnew.LoadBoard(str(board_path))
                pcbnew.ImportSpecctraSES(board, str(ses))
                print(f"[route] freerouting ({mode}): {len(board.GetTracks())} track/via items")
            else:
                print(f"[route] freerouting produced no SES (exit {r.returncode}); board left unrouted")
        except kp.ResolveError as e:
            print(f"[route] skipped: {e.fix}")
        except subprocess.TimeoutExpired:
            print(f"[route] freerouting exceeded {args.route_timeout}s; board left unrouted")
        board = pcbnew.LoadBoard(str(board_path)) if "board" not in dir() else board
        gnd = board.FindNet("GND")
        if gnd:
            for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
                z = pcbnew.ZONE(board)
                z.SetLayer(layer)
                z.SetNet(gnd)
                chain = pcbnew.SHAPE_LINE_CHAIN()
                for px, py in ((0.3, 0.3), (W - 0.3, 0.3), (W - 0.3, H - 0.3), (0.3, H - 0.3)):
                    chain.Append(int(mm(px)), int(mm(py)))
                chain.SetClosed(True)
                z.AddPolygon(chain)
                board.Add(z)
            pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.Save(str(board_path))

    # ---- DRC with schematic parity (the board has the root schematic's name)
    drc = out / "drc.json"
    subprocess.run([cli, "pcb", "drc", "--format", "json", "--severity-all", "--schematic-parity",
                    "-o", str(drc), str(board_path)], capture_output=True, text=True, timeout=900)
    if not drc.exists():
        print("ERROR: DRC did not run", file=sys.stderr)
        return 3
    d = json.loads(drc.read_text(encoding="utf-8"))
    v, u, p = d.get("violations", []), d.get("unconnected_items", []), d.get("schematic_parity", [])
    errs = [x for x in v if x.get("severity") == "error"]
    print(f"[drc] {len(errs)} error(s), {len(v) - len(errs)} warning(s), {len(u)} unconnected, "
          f"{len(p)} parity")
    from collections import Counter
    for (sev, typ), n in Counter((x.get("severity"), x.get("type")) for x in v + p).most_common(8):
        print(f"   - {sev} {typ}: {n}")
    print("[note] automatic placement is a starting point: review edges, antennas, decoupling, "
          "thermal and RF before treating this as a layout")
    return 0 if not (v or u or p) else 4


if __name__ == "__main__":
    sys.exit(main())
