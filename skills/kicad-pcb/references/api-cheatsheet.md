# pcbnew KiCad 10.0.6 + Python 3.14 — calls verificados em 2026-09-21

Import: SEMPRE `/usr/bin/python` (bindings em /usr/lib/python3.14/site-packages).
2 asserts PROPERTY_ENUM no stderr ao importar = ruído inofensivo.

| Ação | Chamada |
|---|---|
| Nova placa | `pcbnew.NewBoard(path)` (path OBRIGATÓRIO) |
| Abrir | `pcbnew.LoadBoard(path)` |
| Salvar | `board.Save(path)` |
| Unidades | `mm = pcbnew.FromMM`; coords `pcbnew.VECTOR2I(x_nm, y_nm)` |
| Formas | `SHAPE_T_RECT`, `SHAPE_T_SEGMENT`, `SHAPE_T_ARC`, `SHAPE_T_CIRCLE`, `SHAPE_T_POLY` (CAIXA ALTA) |
| Retângulo | `s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_RECT); s.SetStart(...); s.SetEnd(...); s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.1)); board.Add(s)` |
| Carregar FP | `pcbnew.FootprintLoad("/usr/share/kicad/footprints/<Lib>.pretty", "<nome>")` |
| Posicionar FP | `f.SetReference("R1"); f.SetPosition(pcbnew.VECTOR2I(...)); f.SetOrientationDegrees(90); board.Add(f)` |
| Achar FP/pad | `board.FindFootprintByReference("R1")`, `fp.FindPadByNumber("2")` |
| Criar rede | `net = pcbnew.NETINFO_ITEM(board, "N$1"); board.Add(net)` |
| Ligar pad | `pad.SetNet(net)` |
| Trilha | `t = pcbnew.PCB_TRACK(board); t.SetStart(pad1.GetPosition()); t.SetEnd(pad2.GetPosition()); t.SetWidth(mm(0.25)); t.SetLayer(pcbnew.F_Cu); t.SetNetCode(p1.GetNetCode()); board.Add(t)` |
| Contar | `len(board.GetTracks())`, `len(list(board.GetFootprints()))` (precisa do patch py3.14) |
| DSN export | `pcbnew.ExportSpecctraDSN(board, "out.dsn")` → True |
| SES import | `pcbnew.ImportSpecctraSES(board, "out.ses")` → True |
| DRC json | `kicad-cli pcb drc --format json --output drc.json board.kicad_pcb` |

## DRC json shape
```json
{"violations": [{"type": "solder_mask_bridge", "description": "...", "severity": "error"}],
 "unconnected_items": [], "schematic_parity": []}
```
Conte por tipo; `unconnected_items` > 0 = conexão não roteada; `schematic_parity` = descompasso sch↔pcb (o pego pelos 46 issues do Astra).

## Freerouting 2.4.1 (headless)
```bash
java -jar ~/Work/tools/freerouting-2.4.1.jar -de in.dsn -do out.ses -mp 50 -mt 4
```
`-mp` = passes máx, `-mt` = threads. Sai com score 1000 + "0 unrouted" quando roteira tudo.
Se travar em placas grandes: `-mp 200`, menos threads, ou `--help` para `--us Hybrid`.

## Patch de compatibilidade (re-aplicar se kicad atualizar)
Arquivo `/usr/lib/python3.14/site-packages/pcbnew.py`, 3 ocorrências:
`item = it.next()` → `item = it.__next__() if hasattr(it, '__next__') else it.next()`
(Causa: shims SWIG legados vs Python 3.14; sem o patch, iteração de tracks/footprints quebra.)

## o que NÃO existe no kicad-cli 10.0.6
`pcb export dsn`, `pcb import ses` — use as funções Python acima. O kicad-cli tem:
`pcb {drc,export,import,render,upgrade}`, `sch {erc,export}`, `fp {export svg,upgrade}`, `sym upgrade`.
