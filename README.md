# kicad-pcb — KiCad PCB automation for Hermes Agent

[![skills.sh](https://skills.sh/b/pantojinho/hermes-kicad-pcb)](https://skills.sh/pantojinho/hermes-kicad-pcb)
[![ci](https://github.com/pantojinho/hermes-kicad-pcb/actions/workflows/ci.yml/badge.svg)](https://github.com/pantojinho/hermes-kicad-pcb/actions/workflows/ci.yml)

Headless PCB design skill for **Linux, Windows and macOS**: native schematics,
collision-free placement, Freerouting autorouting (DSN/SES), DRC/ERC verification,
and gerber/drill/BOM export — the GPT-6 Astra workflow, without the GUI.
Validated end-to-end on Linux; Windows/macOS via runtime resolvers + CI smoke.
Every tool and path is resolved at runtime (env var > known location > PATH), so
the same commands run on any OS.

## Install

```bash
# Hermes
hermes skills install pantojinho/hermes-kicad-pcb/kicad-pcb

# Any agent (Claude Code, Codex, Cursor, ...)
npx skills add pantojinho/hermes-kicad-pcb
```

> **Hermes security scan**: the Hermes skills scanner may flag this skill as
> CAUTION (reads of `os.environ` + `subprocess` calls — inherent to a skill that
> orchestrates kicad-cli/Freerouting/pcbnew; no network access, no `shell=True`,
> no dynamic commands). Review with `hermes skills inspect
> pantojinho/hermes-kicad-pcb/kicad-pcb` and install with `--force` if you agree.

## Requirements

| Component | Linux | Windows | macOS |
|---|---|---|---|
| KiCad 10+ (`kicad-cli` + pcbnew bindings) | distro package / PPA | installer (bundled python + bindings) | app bundle |
| [Freerouting](https://github.com/freerouting/freerouting) | `linux-x64.zip` bundle | `windows-x64.msi` | `macos-*.dmg` |
| System Java | **not needed** (bundles embed their own runtime) | idem | idem |

> The plain `freerouting-*.jar` is a fallback and requires **Java 25+** (the 2.4.1
> jar is compiled for class file 69 — Java 17 fails with `UnsupportedClassVersionError`).
> Prefer the bundle; unzip it to `~/Work/tools/` (auto-detected) or point
> `FREEROUTING_EXE` / `FREEROUTING_JAR` at it.

Quick check on any OS:

```bash
python skills/kicad-pcb/scripts/check_env.py        # relatorio + como consertar o que falta
```

## Quick start

```bash
# pipeline completo: board -> DSN -> Freerouting -> SES -> DRC (exit 0 = PASS)
python skills/kicad-pcb/scripts/demo_autoroute.py --out /tmp/pcb-demo

# estagios isolados
python skills/kicad-pcb/scripts/demo_autoroute.py --stage create   # board + DSN
python skills/kicad-pcb/scripts/demo_autoroute.py --stage route    # autoroute
python skills/kicad-pcb/scripts/demo_autoroute.py --stage import   # importa SES
python skills/kicad-pcb/scripts/demo_autoroute.py --stage drc      # so DRC

# flags: --route-timeout 600 --max-passes 50 --threads 4
# exit codes: 0 ok | 2 ambiente | 3 estagio falhou | 4 DRC FAIL
```

On Windows/macOS the pipeline script re-execs itself on KiCad's bundled Python
when `import pcbnew` is unavailable in the current interpreter — nothing to configure.

Optional env overrides: `KICAD_PYTHON`, `KICAD_CLI`,
`KICAD10_FOOTPRINT_DIR`/`KICAD_FOOTPRINT_DIR`, `FREEROUTING_EXE`, `FREEROUTING_JAR`.

## What's inside

| File | Purpose |
|---|---|
| `skills/kicad-pcb/SKILL.md` | 10-step Astra-style workflow + validated headless pipeline + 14 pitfalls |
| `skills/kicad-pcb/references/api-cheatsheet.md` | Verified pcbnew Python calls (KiCad 10.0.6) + Freerouting bundle-first guide |
| `skills/kicad-pcb/references/jlcpcb-rules.md` | JLCPCB fab/assembly rules + PCBA BOM/CPL upload workflow |
| `skills/kicad-pcb/scripts/kicad_paths.py` | Cross-platform tool resolvers (env > known paths > PATH) |
| `skills/kicad-pcb/scripts/demo_autoroute.py` | One-shot pipeline: board → DSN → Freerouting → SES → DRC |
| `skills/kicad-pcb/scripts/check_env.py` | Preflight: report what's missing and how to fix it |
| `.github/workflows/ci.yml` | Smoke matrix: ubuntu + windows (syntax + frontmatter + preflight) |

## Validated proof: ESP32 dev board

13 components (ESP32-WROOM-32, CH340N, AMS1117-3.3, USB micro-B), 60x35mm 2-layer,
177 tracks — **0 DRC violations, 0 unconnected** (KiCad 10.0.6, Freerouting 2.4.1,
Linux; end-to-end headless).

![ESP32 dev board](assets/esp32-board.png)

## Companion skills

For design review, BOM/sourcing and manufacturing workflows, pair with
[aklofas/kicad-happy](https://github.com/aklofas/kicad-happy) (MIT) — KiCad
project analysis/DFM scoring, JLCPCB/LCSC/DigiKey BOM management, PCBA upload
translator.

## Acknowledgments

- [aklofas/kicad-happy](https://github.com/aklofas/kicad-happy) by Andrew Klofas
  (MIT) — JLCPCB fabrication/assembly rules in
  `references/jlcpcb-rules.md` are adapted (paraphrased) from it.
- Freerouting's headless CLI makes the DSN/SES autorouting loop possible.

## License

MIT — see [LICENSE](LICENSE).
