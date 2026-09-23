#!/usr/bin/env python3
"""Hardware design document (PDF) for a KiCad project — the "official" deliverable.

Every number in the PDF is measured by kicad-cli while the document is built (ERC,
DRC, schematic parity, board statistics, BOM from the netlist). Nothing is copied
from older reports or guessed. The engineering narrative (purpose, requirements,
theory of operation, calculations, assumptions) comes from a design brief written
by the engineer/agent (see references/design_brief_template.md); without one, the
document says so and lists only facts extracted from the design.

Contents: cover (3D render + verification status) · document control (tool versions,
SHA-256 of every source file) · design description / theory of operation ·
3D schematic (pictorial.py) · full schematic (vector) · PCB (statistics, 3D renders,
vector layer plots) · BOM · verification (ERC/DRC/parity, reviewed exceptions) ·
manufacturing outputs · open issues and limitations.

Usage:
  design_doc.py --sch board.kicad_sch [--pcb board.kicad_pcb] [--brief design_brief.md]
                [--reviewed reviewed.json] [--fab fab.zip] [--step board.step]
                [--title "..."] [--rev A] --out DESIGN.pdf

`reviewed.json` = {"drc_type": "fab-backed justification", ...}: findings of those
types are listed as reviewed exceptions instead of open ones — never silently hidden.
Needs: KiCad 10 (kicad-cli) and `pip install pymupdf` in the python running this.
Exit codes: 0 document written | 2 environment | 3 failed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kicad_paths as kp  # noqa: E402

try:
    import pymupdf
except ImportError:  # pragma: no cover
    pymupdf = None

A4 = (595.0, 842.0)
A4L = (842.0, 595.0)
MARGIN = 50
CSS = """
* { font-family: sans-serif; }
body { font-size: 10pt; line-height: 1.35; color: #1d2530; }
h1 { font-size: 18pt; color: #0b3d6b; margin: 0 0 8px 0; }
h2 { font-size: 13pt; color: #0b3d6b; margin: 14px 0 6px 0; }
h3 { font-size: 11pt; color: #23303d; margin: 10px 0 4px 0; }
p { margin: 0 0 6px 0; }
table { border-collapse: collapse; width: 100%; margin: 4px 0 10px 0; }
th { background-color: #0b3d6b; color: #ffffff; font-size: 8.5pt; text-align: left; padding: 3px; }
td { border-bottom: 1px solid #c8d0d8; font-size: 8.5pt; padding: 3px; vertical-align: top; }
code { font-family: monospace; font-size: 8.5pt; }
.note { color: #5a6570; font-size: 8.5pt; }
.pass { color: #1b7a2f; font-weight: bold; }
.fail { color: #b3261e; font-weight: bold; }
.warn { color: #9a6700; font-weight: bold; }
li { margin-bottom: 2px; }
"""


# ------------------------------------------------------------------ helpers

def cli() -> str:
    return str(kp.kicad_cli())


def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def esc(t: object) -> str:
    return html.escape(str(t), quote=False)


def md_to_html(md: str) -> str:
    """Small Markdown subset: headings, paragraphs, lists, tables, bold/italic/code/links."""
    def inline(t: str) -> str:
        t = esc(t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<![\w*])\*([^*]+)\*(?!\w)", r"<i>\1</i>", t)
        t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", t)
        return t
    out, para, lst, tbl = [], [], None, []

    def flush():
        nonlocal para, lst, tbl
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para = []
        if lst:
            tag, items = lst
            out.append(f"<{tag}>" + "".join(f"<li>{inline(i)}</li>" for i in items) + f"</{tag}>")
            lst = None
        if tbl:
            rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in tbl
                    if not re.match(r"^\s*\|?\s*:?-{2,}", r)]
            if rows:
                h = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
                b = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
                out.append(f"<table><tr>{h}</tr>{b}</table>")
            tbl = []
    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            flush()
            continue
        if s.lstrip().startswith("|"):
            if para or lst:
                flush()
            tbl.append(s)
            continue
        if tbl:
            flush()
        m = re.match(r"^(#{1,4})\s+(.*)", s)
        if m:
            flush()
            lvl = min(len(m.group(1)) + 1, 4)  # brief '#' -> h2 inside a section
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            continue
        m = re.match(r"^\s*([-*]|\d+\.)\s+(.*)", s)
        if m:
            tag = "ol" if m.group(1)[0].isdigit() else "ul"
            if para:
                flush()
            if lst and lst[0] != tag:
                flush()
            lst = lst or (tag, [])
            lst[1].append(m.group(2))
            continue
        if lst and line.startswith("  "):
            lst[1][-1] += " " + s.strip()
            continue
        if lst:
            flush()
        para.append(s.strip())
    flush()
    return "\n".join(out)


def parse_brief(path: Path | None) -> tuple[dict, str]:
    """Optional '---' front matter (key: value) + Markdown body."""
    if not path or not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    meta = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip()
        text = text[m.end():]
    return meta, text


def sheet_files(root: Path) -> list[Path]:
    """Root schematic + every child sheet file (recursive, unique, in order)."""
    seen, order = set(), []

    def walk(p: Path):
        if p in seen or not p.exists():
            return
        seen.add(p)
        order.append(p)
        t = p.read_text(encoding="utf-8", errors="replace")
        for f in re.findall(r'\(property\s+"Sheetfile"\s+"([^"]+)"', t):
            walk((p.parent / f).resolve())
    walk(root.resolve())
    return order


# ------------------------------------------------------------------ measurements

def erc(sch: Path, work: Path) -> dict:
    out = work / "erc.json"
    run([cli(), "sch", "erc", "--format", "json", "--severity-all", "-o", str(out), str(sch)])
    if not out.exists():
        return {"ok": False}
    d = json.loads(out.read_text(encoding="utf-8"))
    items = []
    for sheet in d.get("sheets", []):
        for v in sheet.get("violations", []):
            items.append({"sheet": sheet.get("path", "/"), "type": v.get("type"),
                          "severity": v.get("severity"), "description": v.get("description"),
                          "items": "; ".join(i.get("description", "") for i in v.get("items", []))})
    return {"ok": True, "items": items, "version": d.get("kicad_version", "")}


def drc(pcb: Path, work: Path) -> dict:
    out = work / "drc.json"
    parity = pcb.with_suffix(".kicad_sch").exists()
    cmd = [cli(), "pcb", "drc", "--format", "json", "--severity-all"]
    cmd += ["--schematic-parity"] if parity else []
    run(cmd + ["-o", str(out), str(pcb)])
    if not out.exists():
        return {"ok": False}
    d = json.loads(out.read_text(encoding="utf-8"))

    def flat(lst):
        return [{"type": v.get("type"), "severity": v.get("severity"),
                 "description": v.get("description"),
                 "items": "; ".join(i.get("description", "") for i in v.get("items", []))} for v in lst]
    return {"ok": True, "parity_checked": parity, "violations": flat(d.get("violations", [])),
            "unconnected": flat(d.get("unconnected_items", [])),
            "parity": flat(d.get("schematic_parity", []))}


def netlist(sch: Path, work: Path) -> dict:
    out = work / "net.xml"
    run([cli(), "sch", "export", "netlist", "--format", "kicadxml", "-o", str(out), str(sch)])
    comps, nets = [], []
    if out.exists():
        root = ET.parse(out).getroot()
        for c in root.iter("comp"):
            fields = {f.get("name"): (f.text or "") for f in c.iter("field")}
            lib = c.find("libsource")
            comps.append({"ref": c.get("ref"), "value": (c.findtext("value") or ""),
                          "footprint": (c.findtext("footprint") or ""),
                          "description": (c.findtext("description") or
                                          (lib.get("description") if lib is not None else "") or ""),
                          "lcsc": fields.get("LCSC", "") or fields.get("LCSC Part", ""),
                          "mpn": fields.get("MPN", "") or fields.get("Manufacturer_Part_Number", ""),
                          "dnp": any(p.get("name") == "dnp" for p in c.iter("property"))})
        for n in root.iter("net"):
            nets.append({"name": n.get("name"), "nodes": [(x.get("ref"), x.get("pin")) for x in n.iter("node")]})
    return {"components": comps, "nets": nets}


def board_stats(pcb: Path) -> dict:
    t = pcb.read_text(encoding="utf-8", errors="replace")
    edge = []
    for m in re.finditer(r"\(gr_(?:line|rect|arc|circle|poly)\b", t):
        blk = t[m.start():m.start() + 600]
        if '"Edge.Cuts"' in blk.split("(uuid")[0]:
            edge += [(float(a), float(b)) for a, b in
                     re.findall(r"\((?:start|end|mid|center|xy)\s+([-\d.]+)\s+([-\d.]+)\)", blk.split("(stroke")[0])]
    cu = re.findall(r'\(\d+\s+"([^"]+\.Cu)"\s+(?:signal|power|mixed|jumper)', t)
    widths = [float(w) for w in re.findall(r"\(segment.*?\(width\s+([\d.]+)\)", t, re.S)]
    thick = re.search(r"\(general\s+\(thickness\s+([\d.]+)\)", t)
    return {"size": ((max(x for x, _ in edge) - min(x for x, _ in edge)),
                     (max(y for _, y in edge) - min(y for _, y in edge))) if edge else None,
            "copper_layers": cu, "segments": len(re.findall(r"\(segment\b", t)),
            "arcs": len(re.findall(r"\(arc\b", t)), "vias": len(re.findall(r"\(via\b", t)),
            "zones": len(re.findall(r"\(zone\b", t)), "footprints": len(re.findall(r"\(footprint\s+\"", t)),
            "min_track": min(widths) if widths else None, "thickness": float(thick.group(1)) if thick else None}


# ------------------------------------------------------------------ page builders

def story_pdf(html_body: str, path: Path, archive: Path, size=A4) -> int:
    story = pymupdf.Story(html=f"<body>{html_body}</body>", user_css=CSS, archive=str(archive))
    writer = pymupdf.DocumentWriter(str(path))
    rect = pymupdf.Rect(0, 0, *size)
    where = rect + (MARGIN, MARGIN + 10, -MARGIN, -MARGIN - 10)
    more, pages = True, 0
    while more:
        dev = writer.begin_page(rect)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        pages += 1
    writer.close()
    return pages


def content_rect(page) -> "pymupdf.Rect":
    """Union of everything drawn on a page (vector plots come 1:1 on a full sheet)."""
    r = pymupdf.Rect()
    for _kind, bb in page.get_bboxlog():
        bb = pymupdf.Rect(bb)
        if bb.is_empty or bb.width >= page.rect.width * 0.98 and bb.height >= page.rect.height * 0.98:
            continue  # empty ops and page-sized backgrounds
        r |= bb
    return (r + (-6, -6, 6, 6)) & page.rect if not r.is_empty else page.rect


def figure_pages(doc, title: str, src_pdf: Path, caption: str = "", size=A4L, crop: bool = False,
                 names: list[str] | None = None) -> int:
    """Each page of `src_pdf` scaled onto its own landscape page under a title bar
    (crop=True: zoom onto the drawn content, e.g. 1:1 layer plots)."""
    src = pymupdf.open(str(src_pdf))
    for i, sp in enumerate(src):
        page = doc.new_page(width=size[0], height=size[1])
        label = f" — {names[i]}" if names and i < len(names) else (
            f" ({i + 1}/{src.page_count})" if src.page_count > 1 else "")
        page.insert_text((MARGIN, MARGIN), title + label, fontsize=13, fontname="helv", color=(0.04, 0.24, 0.42))
        box = pymupdf.Rect(MARGIN, MARGIN + 12, size[0] - MARGIN, size[1] - MARGIN - (22 if caption else 10))
        page.draw_rect(box, color=(0.78, 0.82, 0.86), width=0.5)
        page.show_pdf_page(box + (4, 4, -4, -4), src, i, keep_proportion=True,
                           clip=content_rect(sp) if crop else None)
        if caption:
            page.insert_textbox(pymupdf.Rect(MARGIN, size[1] - MARGIN - 20, size[0] - MARGIN, size[1] - MARGIN),
                                caption, fontsize=8, fontname="helv", color=(0.35, 0.4, 0.45))
    n = src.page_count
    src.close()  # Windows: an open document locks its file
    return n


def cropped_png(png: Path, work: Path) -> Path:
    """Render with transparent background -> PNG cropped to the opaque pixels."""
    pix = pymupdf.Pixmap(str(png))
    if not pix.alpha:
        return png
    w, h = pix.width, pix.height
    a = bytes(pix.samples_mv[pix.n - 1::pix.n])
    mask = a.translate(bytes(0 if v < 40 else 1 for v in range(256)))
    rows = [y for y in range(h) if mask.find(1, y * w, (y + 1) * w) >= 0]
    if not rows:
        return png
    x0 = min(mask.find(1, y * w, (y + 1) * w) - y * w for y in rows)
    x1 = max(mask.rfind(1, y * w, (y + 1) * w) - y * w for y in rows)
    clip = pymupdf.IRect(max(x0 - 8, 0), max(rows[0] - 8, 0), min(x1 + 9, w), min(rows[-1] + 9, h))
    out = work / f"crop-{png.name}"
    pymupdf.Pixmap(pix, w, h, clip).save(str(out))
    return out


def board_views(doc, renders: dict[str, Path], work: Path) -> None:
    page = doc.new_page(width=A4L[0], height=A4L[1])
    page.insert_text((MARGIN, MARGIN), "4. Board views (KiCad 3D renders)", fontsize=13, fontname="helv",
                     color=(0.04, 0.24, 0.42))
    W, H = A4L[0] - 2 * MARGIN, A4L[1] - 2 * MARGIN - 40
    slots = {"iso": pymupdf.Rect(MARGIN, MARGIN + 16, MARGIN + W * 0.6, MARGIN + 16 + H),
             "top": pymupdf.Rect(MARGIN + W * 0.62, MARGIN + 16, MARGIN + W, MARGIN + 16 + H / 2 - 8),
             "bottom": pymupdf.Rect(MARGIN + W * 0.62, MARGIN + 16 + H / 2 + 8, MARGIN + W, MARGIN + 16 + H)}
    caps = {"iso": "3D view", "top": "Top side", "bottom": "Bottom side"}
    for k, rect in slots.items():
        if k not in renders:
            continue
        page.insert_image(rect - (0, 0, 0, 12), filename=str(cropped_png(renders[k], work)), keep_proportion=True)
        page.insert_text((rect.x0, rect.y1 - 2), caps[k], fontsize=8.5, fontname="helv", color=(0.35, 0.4, 0.45))


def cover(doc, meta: dict, hero: Path | None, status_html: list[tuple[str, str]]) -> None:
    page = doc.new_page(width=A4[0], height=A4[1])
    page.draw_rect(pymupdf.Rect(0, 0, A4[0], 150), color=None, fill=(0.04, 0.24, 0.42))
    page.insert_text((MARGIN, 70), meta["title"], fontsize=24, fontname="hebo", color=(1, 1, 1))
    page.insert_text((MARGIN, 98), "Hardware design document", fontsize=14, fontname="helv", color=(0.85, 0.9, 0.95))
    page.insert_text((MARGIN, 126), f"Revision {meta['rev']}  ·  {meta['date']}  ·  {meta['status']}",
                     fontsize=10, fontname="helv", color=(0.85, 0.9, 0.95))
    if hero and hero.exists():
        page.insert_image(pymupdf.Rect(MARGIN, 170, A4[0] - MARGIN, 520),
                          filename=str(cropped_png(hero, hero.parent)), keep_proportion=True)
    y = 550
    page.insert_text((MARGIN, y), "Verification at document generation", fontsize=12, fontname="hebo",
                     color=(0.04, 0.24, 0.42))
    for label, value in status_html:
        y += 18
        page.insert_text((MARGIN + 6, y), label, fontsize=10, fontname="helv", color=(0.2, 0.25, 0.3))
        page.insert_text((MARGIN + 190, y), value, fontsize=10, fontname="hebo",
                         color=(0.1, 0.48, 0.18) if value.startswith(("PASS", "0 ")) else (0.7, 0.15, 0.12)
                         if value.startswith(("FAIL", "OPEN")) else (0.2, 0.25, 0.3))
    page.insert_textbox(pymupdf.Rect(MARGIN, A4[1] - 120, A4[0] - MARGIN, A4[1] - 50),
                        meta["disclaimer"], fontsize=8.5, fontname="helv", color=(0.35, 0.4, 0.45))


def stamp(doc, title: str, rev: str) -> None:
    n = doc.page_count
    for i, page in enumerate(doc):
        if i == 0:
            continue
        w, h = page.rect.width, page.rect.height
        page.draw_line((MARGIN, h - 32), (w - MARGIN, h - 32), color=(0.78, 0.82, 0.86), width=0.5)
        page.insert_text((MARGIN, h - 20), f"{title} — hardware design document — rev {rev}",
                         fontsize=7.5, fontname="helv", color=(0.4, 0.45, 0.5))
        page.insert_text((w - MARGIN - 50, h - 20), f"page {i + 1} / {n}", fontsize=7.5,
                         fontname="helv", color=(0.4, 0.45, 0.5))


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sch", type=Path, required=True, help="root .kicad_sch")
    ap.add_argument("--pcb", type=Path, help=".kicad_pcb (omit when no layout exists yet)")
    ap.add_argument("--brief", type=Path, help="design brief (Markdown, see references/design_brief_template.md)")
    ap.add_argument("--reviewed", type=Path, help="JSON {drc_or_erc_type: justification}")
    ap.add_argument("--fab", type=Path, help="fabrication zip (gerbers/drill) to list")
    ap.add_argument("--step", type=Path, help="board STEP to reference")
    ap.add_argument("--title")
    ap.add_argument("--rev")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if pymupdf is None:
        print("ERROR: this script needs pymupdf in the running python: pip install pymupdf", file=sys.stderr)
        return 2
    sch = args.sch.resolve()
    pcb = args.pcb.resolve() if args.pcb else None
    meta_b, body = parse_brief(args.brief)
    reviewed = json.loads(args.reviewed.read_text(encoding="utf-8")) if args.reviewed else {}
    reviewed.update({k[len("reviewed "):]: v for k, v in meta_b.items() if k.startswith("reviewed ")})
    version = run([cli(), "version"]).stdout.strip() or "unknown"
    today = dt.date.today().isoformat()
    meta = {"title": args.title or meta_b.get("title") or sch.stem.replace("-", " ").title(),
            "rev": args.rev or meta_b.get("revision", "draft"), "date": today,
            "status": meta_b.get("status", "ENGINEERING DRAFT"),
            "disclaimer": meta_b.get("disclaimer", "Generated headless by the kicad-pcb skill. Every "
                                     "verification figure was measured by kicad-cli while this document was "
                                     "built. A clean ERC/DRC report proves rule consistency only: it does not "
                                     "certify electrical safety, RF performance, thermal behaviour or "
                                     "manufacturability. Independent engineering review and the project's "
                                     "release gate are required before ordering hardware.")}
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            work = Path(td)
            print(f"[doc] measuring: ERC{', DRC, parity' if pcb else ''} ...")
            e = erc(sch, work)
            d = drc(pcb, work) if pcb else None
            nl = netlist(sch, work)
            sheets = sheet_files(sch)
            stats = board_stats(pcb) if pcb else None

            # ---------- figures
            pdf_sch = work / "schematic.pdf"
            run([cli(), "sch", "export", "pdf", "-o", str(pdf_sch), str(sch)])
            pict_pdfs = []
            for sf in sheets:
                if "(lib_id" not in sf.read_text(encoding="utf-8", errors="replace"):
                    continue
                svg = work / f"pict-{sf.stem}.svg"
                r = run([sys.executable, str(Path(__file__).with_name("pictorial.py")), str(sf), "--out", str(svg)],
                        timeout=1800)
                if svg.exists():
                    p = work / f"pict-{sf.stem}.pdf"
                    with pymupdf.open(str(svg)) as sd, pymupdf.open("pdf", sd.convert_to_pdf()) as pd:
                        pd.save(str(p))
                    pict_pdfs.append((sf.stem, p))
                else:
                    print(f"[doc] 3D schematic skipped for {sf.name}: {(r.stderr or r.stdout)[-200:]}")
            renders = {}
            layer_pdf = None
            layers: list[str] = []
            if pcb:
                for name, opts in (("iso", ["--rotate", "-50,0,35", "--perspective"]), ("top", ["--side", "top"]),
                                   ("bottom", ["--side", "bottom"])):
                    png = work / f"render-{name}.png"
                    run([cli(), "pcb", "render", *opts, "--quality", "high", "--width", "1600",
                         "--height", "1100", "-o", str(png), str(pcb)], timeout=1800)
                    if png.exists():
                        renders[name] = png
                layers = stats["copper_layers"] + ["F.Silkscreen", "B.Silkscreen", "F.Fab"]
                layer_pdf = work / "layers.pdf"
                run([cli(), "pcb", "export", "pdf", "--mode-multipage", "--sp", "-l", ",".join(layers),
                     "--cl", "Edge.Cuts", "-o", str(layer_pdf), str(pcb)])
                if not layer_pdf.exists():
                    layer_pdf = None

            # ---------- verification bookkeeping
            e_items = e.get("items", []) if e.get("ok") else []
            e_rev = [i for i in e_items if i["type"] in reviewed]
            e_open = [i for i in e_items if i["type"] not in reviewed]
            if d and d.get("ok"):
                d_rev = [v for v in d["violations"] if v["type"] in reviewed and v["severity"] != "error"]
                d_open = [v for v in d["violations"] if v not in d_rev]
            else:
                d_rev, d_open = [], []
            erc_state = ("n/a" if not e.get("ok") else "PASS (0 open)" if not e_open
                         else f"OPEN ({len(e_open)} finding(s))")
            if not pcb:
                drc_state, par_state, unc_state = "no PCB layout yet", "no PCB layout yet", "no PCB layout yet"
            elif not d.get("ok"):
                drc_state = par_state = unc_state = "DRC did not run"
            else:
                drc_state = "PASS (0 open)" if not d_open else f"FAIL ({len(d_open)} open)"
                unc_state = f"{len(d['unconnected'])} unconnected" if d["unconnected"] else "0 unconnected"
                par_state = ("not checked (no schematic named like the board)" if not d["parity_checked"]
                             else "0 differences" if not d["parity"] else f"FAIL ({len(d['parity'])} differences)")
            status = [("ERC", erc_state), ("DRC", drc_state), ("Connectivity", unc_state),
                      ("Schematic <-> board parity", par_state),
                      ("Reviewed exceptions", f"{len(e_rev) + len(d_rev)} (justified in section 6)")]

            # ---------- text sections
            H = {}
            files = [sch] + [s for s in sheets if s != sch] + ([pcb] if pcb else [])
            H["control"] = (
                "<h1>Document control</h1><table><tr><th>Item</th><th>Value</th></tr>"
                f"<tr><td>Title</td><td>{esc(meta['title'])}</td></tr>"
                f"<tr><td>Revision / status</td><td>{esc(meta['rev'])} / {esc(meta['status'])}</td></tr>"
                f"<tr><td>Generated</td><td>{today} by the kicad-pcb skill (design_doc.py)</td></tr>"
                f"<tr><td>Tools</td><td>KiCad {esc(version)} (kicad-cli)</td></tr></table>"
                "<h2>Source files</h2><table><tr><th>File</th><th>SHA-256 (first 16)</th></tr>"
                + "".join(f"<tr><td><code>{esc(f.name)}</code></td><td><code>{sha(f)}</code></td></tr>"
                          for f in files if f and f.exists())
                + "</table><p class='note'>The hashes tie this document to the exact design files it was "
                  "generated from. Regenerate the document after any change.</p>"
                "<h2>Contents</h2><ol><li>Design description and theory of operation</li>"
                "<li>3D schematic</li><li>Schematic</li><li>Printed circuit board</li>"
                "<li>Bill of materials</li><li>Verification</li><li>Manufacturing outputs</li>"
                "<li>Open issues and limitations</li></ol>")
            comps = [c for c in nl["components"] if not c["ref"].startswith("#")]
            power_nets = sorted({n["name"] for n in nl["nets"]
                                 if re.match(r"^(\+|-)?\d|^(GND|VCC|VDD|VBUS|VIN|VBAT)", n["name"].lstrip("/"))})
            if body.strip():
                desc = "<h1>1. Design description and theory of operation</h1>" + md_to_html(body)
            else:
                desc = ("<h1>1. Design description</h1><p class='warn'>No design brief was supplied. "
                        "This section lists only facts extracted from the design; the engineering "
                        "narrative (purpose, requirements, theory of operation, calculations, assumptions) "
                        "must be written in a design brief (references/design_brief_template.md).</p>")
            desc += ("<h2>Design facts (extracted from the netlist)</h2><table><tr><th>Item</th><th>Value</th></tr>"
                     f"<tr><td>Components</td><td>{len(comps)}</td></tr>"
                     f"<tr><td>Nets</td><td>{len(nl['nets'])}</td></tr>"
                     f"<tr><td>Sheets</td><td>{len(sheets)}</td></tr>"
                     f"<tr><td>Supply / ground nets</td><td>{esc(', '.join(power_nets) or '—')}</td></tr></table>")
            H["desc"] = desc

            pcb_html = "<h1>4. Printed circuit board</h1>"
            if not pcb:
                pcb_html += "<p class='warn'>No PCB layout exists yet for this design.</p>"
            else:
                sz = stats["size"]
                pcb_html += ("<table><tr><th>Property</th><th>Value</th></tr>"
                             f"<tr><td>Outline</td><td>{f'{sz[0]:.2f} x {sz[1]:.2f} mm' if sz else 'no Edge.Cuts outline'}</td></tr>"
                             f"<tr><td>Copper layers</td><td>{len(stats['copper_layers'])} ({esc(', '.join(stats['copper_layers']))})</td></tr>"
                             f"<tr><td>Board thickness</td><td>{stats['thickness'] or '—'} mm</td></tr>"
                             f"<tr><td>Footprints</td><td>{stats['footprints']}</td></tr>"
                             f"<tr><td>Track segments / arcs / vias / zones</td><td>{stats['segments']} / {stats['arcs']} / {stats['vias']} / {stats['zones']}</td></tr>"
                             f"<tr><td>Narrowest track</td><td>{stats['min_track'] if stats['min_track'] else '—'} mm</td></tr></table>")
                pcb_html += ("<p class='note'>Board views and vector layer plots follow on the next pages "
                             "(KiCad raytraced renders with the footprints' own 3D models).</p>")
            H["pcb"] = pcb_html

            groups = defaultdict(list)
            for c in comps:
                groups[(c["value"], c["footprint"], c["lcsc"], c["mpn"], c["description"], c["dnp"])].append(c["ref"])
            has_l = any(k[2] for k in groups)
            has_m = any(k[3] for k in groups)

            def refkey(r):
                m = re.match(r"([A-Za-z#]+)(\d+)", r)
                return (m.group(1), int(m.group(2))) if m else (r, 0)
            bom = ("<h1>5. Bill of materials</h1><p class='note'>Grouped from the schematic netlist. "
                   "Supplier columns appear only when the schematic carries the field (LCSC / MPN); no "
                   "part number or stock class is inferred.</p><table><tr><th>Qty</th><th>References</th>"
                   "<th>Value</th><th>Footprint</th>" + ("<th>LCSC</th>" if has_l else "")
                   + ("<th>MPN</th>" if has_m else "") + "<th>Description</th></tr>")
            for k, refs in sorted(groups.items(), key=lambda kv: refkey(sorted(kv[1], key=refkey)[0])):
                refs = sorted(refs, key=refkey)
                bom += (f"<tr><td>{len(refs)}{' DNP' if k[5] else ''}</td><td>{esc(', '.join(refs))}</td>"
                        f"<td>{esc(k[0])}</td><td>{esc(k[1].split(':')[-1])}</td>"
                        + (f"<td>{esc(k[2]) or '—'}</td>" if has_l else "")
                        + (f"<td>{esc(k[3]) or '—'}</td>" if has_m else "")
                        + f"<td>{esc(k[4][:70])}</td></tr>")
            H["bom"] = bom + "</table>"

            def vtable(rows, empty):
                if not rows:
                    return f"<p class='pass'>{empty}</p>"
                c = Counter((r["severity"], r["type"]) for r in rows)
                t = "<table><tr><th>Severity</th><th>Type</th><th>Count</th><th>Example</th></tr>"
                for (sev, typ), n in sorted(c.items()):
                    ex = next(r for r in rows if r["type"] == typ and r["severity"] == sev)
                    t += (f"<tr><td>{esc(sev)}</td><td><code>{esc(typ)}</code></td><td>{n}</td>"
                          f"<td>{esc((ex['description'] or '') + ' — ' + ex['items'])[:160]}</td></tr>")
                return t + "</table>"
            ver = "<h1>6. Verification</h1><table><tr><th>Check</th><th>Result</th></tr>" + "".join(
                f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in status) + "</table>"
            ver += "<h2>ERC (all severities)</h2>" + (vtable(e_open, "No open ERC findings.") if e.get("ok")
                                                        else "<p class='fail'>ERC did not run.</p>")
            if pcb and d and d.get("ok"):
                ver += "<h2>DRC (all severities)</h2>" + vtable(d_open, "No open DRC findings.")
                ver += "<h2>Unconnected items</h2>" + vtable(d["unconnected"], "None.")
                ver += "<h2>Schematic parity</h2>" + (
                    vtable(d["parity"], "Board matches the schematic.") if d["parity_checked"]
                    else "<p class='warn'>Not checked: no schematic with the board's file name.</p>")
            ver += "<h2>Reviewed exceptions</h2>"
            if e_rev or d_rev:
                ver += "<table><tr><th>Type</th><th>Count</th><th>Justification (recorded by the reviewer)</th></tr>"
                for typ, n in sorted(Counter(r["type"] for r in e_rev + d_rev).items()):
                    ver += f"<tr><td><code>{esc(typ)}</code></td><td>{n}</td><td>{esc(reviewed[typ])}</td></tr>"
                ver += "</table>"
            else:
                ver += "<p>None.</p>"
            ver += ("<p class='note'>ERC/DRC measure rule consistency against the project's design rules. "
                    "They do not replace the engineering review of power, RF, thermal and mechanical "
                    "behaviour listed in section 8.</p>")
            H["ver"] = ver

            man = "<h1>7. Manufacturing outputs</h1>"
            if args.fab and args.fab.exists():
                import zipfile
                with zipfile.ZipFile(args.fab) as z:
                    names = z.namelist()
                man += (f"<p>Fabrication archive <code>{esc(args.fab.name)}</code> (SHA-256 {sha(args.fab)}), "
                        f"{len(names)} files:</p><ul>" + "".join(f"<li><code>{esc(n)}</code></li>" for n in names) + "</ul>")
            else:
                man += "<p class='warn'>No fabrication archive was supplied to this document.</p>"
            if args.step and args.step.exists():
                man += f"<p>Board 3D model: <code>{esc(args.step.name)}</code> (SHA-256 {sha(args.step)}).</p>"
            man += ("<p class='note'>Check every output against the selected fabricator's current "
                    "capabilities (references/jlcpcb-rules.md for the JLCPCB class) before ordering.</p>")
            H["man"] = man

            opn = "<h1>8. Open issues and limitations</h1>"
            m = re.search(r"^#+\s*(open issues|limitations|open questions)[^\n]*\n(.*?)(?=^#\s|\Z)", body, re.S | re.M | re.I)
            opn += md_to_html(m.group(2)) if m else "<p>No open issues were listed in the design brief.</p>"
            auto = []
            if e_open:
                auto.append(f"{len(e_open)} open ERC finding(s) — see section 6.")
            if pcb and d and d.get("ok") and (d_open or d["unconnected"] or d["parity"]):
                auto.append(f"DRC: {len(d_open)} open finding(s), {len(d['unconnected'])} unconnected, "
                            f"{len(d['parity'])} parity difference(s).")
            if not pcb:
                auto.append("No PCB layout exists yet.")
            if not body.strip():
                auto.append("No design brief: theory of operation, calculations and assumptions are undocumented.")
            if auto:
                opn += "<h2>Detected automatically</h2><ul>" + "".join(f"<li>{esc(a)}</li>" for a in auto) + "</ul>"
            opn += f"<p class='note'>{esc(meta['disclaimer'])}</p>"
            H["open"] = opn

            # ---------- assemble
            doc = pymupdf.open()
            toc = []
            cover(doc, meta, renders.get("iso"), status)
            toc.append([1, "Cover", 1])

            def add_story(key, label):
                p = work / f"{key}.pdf"
                story_pdf(H[key], p, work)
                start = doc.page_count + 1
                with pymupdf.open(str(p)) as part:
                    doc.insert_pdf(part)
                toc.append([1, label, start])
            add_story("control", "Document control")
            add_story("desc", "1. Design description")
            start = doc.page_count + 1
            for stem, p in pict_pdfs:
                figure_pages(doc, f"2. 3D schematic — {stem}", p, caption=
                             "Parts drawn with their footprint's 3D model (KiCad renders). Wiring, labels and "
                             "power symbols are the schematic itself.")
            if pict_pdfs:
                toc.append([1, "2. 3D schematic", start])
            if pdf_sch.exists():
                start = doc.page_count + 1
                figure_pages(doc, "3. Schematic", pdf_sch)
                toc.append([1, "3. Schematic", start])
            add_story("pcb", "4. Printed circuit board")
            if renders:
                toc.append([2, "Board views", doc.page_count + 1])
                board_views(doc, renders, work)
            if layer_pdf:
                start = doc.page_count + 1
                figure_pages(doc, "4. Layer plot", layer_pdf, "Vector plot from kicad-cli (board outline "
                             "on every page); zoomed to the board.", crop=True, names=layers)
                toc.append([2, "Layer plots", start])
            add_story("bom", "5. Bill of materials")
            add_story("ver", "6. Verification")
            add_story("man", "7. Manufacturing outputs")
            add_story("open", "8. Open issues and limitations")
            stamp(doc, meta["title"], meta["rev"])
            doc.set_toc(toc)
            doc.set_metadata({"title": f"{meta['title']} — hardware design document",
                              "subject": f"rev {meta['rev']}", "creator": "kicad-pcb skill design_doc.py",
                              "producer": f"KiCad {version} + PyMuPDF"})
            args.out.parent.mkdir(parents=True, exist_ok=True)
            doc.save(str(args.out), garbage=3, deflate=True)
            doc.close()
    except kp.ResolveError as err:
        print(f"ERROR [{err.component}]: {err.fix}", file=sys.stderr)
        return 2
    with pymupdf.open(str(args.out)) as done:
        pages = done.page_count
    print(f"[doc] {args.out} ({pages} pages) | "
          + " | ".join(f"{a}: {b}" for a, b in status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
