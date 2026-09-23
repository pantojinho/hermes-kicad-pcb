#!/usr/bin/env python3
"""3D schematic of any KiCad 10 sheet — real 3D part renders drawn into the schematic.

The EasyEDA "3D schematic" look, generated headless from the project's own files:
  * every placed symbol that has a footprint gets a render of that footprint's 3D
    model (kicad-cli pcb render on a hidden 0.1 mm board, transparent background);
  * two-terminal parts (R, C, L, LED, diode, fuse) are drawn AS the 3D part between
    their leads, pin 1 on pin 1; other small parts (headers, SOT-23...) get the 3D
    part in place of the symbol body;
  * ICs, modules and large connectors keep their symbol (pin names stay readable)
    and get the 3D part as a translucent layer over the body;
  * wires, labels, power symbols and fields are KiCad's own vector SVG.
Parts without a footprint or without a 3D model keep their plain symbol.

Usage:
  pictorial.py SHEET.kicad_sch --out 3d-schematic.svg [--png 3d-schematic.png]

Runs in KiCad's Python (re-execs itself when `import pcbnew` fails).
Exit codes: 0 ok | 2 environment | 3 failed.
"""
from __future__ import annotations

import argparse
import base64
import math
import re
import struct
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kicad_paths as kp  # noqa: E402
import sch_gen as sg  # noqa: E402

TILT = "-40,0,0"          # camera: tilt about X keeps the part's X axis horizontal
GHOST = 0.35              # opacity of the 3D overlay on IC/module/connector bodies
THUMB_W, THUMB_H = 520, 400


# ------------------------------------------------------------------ sheet parsing

@dataclass
class LibPin:
    number: str
    name: str
    x: float
    y: float
    angle: int        # library angle: direction from the tip INTO the body (y up)
    length: float
    font: float


@dataclass
class LibGeo:
    pins: list[LibPin]
    gbox: tuple[float, float, float, float]   # graphics only, library coords (y up)
    names_hidden: bool
    name_offset: float


@dataclass
class Inst:
    lib_id: str
    ref: str
    footprint: str
    x: float
    y: float
    rot: int
    mirror: str
    unit: int
    geo: LibGeo | None = None
    tips: list[tuple[float, float]] = field(default_factory=list)

    def T(self, lx: float, ly: float) -> tuple[float, float]:
        """Library point (y up) -> sheet point (y down): rotate CCW, then mirror."""
        vx, vy = lx, -ly
        a = math.radians(self.rot)
        c, s = round(math.cos(a), 9), round(math.sin(a), 9)
        vx, vy = vx * c + vy * s, -vx * s + vy * c
        if self.mirror == "x":
            vy = -vy
        elif self.mirror == "y":
            vx = -vx
        return self.x + vx, self.y + vy

    def box(self, b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        pts = [self.T(b[0], b[1]), self.T(b[2], b[3]), self.T(b[0], b[3]), self.T(b[2], b[1])]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)


def _num(t: str) -> float:
    return float(t)


def lib_geometry(block: str, unit: int) -> LibGeo:
    """Pins + graphics box of one unit (plus the shared unit 0), body style 1."""
    name = re.match(r'\(symbol\s+"([^"]+)"', block).group(1).split(":")[-1]
    subs = []
    for m in re.finditer(r'\(symbol\s+"%s_(\d+)_(\d+)"' % re.escape(name), block):
        u, st = int(m.group(1)), int(m.group(2))
        if u in (0, unit) and st in (0, 1):
            subs.append(sg.balanced(block, m.start()))
    if not subs:  # flat symbol (rare)
        subs = [block]
    pins, pts = [], []
    for sub in subs:
        for m in re.finditer(r"\(pin\s+\w+\s+\w+", sub):
            pb = sg.balanced(sub, m.start())
            at = re.search(r"\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)", pb)
            num = re.search(r'\(number\s+"([^"]*)"', pb)
            nam = re.search(r'\(name\s+"([^"]*)"', pb)
            ln = re.search(r"\(length\s+([-\d.]+)\)", pb)
            fs = re.search(r"\(name\s+\"[^\"]*\"\s+\(effects\s+\(font\s+\(size\s+([-\d.]+)", pb)
            if at and num:
                pins.append(LibPin(num.group(1), nam.group(1) if nam else "", _num(at.group(1)),
                                   _num(at.group(2)), int(float(at.group(3) or 0)) % 360,
                                   _num(ln.group(1)) if ln else 0.0, _num(fs.group(1)) if fs else 1.27))
        geo = sg.strip_blocks(sg.strip_blocks(sub, "(pin "), "(property ")
        geo = sg.strip_blocks(geo, "(text ")
        for m in re.finditer(r"\((?:start|end|xy|mid)\s+([-\d.]+)\s+([-\d.]+)\)", geo):
            pts.append((_num(m.group(1)), _num(m.group(2))))
        for m in re.finditer(r"\(center\s+([-\d.]+)\s+([-\d.]+)\)\s*\(radius\s+([-\d.]+)\)", geo):
            cx, cy, r = _num(m.group(1)), _num(m.group(2)), _num(m.group(3))
            pts += [(cx - r, cy - r), (cx + r, cy + r)]
    if not pts:  # no body graphics: use the pin inner ends
        for p in pins:
            dx, dy = math.cos(math.radians(p.angle)), math.sin(math.radians(p.angle))
            pts.append((p.x + dx * p.length, p.y + dy * p.length))
    xs, ys = [p[0] for p in pts] or [0.0], [p[1] for p in pts] or [0.0]
    head = block[:600]
    hidden = bool(re.search(r"\(pin_names[^)]*\bhide\b|\(pin_names\s*(\(offset[^)]*\))?\s*\(hide yes\)", head))
    off = re.search(r"\(pin_names\s*\(offset\s+([-\d.]+)\)", head)
    return LibGeo(pins, (min(xs), min(ys), max(xs), max(ys)), hidden, _num(off.group(1)) if off else 0.508)


def parse_sheet(path: Path) -> tuple[list[Inst], list[tuple[float, float]], list[tuple[float, float]]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    libs: dict[str, str] = {}
    lm = re.search(r"\(lib_symbols", text)
    if lm:
        lib_block = sg.balanced(text, lm.start())
        i = 1
        while (m := re.search(r'\(symbol\s+"([^"]+)"', lib_block[i:])):
            start = i + m.start()
            blk = sg.balanced(lib_block, start)
            libs[m.group(1)] = blk
            i = start + len(blk)
        text = text.replace(lib_block, "")
    insts: list[Inst] = []
    fields: list[tuple[float, float]] = []
    for m in re.finditer(r"\(symbol\s+\(lib_id\s+\"([^\"]+)\"\)", text):
        blk = sg.balanced(text, m.start())
        at = re.search(r"\(at\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\)", blk)
        mir = re.search(r"\(mirror\s+(\w)\)", blk)
        unit = re.search(r"\(unit\s+(\d+)\)", blk)
        prop = dict(re.findall(r'\(property\s+"([^"]+)"\s+"([^"]*)"', blk))
        inst = Inst(m.group(1), prop.get("Reference", "?"), prop.get("Footprint", ""),
                    _num(at.group(1)), _num(at.group(2)), int(float(at.group(3))) % 360,
                    mir.group(1) if mir else "", int(unit.group(1)) if unit else 1)
        if inst.lib_id in libs:
            inst.geo = lib_geometry(libs[inst.lib_id], inst.unit)
            inst.tips = [inst.T(p.x, p.y) for p in inst.geo.pins]
        for pm in re.finditer(r'\(property\s+"[^"]+"\s+"([^"]*)"\s+\(at\s+([-\d.]+)\s+([-\d.]+)', blk):
            tail = blk[pm.end():pm.end() + 200]
            if not pm.group(1) or re.match(r"[^()]*\)\s*\(effects[^)]*\)[^)]*\(hide yes\)", tail) \
                    or "(hide yes)" in tail.split("(property")[0][:160]:
                continue
            fx, fy, w = _num(pm.group(2)), _num(pm.group(3)), len(pm.group(1)) * 1.1
            fields.append((fx - w, fy - 1.5))
            fields.append((fx + w, fy + 1.5))
        insts.append(inst)
    # connection points drawn by KiCad itself: wire ends, labels, no-connects, junctions
    conn: list[tuple[float, float]] = []
    extent: list[tuple[float, float]] = []
    for m in re.finditer(r"\((?:wire|bus|polyline)\s+\(pts((?:\s*\(xy\s+[-\d.]+\s+[-\d.]+\))+)", text):
        for x, y in re.findall(r"\(xy\s+([-\d.]+)\s+([-\d.]+)\)", m.group(1)):
            conn.append((_num(x), _num(y)))
    for m in re.finditer(r"\((?:label|global_label|hierarchical_label|no_connect|junction|text|sheet)\s"
                         r"[^()]*\(at\s+([-\d.]+)\s+([-\d.]+)", text):
        conn.append((_num(m.group(1)), _num(m.group(2))))
    extent += conn + fields
    return insts, conn, extent


# ------------------------------------------------------------------ PNG helpers (no deps)

def _png_rgba(data: bytes) -> tuple[int, int, list[bytearray]]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    i, idat = 8, b""
    w = h = ctype = 0
    while i < len(data):
        n, typ = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8:i + 8 + n]
        if typ == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            if depth != 8 or ctype not in (2, 6) or body[12] != 0:
                raise ValueError("unsupported PNG layout")
        elif typ == b"IDAT":
            idat += body
        i += 12 + n
    bpp = 4 if ctype == 6 else 3
    raw, stride = zlib.decompress(idat), w * bpp
    rows, prev, pos = [], bytearray(stride), 0
    for _ in range(h):
        f, line = raw[pos], bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if f == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 255
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                c = prev[x - bpp] if x >= bpp else 0
                b = prev[x]
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        if bpp == 3:  # promote to RGBA (opaque)
            line = bytearray(b"".join(bytes(line[k:k + 3]) + b"\xff" for k in range(0, stride, 3)))
        rows.append(line)
        prev = rows[-1] if bpp == 4 else bytearray(raw[pos - stride:pos])
    return w, h, rows


def png_crop(path: Path, alpha_min: int = 150, pad: int = 4,
             opacity: float = 1.0) -> tuple[bytes, int, int] | None:
    """Crops a render to its opaque pixels (drops the soft floor shadow) and scales
    alpha by `opacity` (baked in: not every SVG renderer honours opacity=). None if empty."""
    w, h, rows = _png_rgba(path.read_bytes())
    ys = [y for y, r in enumerate(rows) if any(r[x] > alpha_min for x in range(3, len(r), 4))]
    if not ys:
        return None
    xs = [x // 4 for r in (rows[ys[0]:ys[-1] + 1]) for x in range(3, len(r), 4) if r[x] > alpha_min]
    x0, x1 = max(min(xs) - pad, 0), min(max(xs) + pad, w - 1)
    y0, y1 = max(ys[0] - pad, 0), min(ys[-1] + pad, h - 1)
    cw, ch = x1 - x0 + 1, y1 - y0 + 1
    if opacity < 1.0:
        for y in range(y0, y1 + 1):
            r = rows[y]
            for x in range(x0 * 4 + 3, (x1 + 1) * 4, 4):
                r[x] = int(r[x] * opacity)
    raw = b"".join(b"\x00" + bytes(rows[y][x0 * 4:(x1 + 1) * 4]) for y in range(y0, y1 + 1))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", cw, ch, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    return png, cw, ch


# ------------------------------------------------------------------ 3D thumbnails

def render_thumbs(pcbnew, jobs: dict[tuple[str, int], Path], proj_dir: Path, work: Path,
                  cli: str) -> dict[tuple[str, int], tuple[bytes, int, int] | None]:
    """jobs: (footprint id, rotation) -> png path. Renders each footprint alone on a
    hidden 0.1 mm board; returns cropped PNGs (None = no footprint / no 3D model)."""
    out: dict[tuple[str, int], tuple[bytes, int, int] | None] = {}
    for (fpid, rot), png in jobs.items():
        out[(fpid, rot)] = None
        nick, _, name = fpid.partition(":")
        lib = kp.footprint_lib(nick, proj_dir)
        if not lib:
            print(f"[3d] {fpid}: footprint library not found — symbol kept")
            continue
        f = pcbnew.FootprintLoad(str(lib), name)
        if not f:
            print(f"[3d] {fpid}: footprint not found — symbol kept")
            continue
        brd = work / f"t{len(out)}.kicad_pcb"
        b = pcbnew.NewBoard(str(brd))
        b.GetDesignSettings().SetBoardThickness(pcbnew.FromMM(0.1))
        f.SetOrientationDegrees(rot % 1000)
        b.Add(f)
        f.Reference().SetVisible(False)
        f.Value().SetVisible(False)
        pads = [p.GetPosition() for p in f.Pads()]
        f.BuildCourtyardCaches()
        cb = f.GetCourtyard(pcbnew.F_CrtYd).BBox()
        ext = max(cb.GetWidth(), cb.GetHeight()) / 1e6 if cb.GetWidth() > 0 else 0
        if pads:
            xs, ys = [p.x for p in pads], [p.y for p in pads]
            ext = max(ext, (max(xs) - min(xs)) / 1e6, (max(ys) - min(ys)) / 1e6)
            cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
        else:
            cx, cy = f.GetPosition().x, f.GetPosition().y
        h = pcbnew.FromMM(0.05)  # hidden board, entirely under the part
        e = pcbnew.PCB_SHAPE(b)
        e.SetShape(pcbnew.SHAPE_T_RECT)
        e.SetLayer(pcbnew.Edge_Cuts)
        e.SetStart(pcbnew.VECTOR2I(cx - h, cy - h))
        e.SetEnd(pcbnew.VECTOR2I(cx + h, cy + h))
        b.Add(e)
        b.Save(str(brd))
        zoom = 0.16 / max(ext, 0.8)  # camera auto-fits the 0.1 mm board; widen to the part
        subprocess.run([cli, "pcb", "render", "--rotate", TILT, "--background", "transparent",
                        "--quality", "high", "--zoom", f"{zoom:.5f}", "--width", str(THUMB_W),
                        "--height", str(THUMB_H), "-o", str(png), str(brd)],
                       capture_output=True, text=True, timeout=300)
        if png.exists():
            out[(fpid, rot)] = png_crop(png, opacity=GHOST if rot >= 1000 else 1.0)
        if out[(fpid, rot)] is None:
            print(f"[3d] {fpid}: no 3D model rendered — symbol kept")
    return out


def fp_pad_axis(pcbnew, fpid: str, proj_dir: Path) -> float | None:
    """Screen angle (deg, y down) of the pad-1 -> pad-2 direction at rotation 0."""
    nick, _, name = fpid.partition(":")
    lib = kp.footprint_lib(nick, proj_dir)
    f = pcbnew.FootprintLoad(str(lib), name) if lib else None
    if not f:
        return None
    p1 = f.FindPadByNumber("1")
    p2 = f.FindPadByNumber("2")
    if not p1 or not p2:
        return None
    a, b = p1.GetPosition(), p2.GetPosition()
    return math.degrees(math.atan2(-(b.y - a.y), b.x - a.x)) % 360


# ------------------------------------------------------------------ composition

def plan(inst: Inst) -> tuple[str, dict]:
    """('lead', {cx, cy, L, ang}) for discrete parts: the 3D part is drawn between the
    two lead ends, pin 1 on pin 1. ('ghost', {box}) for ICs/modules/connectors: the
    3D part as a translucent layer over the symbol body, pin names stay readable.
    ('keep', {}) when neither fits."""
    g = inst.geo
    gx0, gy0, gx1, gy1 = g.gbox
    small = max(gx1 - gx0, gy1 - gy0) <= 8.5
    p1 = next((p for p in g.pins if p.number == "1"), g.pins[0] if g.pins else None)
    p2 = next((p for p in g.pins if p.number == "2"), g.pins[1] if len(g.pins) > 1 else None)
    # two-terminal parts: pins on OPPOSITE sides (R, C, L, LED, diode, fuse)
    if small and len(g.pins) <= 3 and p1 and p2 and (p1.angle - p2.angle) % 360 == 180:

        def inner(p):
            dx, dy = math.cos(math.radians(p.angle)), math.sin(math.radians(p.angle))
            return inst.T(p.x + dx * p.length, p.y + dy * p.length)
        a, b = inner(p1), inner(p2)
        ta, tb = inst.T(p1.x, p1.y), inst.T(p2.x, p2.y)
        # C/L symbols: pins nearly touch -> at least 70 % of the tip-to-tip span
        L = max(math.hypot(b[0] - a[0], b[1] - a[1]), 0.7 * math.hypot(tb[0] - ta[0], tb[1] - ta[1]))
        if L >= 1.0:
            ang = math.degrees(math.atan2(-(b[1] - a[1]), b[0] - a[0])) % 360
            horiz = abs(b[0] - a[0]) >= abs(b[1] - a[1])
            gx = inst.box(g.gbox)
            across = max((gx[3] - gx[1]) if horiz else (gx[2] - gx[0]), 2.5) * 1.1
            # white-out only between the lead ends, so the leads stay drawn
            cov = ((min(a[0], b[0]), gx[1], max(a[0], b[0]), gx[3]) if horiz
                   else (gx[0], min(a[1], b[1]), gx[2], max(a[1], b[1])))
            return "lead", {"cx": (a[0] + b[0]) / 2, "cy": (a[1] + b[1]) / 2, "L": L, "ang": ang,
                            "across": across, "cover": cov}
    bx0, by0, bx1, by1 = inst.box(g.gbox)
    if small:  # small multi-pin part (connector, SOT-23...): the 3D part replaces the body
        return "body", {"box": (bx0, by0, bx1, by1)}
    if min(bx1 - bx0, by1 - by0) < 5:
        return "keep", {}
    return "ghost", {"box": (bx0 + 1, by0 + 1, bx1 - 1, by1 - 1)}


def compose(sheet: Path, out_svg: Path, pcbnew) -> dict[str, int]:
    cli = str(kp.kicad_cli())
    insts, conn, extent = parse_sheet(sheet)
    parts = [i for i in insts if i.geo and i.footprint and not i.ref.startswith("#")]
    # transform self-check: pin tips must land on KiCad's own connection points
    cset = {(round(x, 2), round(y, 2)) for x, y in conn}
    tips = [(round(x, 2), round(y, 2)) for i in insts if i.geo for x, y in i.tips]
    hit = sum(t in cset for t in tips)
    stats = {"symbols": len(parts), "lead": 0, "body": 0, "ghost": 0, "kept": 0,
             "pin_tips": len(tips), "pin_tips_on_nets": hit}

    jobs: dict[tuple[str, int], Path] = {}
    placements = []
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        for inst in parts:
            mode, info = plan(inst)
            if mode == "keep":
                stats["kept"] += 1
                continue
            rot = 0
            if mode == "lead":
                # render with pad 1 -> pad 2 pointing right (unforeshortened by the
                # camera tilt); the image is rotated onto the symbol axis in the SVG
                base = fp_pad_axis(pcbnew, inst.footprint, sheet.parent)
                if base is None:
                    stats["kept"] += 1
                    continue
                rot = int(round(((0 - base) % 360) / 90.0) * 90) % 360
            elif mode == "body":
                rot = 0
            else:  # ghost: native orientation; key +1000 so a footprint can be both kinds
                rot = 1000
            key = (inst.footprint, rot)
            jobs.setdefault(key, work / f"j{len(jobs)}.png")
            placements.append((inst, mode, info, key))
        thumbs = render_thumbs(pcbnew, jobs, sheet.parent, work, cli)

        overlay = []
        for inst, mode, info, key in placements:
            th = thumbs.get(key)
            if not th:
                stats["kept"] += 1
                continue
            png, pw, ph = th
            href = base64.b64encode(png).decode()
            if mode == "lead":  # hide the symbol body, the pin lines stay as the leads
                gx0, gy0, gx1, gy1 = info["cover"]
                overlay.append(f'<rect x="{gx0 - 0.3:.3f}" y="{gy0 - 0.3:.3f}" width="{gx1 - gx0 + 0.6:.3f}" '
                               f'height="{gy1 - gy0 + 0.6:.3f}" fill="#FFFFFF"/>')
                sc = min(info["L"] / pw, info["across"] / ph)  # lead to lead, no taller than the body
                w, h = pw * sc, ph * sc
                cx, cy, ang = info["cx"], info["cy"], info["ang"]
                overlay.append(f'<image x="{cx - w / 2:.3f}" y="{cy - h / 2:.3f}" width="{w:.3f}" '
                               f'height="{h:.3f}" transform="rotate({-ang:.1f} {cx:.3f} {cy:.3f})" '
                               f'href="data:image/png;base64,{href}" xlink:href="data:image/png;base64,{href}"/>')
                r = max(w, h) / 2
                extent += [(cx - r, cy - r), (cx + r, cy + r)]
            elif mode == "body":  # small connector etc.: 3D part instead of the body
                bx0, by0, bx1, by1 = info["box"]
                overlay.append(f'<rect x="{bx0 - 0.2:.3f}" y="{by0 - 0.2:.3f}" width="{bx1 - bx0 + 0.4:.3f}" '
                               f'height="{by1 - by0 + 0.4:.3f}" fill="#FFFFFF"/>')
                sc = min((bx1 - bx0) / pw, (by1 - by0) / ph) * 1.25
                w, h = pw * sc, ph * sc
                cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
                overlay.append(f'<image x="{cx - w / 2:.3f}" y="{cy - h / 2:.3f}" width="{w:.3f}" '
                               f'height="{h:.3f}" href="data:image/png;base64,{href}" '
                               f'xlink:href="data:image/png;base64,{href}"/>')
            else:               # translucent 3D part over the IC body
                bx0, by0, bx1, by1 = info["box"]
                sc = min((bx1 - bx0) / pw, (by1 - by0) / ph)
                w, h = pw * sc, ph * sc
                cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
                overlay.append(f'<image x="{cx - w / 2:.3f}" y="{cy - h / 2:.3f}" width="{w:.3f}" '
                               f'height="{h:.3f}" href="data:image/png;base64,{href}" '
                               f'xlink:href="data:image/png;base64,{href}"/>')
            stats[mode] += 1

    for inst in insts:
        if inst.geo:
            b = inst.box(inst.geo.gbox)
            extent += [(b[0], b[1]), (b[2], b[3])] + inst.tips
    xs, ys = [p[0] for p in extent], [p[1] for p in extent]
    bbox = (min(xs) - 8, min(ys) - 8, max(xs) + 8, max(ys) + 8)
    if not sg.export_svg(cli, sheet, out_svg, bbox, extra="\n".join(overlay)):
        raise RuntimeError("kicad-cli sch export svg failed")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sheet", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--png", type=Path)
    args = ap.parse_args()
    pcbnew = kp.import_pcbnew(__file__)
    try:
        stats = compose(args.sheet.resolve(), args.out.resolve(), pcbnew)
    except kp.ResolveError as e:
        print(f"ERROR [{e.component}]: {e.fix}", file=sys.stderr)
        return 2
    except (RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    ok = stats["pin_tips"] == 0 or stats["pin_tips_on_nets"] / stats["pin_tips"] >= 0.8
    print(f"[3d-sch] {args.out.name}: {stats['lead'] + stats['body']} parts drawn in 3D, {stats['ghost']} ICs with "
          f"a 3D overlay, {stats['kept']} kept as symbols | transform check: "
          f"{stats['pin_tips_on_nets']}/{stats['pin_tips']} pin tips on KiCad connection points"
          f"{'' if ok else ' (LOW: check symbol rotation/mirror handling)'}")
    if args.png:
        if sg.svg_to_png(args.out.resolve(), args.png.resolve(), 2000):
            print(f"[3d-sch] png: {args.png}")
        else:
            print("[3d-sch] png skipped (pip install pymupdf, or set PNG_PYTHON)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
