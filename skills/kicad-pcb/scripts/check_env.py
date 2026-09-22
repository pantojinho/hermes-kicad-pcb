#!/usr/bin/env python3
"""Preflight do toolchain KiCad headless — Linux / Windows / macOS.

Uso:
  python3 check_env.py            # relatorio; exit 0 sempre (informativo)
  python3 check_env.py --strict   # exit 1 se algo critico faltar (p/ CI)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kicad_paths as kp  # noqa: E402

OSNAME = {"Linux": "Linux", "Darwin": "macOS", "Windows": "Windows"}.get(
    __import__("platform").system(), __import__("platform").system())

def check_python_pcbnew() -> str:
    for p in kp.python_with_pcbnew_candidates():
        if str(p) == sys.executable or p.exists():
            v = kp.verify_pcbnew(p, timeout=60)
            if v:
                return f"{p} ({v})"
    raise kp.ResolveError(
        "python+pcbnew",
        "instale o KiCad 10 (inclui os bindings pcbnew) ou aponte KICAD_PYTHON")


CHECKS = [
    ("python+pcbnew", check_python_pcbnew),
    ("footprints dir", kp.footprints_dir),
    ("kicad-cli", kp.kicad_cli),
    ("freerouting", lambda: kp.freerouting()[1]),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 se algo faltar")
    args = ap.parse_args()

    print(f"Sistema: {OSNAME} | python: {sys.version.split()[0]} ({sys.executable})\n")
    ok_all = True
    for name, fn in CHECKS:
        try:
            val = fn()
            print(f"  OK  {name:16} -> {val}")
        except Exception as e:  # noqa: BLE001 — preflight deve listar tudo
            ok_all = False
            print(f"  X   {name:16} -> {e}")
    print()
    if ok_all:
        print("Toolchain completo. Proximo passo: "
              "python3 demo_autoroute.py (pipeline completo ate DRC).")
    else:
        print("Faltam componentes acima (veja as dicas de correcao em cada linha).")
    return 0 if (ok_all or not args.strict) else 1


if __name__ == "__main__":
    sys.exit(main())
