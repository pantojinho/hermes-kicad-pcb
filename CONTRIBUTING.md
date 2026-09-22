# Contributing to kicad-pcb

This skill is MIT-licensed and open to improvements — by humans **and AI agents**.
If you are an agent reading this: you are welcome to contribute; follow the same
rules as everyone else.

## Ground rules

1. **English only** in all repo text — code, comments, docs, commit messages,
   CI logs. The repo is global; non-English prose (except deliberate multilingual
   trigger keywords in `SKILL.md`'s description) will be rejected.
2. **Cross-platform by default.** Every script must run on Linux, Windows and
   macOS. Never hardcode an OS path — resolve via `scripts/kicad_paths.py`
   (env var > known locations > PATH) or add a new resolver there.
3. **ASCII-safe output strings** in scripts (no accents) — Windows consoles still
   ship code pages that mangle non-ASCII.
4. **Validated claims only.** Never state "works" without the command that proves
   it. Platform claims must say WHERE they were validated (local Linux is the
   reference; Windows/macOS via CI or a real machine).
5. **Fix issues at source** — no DRC/ERC exclusions just to pass, no faked assets.

## Repo layout

```
skills/kicad-pcb/SKILL.md            # main skill doc (frontmatter: name + description <=1024)
skills/kicad-pcb/references/         # deep-dive docs (api-cheatsheet, jlcpcb-rules)
skills/kicad-pcb/scripts/            # kicad_paths (resolvers), demo_autoroute (pipeline),
                                     # check_env (preflight), easyeda_bridge, esp32_example
.github/workflows/ci.yml             # ubuntu + windows smoke
assets/                              # validated example renders (3D + schematic), per-OS
```

## Development loop

```bash
# 1) preflight (all tools present?)
python3 skills/kicad-pcb/scripts/check_env.py --strict

# 2) reference pipeline end-to-end (must end with: DRC: PASS)
python3 skills/kicad-pcb/scripts/demo_autoroute.py --out /tmp/demo

# 3) full example (schematic + board + autoroute + 3D render + schematic PNG)
python3 skills/kicad-pcb/scripts/esp32_example.py --out /tmp/esp32

# 4) syntax check everything
python3 -m py_compile skills/kicad-pcb/scripts/*.py
```

Every PR must keep steps 1–4 green on Linux; CI runs the smoke matrix on
ubuntu-latest and windows-latest.

## Good first contributions

- Real-machine macOS validation (the resolvers cover it, but it has never run there).
- Schematic import for EasyEDA Std projects (PCB side is done — see `easyeda_bridge.py`;
  the schematic side has no headless importer yet).
- More examples (LED matrix, motor driver) reusing `esp32_example.py` structure.
- Translations of SKILL.md *trigger keywords* into other languages (keep them in the
  frontmatter description only).

## For AI agents specifically

- Read `SKILL.md` and `references/api-cheatsheet.md` before touching scripts — the
  pitfalls there were paid for in debugging hours.
- Do not add dependencies beyond the Python standard library in `kicad_paths.py` /
  `demo_autoroute.py` / `check_env.py` (they must run before anything is installed).
  `easyeda_bridge.py` may call `easyeda2kicad` when present (optional tool).
- Generated artifacts (boards, DSN/SES, DRC json) are gitignored — never commit them.
  The only committed artifacts are the example renders under `assets/`.
- When you change behavior, update SKILL.md + cheatsheet in the same commit, and
  re-run the development loop above.

## License

MIT — see [LICENSE](LICENSE). By contributing you agree your work is licensed
under MIT as well.
