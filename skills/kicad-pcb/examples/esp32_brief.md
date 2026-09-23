---
title: ESP32 + LED + USB-C example board
revision: A
status: DEMONSTRATION DESIGN — NOT FOR PRODUCTION
author: kicad-pcb skill (esp32_example.py), reviewed by an AI agent
reviewed hole_clearance: J1 = GCT USB4105 vendor footprint: its NPTH alignment pegs sit 0.194 mm from its own GND pads by manufacturer geometry; 0.25 mm is this demo project's default, not a fab limit. Confirm against the chosen fab's NPTH-to-copper capability before ordering.
---

# Purpose and scope

A minimal board used to demonstrate the kicad-pcb skill end to end, headless, on
Linux and Windows: schematic generation, schematic-driven board nets, placement,
Freerouting, copper pours, DRC with schematic parity, 3D renders and this document.
It is **not** a usable ESP32 development board: programming access, reset/boot
circuitry and input protection are deliberately missing (see Open issues).

# Requirements

| ID | Requirement | Source | Status |
|---|---|---|---|
| R1 | Powered from a USB-C source, 5 V, default USB current | demo scope | implemented (sink only) |
| R2 | 3.3 V rail for an ESP32-WROOM-32 module | module datasheet: 3.0–3.6 V | implemented (AMS1117-3.3) |
| R3 | One user LED on a GPIO | demo scope | implemented (IO2) |
| R4 | 2-layer board, 45 x 25 mm, antenna at the board edge | demo scope | implemented |
| R5 | Firmware programming and reset | normal ESP32 practice | **not implemented** |

# Architecture

USB-C receptacle J1 (VBUS, CC1/CC2, GND) → +5V → AMS1117-3.3 LDO U2 → +3V3 →
ESP32-WROOM-32 module U1. IO2 of U1 drives LED D1 through R1. There is no data
path: USB D+/D− and SBU pins of J1 are not connected.

# Theory of operation

## USB-C power input (J1, R2, R3)

R2 and R3 (5.1 kΩ) pull CC1 and CC2 to ground. These are the Type-C sink
termination resistors Rd: a Type-C source detects them on whichever CC line the
plug orientation connects and only then enables VBUS. The board draws "default
USB power" (500 mA from USB 2.0 ports, 900 mA from USB 3.x ports); it does not
read the advertised current level. C1 (10 µF) is the local bulk capacitor on +5V.

## 3.3 V regulation (U2, C1, C2)

U2 is a fixed 3.3 V linear regulator. Input 5 V, output 3.3 V: the headroom of
1.7 V exceeds the AMS1117 dropout (about 1.1–1.3 V at its 800 mA rating) with
margin. C2 (10 µF) is the output capacitor next to the module supply pin.

## ESP32 module (U1)

The module contains the ESP32, flash, crystal and PCB antenna; it needs only
3.3 V, ground, an enable (EN) signal and strapping-pin states at reset. VDD
(pin 2) is on +3V3, the GND pins (1, 15, 38 and the thermal pad 39) on GND.

## LED (IO2, R1, D1)

IO2 (module pin 24) drives the anode of D1 through R1 = 1 kΩ; the cathode goes to
GND, so the LED is lit when IO2 is high. IO2 is a strapping pin: it must be low
or floating for the serial bootloader. The LED path only conducts above the LED
forward voltage, so at reset the pin is effectively floating — compatible.

# Design calculations

| Quantity | Calculation | Result | Basis |
|---|---|---|---|
| LED current | (3.3 V − 2.1 V) / 1 kΩ | ≈ 1.2 mA | Vf 2.1 V assumed for a green 0603 LED (part TBD) |
| LDO dissipation, typical | (5 − 3.3) V × 0.08 A | ≈ 0.14 W | 80 mA average assumed (Wi-Fi idle/modem-sleep mix) |
| LDO dissipation, TX peaks | (5 − 3.3) V × 0.5 A | ≈ 0.85 W | Espressif asks for a supply able to deliver ≥ 500 mA |
| Junction rise at 0.85 W | 0.85 W × 60–90 °C/W | ≈ 50–77 °C | θJA of SOT-223 on a small 2-layer board: assumed range, TBD |

The peaks are short bursts, so the average dissipation is what heats the LDO;
sustained high-duty transmission would need either more copper under the tab or a
switching regulator.

# Component choices

- **ESP32-WROOM-32**: pre-certified module with PCB antenna, so the demo needs no RF design.
- **AMS1117-3.3**: ubiquitous, cheap fixed LDO; adequate for a USB-powered demo.
- **GCT USB4105 (16-pin USB-C)**: official KiCad footprint and 3D model; the matching
  symbol is `USB_C_Receptacle_USB2.0_16P` (pin-for-pad).

# PCB layout notes

- 2 layers, 45 x 25 mm, 1.6 mm. USB-C opening flush with the left edge; the module sits
  at the right edge with its antenna past the board edge (no copper under the antenna).
- GND pours on both layers, a stitching via next to every SMD GND pad.
- Signal routing by Freerouting after placement; the pours are added after routing.
- The antenna overhang is documented by a scoped custom DRC rule on U1's silkscreen.

# Assumptions and unverified data

- LED forward voltage, exact LED part and all passive part numbers: TBD.
- AMS1117 vendor/datasheet revision: TBD. The original AMS1117 datasheet asks for a
  22 µF tantalum output capacitor for stability; C2 is a 10 µF ceramic. Stability
  with a low-ESR ceramic must be confirmed against the chosen vendor's datasheet.
- θJA of U2 on this board: not simulated, not measured.
- No part of this board has been built or measured.

# Open issues

1. **No programming access**: the original ESP32 has no USB peripheral and UART0
   (TXD0/RXD0) is not brought out. Add a USB-UART bridge on the USB-C data pins, or at
   least a UART header.
2. **EN has no RC**: add 10 kΩ pull-up to +3V3 and 1 µF to GND (Espressif reference
   design), plus a RESET button. ERC keeps this pin flagged on purpose.
3. **No BOOT button on IO0.**
4. **Decoupling**: add 0.1 µF next to the module supply pin in addition to C2.
5. **LDO output capacitor** type/value per the chosen AMS1117 vendor (see assumptions).
6. **No input protection**: no ESD/TVS on VBUS/CC, no fuse or reverse-current path.
7. **LDO thermal margin** under sustained Wi-Fi transmission is not verified.

# Bring-up and test plan

This board is a documentation example; if one is ever built: power from a current-limited
5 V supply (100 mA limit), check +3V3 = 3.3 V ± 3 %, then raise the limit to 600 mA.
Programming requires resolving open issues 1–3 first.
