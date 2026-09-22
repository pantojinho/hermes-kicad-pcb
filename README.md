# kicad-pcb — KiCad PCB automation for Hermes Agent

[![skills.sh](https://skills.sh/b/pantojinho/hermes-kicad-pcb)](https://skills.sh/pantojinho/hermes-kicad-pcb)

Headless PCB design skill: native schematics, collision-free placement, Freerouting autorouting (DSN/SES), DRC/ERC verification, and gerber/BOM export — the GPT-6 Astra workflow, without the GUI.

## Install

```bash
# Hermes
hermes skills install pantojinho/hermes-kicad-pcb/kicad-pcb

# Any agent (Claude Code, Codex, Cursor, ...)
npx skills add pantojinho/hermes-kicad-pcb
```

## What's inside

| File | Purpose |
|---|---|
| `skills/kicad-pcb/SKILL.md` | 10-step Astra-style workflow + validated headless pipeline |
| `skills/kicad-pcb/references/api-cheatsheet.md` | Verified pcbnew Python calls (KiCad 10.0.6) + 13 documented pitfalls |
| `skills/kicad-pcb/scripts/demo_autoroute.py` | Reference implementation: board → DSN → Freerouting → SES → DRC |

## Validated proof: ESP32 dev board

13 components (ESP32-WROOM-32, CH340N, AMS1117-3.3, USB micro-B), 60x35mm 2-layer, 177 tracks — **0 DRC violations, 0 unconnected**.

![ESP32 dev board](assets/esp32-board.png)

## Requirements

- KiCad 10+ (`kicad-cli` + pcbnew Python bindings)
- Java 17+ + [Freerouting](https://github.com/freerouting/freerouting) jar for autorouting
