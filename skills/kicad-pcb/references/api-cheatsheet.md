# pcbnew KiCad 10.0.6 — calls verificados no Linux em 2026-09-21; resolvers cross-platform (smoke em CI)

Import: qualquer Python que importe `pcbnew` — no Linux distro/PPA é o `python3`
do sistema; no Windows/macOS é o Python embutido do KiCad (o `demo_autoroute.py`
re-executa sozinho nele; override: env `KICAD_PYTHON`).
2–3 asserts `PROPERTY_ENUM` no stderr ao importar = ruído C++ inofensivo, ignore.

Paths NÃO hardcode: use `scripts/kicad_paths.py` (`footprints_dir()`,
`kicad_cli()`, `freerouting()`) — resolve por SO (env > locais conhecidos > PATH).
`pcbnew.GetDefaultFootprintsPath()` NÃO existe no 10.0.6.

| Ação | Chamada |
|---|---|
| Nova placa | `pcbnew.NewBoard(path)` (path OBRIGATÓRIO) |
| Abrir | `pcbnew.LoadBoard(path)` |
| Salvar | `board.Save(path)` |
| Unidades | `mm = pcbnew.FromMM`; coords `pcbnew.VECTOR2I(x_nm, y_nm)` |
| Formas | `SHAPE_T_RECT`, `SHAPE_T_SEGMENT`, `SHAPE_T_ARC`, `SHAPE_T_CIRCLE`, `SHAPE_T_POLY` (CAIXA ALTA) |
| Retângulo | `s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_RECT); s.SetStart(...); s.SetEnd(...); s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.1)); board.Add(s)` |
| Carregar FP | `pcbnew.FootprintLoad("<footprints_dir>/<Lib>.pretty", "<nome>")` |
| Posicionar FP | `f.SetReference("R1"); f.SetPosition(pcbnew.VECTOR2I(...)); f.SetOrientationDegrees(90); board.Add(f)` |
| Achar FP/pad | `board.FindFootprintByReference("R1")`, `fp.FindPadByNumber("2")` |
| Criar rede | `net = pcbnew.NETINFO_ITEM(board, "N$1"); board.Add(net)` |
| Ligar pad | `pad.SetNet(net)` |
| Trilha | `t = pcbnew.PCB_TRACK(board); t.SetStart(pad1.GetPosition()); t.SetEnd(pad2.GetPosition()); t.SetWidth(mm(0.25)); t.SetLayer(pcbnew.F_Cu); t.SetNetCode(p1.GetNetCode()); board.Add(t)` |
| Contar | `len(board.GetTracks())`, `len(list(board.GetFootprints()))` (se iterar quebra → patch condicional abaixo) |
| DSN export | `pcbnew.ExportSpecctraDSN(board, "out.dsn")` → True |
| SES import | `pcbnew.ImportSpecctraSES(board, "out.ses")` → True |
| DRC json | `kicad-cli pcb drc --format json --output drc.json board.kicad_pcb` |

## DRC json shape
```json
{"violations": [{"type": "solder_mask_bridge", "description": "...", "severity": "error"}],
 "unconnected_items": [], "schematic_parity": []}
```
Conte por tipo; `unconnected_items` > 0 = conexão não roteada; `schematic_parity` =
descompasso sch↔pcb (é o que DRC sozinho não vê). `solder_mask_bridge` em placas
apertadas = cosmético.

## Freerouting 2.4.1 (headless)

**Preferir o bundle do SO com runtime embutido** (releases: `linux-x64.zip`,
`windows-x64.msi`, `macos-*.dmg`) — invoca o launcher direto, ZERO Java do sistema:

```bash
# Linux/macOS (bundle em ~/Work/tools/ é auto-detectado):
~/Work/tools/freerouting-2.4.1-linux-x64/bin/freerouting -de in.dsn -do out.ses -mp 50 -mt 4
# Windows (MSI):
"C:\Program Files\Freerouting\freerouting\freerouting.exe" -de in.dsn -do out.ses -mp 50 -mt 4
```

O **jar** solto é fallback e exige **Java 25+** (o 2.4.1 = class file 69; Java 17
morre com `UnsupportedClassVersionError`):
```bash
java -jar freerouting-2.4.1.jar -de in.dsn -do out.ses -mp 50 -mt 4
```
`-mp` = passes máx, `-mt` = threads. Score 1000 + "0 unrouted" = roteou tudo.
Placas grandes travando: `-mp 200`, menos threads. Ou deixe os resolvers acharem
tudo: `python3 demo_autoroute.py --stage route`.

## Patch de compatibilidade — CONDITIONAL (não é instalação padrão)

Só se iteração crashar com `'SwigPyIterator' object has no attribute 'next'`
(python 3.14 + bindings antigos, ex. Arch/omarchy). Ache o arquivo:
`python3 -c "import pcbnew; print(pcbnew.__file__)"` — 3 ocorrências de:
`item = it.next()` → `item = it.__next__() if hasattr(it, '__next__') else it.next()`
No Ubuntu 24.04 + KiCad 10.0.6 (python 3.12) NÃO precisa de patch.

## o que NÃO existe no kicad-cli 10.0.6
`pcb export dsn`, `pcb import ses` — use as funções Python acima. O kicad-cli tem:
`pcb {drc,export,import,render,upgrade}`, `sch {erc,export}`, `fp {export svg,upgrade}`, `sym upgrade`.
