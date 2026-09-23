---
title: Simple LED board
revision: A
status: DEMONSTRATION DESIGN
author: kicad-pcb skill (simple_board.py)
---

# Purpose and scope

The smallest complete board the kicad-pcb skill produces with nothing but KiCad
installed (no Freerouting, no Java): a 2-pin power header, a series resistor and an
LED. It exercises schematic generation, board generation linked to the schematic,
deterministic routing, ERC, DRC with schematic parity and the fabrication outputs.

# Requirements

| ID | Requirement | Source | Status |
|---|---|---|---|
| R1 | Light one LED from an external DC supply `VIN` | demo scope | implemented |
| R2 | LED current set by the generator options | `--vin`, `--led-vf`, `--led-ma` | implemented (E12 value) |
| R3 | Single-layer routing, 30 x 20 mm board | demo scope | implemented |

# Theory of operation

The supply enters on J1 (pin 1 = VIN, pin 2 = GND). R1 limits the LED current:
`R = (VIN − Vf) / I_LED`, rounded **up** to the next E12 value so the current never
exceeds the target. D1's anode (pad 2) takes the resistor side, its cathode (pad 1)
returns to GND.

# Design calculations

| Case | Calculation | R1 (E12) | Resulting current |
|---|---|---|---|
| Default: 5 V, Vf 2.0 V, 3 mA | (5 − 2.0) / 3 mA = 1.0 kΩ | 1 kΩ | 3.0 mA |
| 3.3 V, Vf 2.0 V, 2 mA | (3.3 − 2.0) / 2 mA = 650 Ω | 680 Ω | 1.9 mA |

Resistor dissipation at the default: (3.0 V)² / 1 kΩ = 9 mW — far below the 125 mW
standard rating of an 0805 resistor.

# PCB layout notes

All parts on the top layer, one 0.3 mm track per net, GND returned along the bottom
edge of the board. Silkscreen marks VIN/GND next to the header pins.

# Assumptions and unverified data

- LED forward voltage (default 2.0 V) is an input, not a measured value; the exact LED
  part number is TBD.
- No reverse-polarity protection: a reversed supply only reverse-biases the LED, which
  most LEDs tolerate only up to about 5 V (check the chosen LED's datasheet).

# Open issues

1. Choose exact LED and resistor part numbers before ordering.
2. Add reverse-polarity protection if the supply can be connected backwards above 5 V.
