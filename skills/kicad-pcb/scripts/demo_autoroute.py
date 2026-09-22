#!/usr/bin/env python3
"""Pipeline Astra-style headless validado (2026-09-21): cria placa 30x20mm
com R1 + D1 e uma conexao N$1 SEM rotear; Freerouting roteira via DSN/SES.

Etapa 1: /usr/bin/python demo_autoroute.py          # cria demo-unrouted.kicad_pcb + demo.dsn
Etapa 2: java -jar ~/Work/tools/freerouting-2.4.1.jar -de demo.dsn -do demo.ses -mp 50 -mt 4
Etapa 3: /usr/bin/python demo_autoroute.py          # importa demo.ses -> demo-routed.kicad_pcb
Etapa 4: kicad-cli pcb drc --format json --output demo-drc.json demo-routed.kicad_pcb
"""
import pcbnew, os

BASE = os.path.dirname(os.path.abspath(__file__))
UNROUTED = f"{BASE}/demo-unrouted.kicad_pcb"
DSN = f"{BASE}/demo.dsn"
SES = f"{BASE}/demo.ses"
FINAL = f"{BASE}/demo-routed.kicad_pcb"
LIBS = "/usr/share/kicad/footprints"
mm = pcbnew.FromMM

board = pcbnew.NewBoard(UNROUTED)

# 1) contorno da placa (obrigatorio p/ DRC/autorouter)
edge = pcbnew.PCB_SHAPE(board)
edge.SetShape(pcbnew.SHAPE_T_RECT)
edge.SetStart(pcbnew.VECTOR2I(mm(0), mm(0)))
edge.SetEnd(pcbnew.VECTOR2I(mm(30), mm(20)))
edge.SetLayer(pcbnew.Edge_Cuts)
edge.SetWidth(mm(0.1))
board.Add(edge)

# 2) footprints das libs oficiais
parts = []
for lib, name, ref, x, y in [
    ("Resistor_SMD", "R_0603_1608Metric", "R1", 8, 10),
    ("LED_SMD", "LED_0603_1608Metric", "D1", 22, 10),
]:
    f = pcbnew.FootprintLoad(f"{LIBS}/{lib}.pretty", name)
    assert f, f"footprint {lib}:{name} nao encontrado"
    f.SetReference(ref)
    f.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
    board.Add(f)
    parts.append(f)

# 3) rede eletrica: R1.2 -> D1.1 (SEM trilha - o autorouter fecha o circuito)
net = pcbnew.NETINFO_ITEM(board, "N$1")
board.Add(net)
parts[0].FindPadByNumber("2").SetNet(net)
parts[1].FindPadByNumber("1").SetNet(net)
board.Save(UNROUTED)
print("placa DES-roteada salva:", UNROUTED)

# 4) exporta DSN p/ Freerouting
ok = pcbnew.ExportSpecctraDSN(board, DSN)
print("DSN export:", ok)
print("ETAPA 2: java -jar ~/Work/tools/freerouting-2.4.1.jar -de", DSN, "-do", SES)

# 5) importa SES se existir (2a execucao)
if os.path.exists(SES):
    board = pcbnew.LoadBoard(UNROUTED)
    ok = pcbnew.ImportSpecctraSES(board, SES)
    board.Save(FINAL)
    print("SES import:", ok, "| tracks finais:", len(board.GetTracks()), "| salvo:", FINAL)
