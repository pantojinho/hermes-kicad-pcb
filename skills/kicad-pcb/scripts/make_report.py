#!/usr/bin/env python3
"""Generate the standard project deliverable document (REPORT.md) for a board.

Encodes the kicad-pcb deliverables contract: every board project — KiCad-only or
KiCad+EasyEDA — ships the SAME software-independent package:
  1. validated design (.kicad_sch/.kicad_pcb/.kicad_pro)
  2. board-level 3D (STEP with package-verified models)
  3. REPORT.md with the prints: schematic figure, 3D renders, fabrication BOM,
     validation summary (this script)
  4. fabrication pack (gerbers/drill/CPL/BOM)

Usage:
  make_report.py --project DIR [--out REPORT.md] [--title "My Board"]
                 [--lcsc U1=C82899 --lcsc R1=C21190 ...]
                 [--renders DIR] [--step PATH] [--fab ZIP]
                 [--drc JSON] [--erc JSON]

The prints are produced by kicad-cli (renders + schematic SVG) when missing.
LCSC numbers are printed as supplied; stock class (Basic/Extended) and part/footprint
match are NOT verified here — check them on JLCPCB before ordering. For the full,
measured PDF deliverable use design_doc.py.
Exit codes: 0 ok | 2 bad args/env | 3 generation failed.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import date
from pathlib import Path



def sh(cmd: list[str]) -> bool:
    print("[exec]", " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    return r.returncode == 0


def find_board(project: Path) -> tuple[Path, Path]:
    schs = sorted(project.glob("*.kicad_sch"))
    pcbs = sorted(project.glob("*.kicad_pcb"))
    if not schs or not pcbs:
        raise SystemExit(f"ERROR: need a .kicad_sch and a .kicad_pcb under {project}")
    return schs[0], pcbs[0]


def render_prints(kp, sch: Path, pcb: Path, renders: Path) -> dict[str, Path]:
    renders.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    # `sch export svg --output` is a DIRECTORY (one SVG per sheet), not a file
    sch_dir = renders / "schematic-svg"
    if not list(sch_dir.glob("*.svg")):
        sh([str(kp.kicad_cli()), "sch", "export", "svg", "--output", str(sch_dir), str(sch)])
        sh([str(kp.kicad_cli()), "sch", "export", "pdf", "--output", str(renders / "schematic.pdf"), str(sch)])
    svgs = sorted(sch_dir.glob("*.svg"))
    out["schematic"] = svgs[0] if svgs else renders / "schematic.pdf"
    for side in ("top", "bottom"):
        png = renders / f"board-3d-{side}.png"
        if not png.exists():
            sh([str(kp.kicad_cli()), "pcb", "render", "--output", str(png), "--side", side,
                "--width", "1600", "--height", "1200", "--quality", "high", str(pcb)])
        out[side] = png
    return out


def bom_table(sch: Path, renders: Path, lcsc: dict[str, str]) -> str:
    csv_path = renders / "bom.csv"
    sh([str(kp.kicad_cli()), "sch", "export", "bom",
        "--fields", "Reference,Value,Footprint", "--labels", "Refs,Value,Package",
        "--group-by", "Value", "--output", str(csv_path), str(sch)])
    rows = ["| Qty | Refs | Value | Package | LCSC (as supplied, unverified) |", "|---|---|---|---|---|"]
    try:
        with open(csv_path, newline="") as f:
            for rec in csv.DictReader(f):
                refs = rec.get("Refs", "")
                first = refs.split(",")[0].strip()
                code = lcsc.get(first, lcsc.get(refs, ""))
                pkg = (rec.get("Package", "") or "").split(":")[-1]
                qty = len([r for r in refs.split(",") if r.strip()])
                rows.append(f"| {qty} | {refs} | {rec.get('Value','')} | {pkg} | {code or '—'} |")
    except FileNotFoundError:
        rows.append("| — | — | — | — | — |")
    return "\n".join(rows)


def check_summary(drc: Path | None, erc: Path | None) -> str:
    lines = []
    for name, p in (("DRC", drc), ("ERC", erc)):
        if not p or not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        # ERC JSON nests findings per sheet; DRC JSON has them at the top level
        v = d.get("violations", []) + [x for s in d.get("sheets", []) for x in s.get("violations", [])]
        u = d.get("unconnected_items", [])
        pa = d.get("schematic_parity", [])
        lines.append(f"- **{name}**: {len(v)} violation(s), {len(u)} unconnected, {len(pa)} parity"
                     + (f" — types: {', '.join(sorted({x.get('type','?') for x in v}))}" if v else " — clean"))
    return "\n".join(lines) or "- (no DRC/ERC json supplied — run the pipeline first)"


def main() -> int:
    global kp
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True, help="dir with .kicad_sch + .kicad_pcb")
    ap.add_argument("--out", default=None, help="report path (default: PROJECT/REPORT.md)")
    ap.add_argument("--title", default=None)
    ap.add_argument("--lcsc", action="append", default=[], metavar="REF=Cxxxxx")
    ap.add_argument("--renders", default=None)
    ap.add_argument("--step", default=None, help="board STEP to link")
    ap.add_argument("--fab", default=None, help="gerber/drill/CPL zip to link")
    ap.add_argument("--drc", default=None)
    ap.add_argument("--erc", default=None)
    args = ap.parse_args()

    project = Path(args.project).expanduser().resolve()
    out = Path(args.out).expanduser().resolve() if args.out else project / "REPORT.md"
    renders = Path(args.renders).expanduser().resolve() if args.renders else project / "renders"
    lcsc = dict(e.split("=", 1) for e in args.lcsc if "=" in e)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kicad_paths as kp  # noqa: E403

    sch, pcb = find_board(project)
    title = args.title or pcb.stem.replace("-", " ").title()
    prints = render_prints(kp, sch, pcb, renders)

    def rel(p: Path) -> str:
        try:
            return p.relative_to(out.parent).as_posix()  # links must work on GitHub too
        except ValueError:
            return p.as_posix()

    parts = [f"# {title} — project report", "",
             f"*Generated by the kicad-pcb skill deliverables contract — {date.today().isoformat()}. "
             "Same package for KiCad-only or KiCad+EasyEDA projects.*", "",
             "## Schematic", f"![schematic]({rel(prints['schematic'])})", "",
             "## Board (3D)", f"![top]({rel(prints['top'])})", "",
             f"![bottom]({rel(prints['bottom'])})", ""]
    if args.step and Path(args.step).exists():
        parts += [f"- Board 3D model (STEP): `{rel(Path(args.step))}`", ""]
    parts += ["## Bill of Materials (fabrication-ready)", bom_table(sch, renders, lcsc), "",
              "## Validation", check_summary(Path(args.drc) if args.drc else None,
                                             Path(args.erc) if args.erc else None), ""]
    if args.fab and Path(args.fab).exists():
        parts += ["## Fabrication pack", f"- `{rel(Path(args.fab))}` (gerbers + drill + CPL + BOM CSV)", ""]
    parts += ["## Review checklist (before ordering)",
              "- [ ] Visual feedback loop done: connectors at board edge, antenna keepout clear,",
              "      rotations aligned, decoupling at power pins (compare against real boards)",
              "- [ ] Footprint/pin-1/polarity verified against datasheets",
              "- [ ] Reviewed DRC exceptions documented with fab justification",
              "- [ ] BOM LCSC numbers match the ordered parts", ""]
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"[ok] report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
