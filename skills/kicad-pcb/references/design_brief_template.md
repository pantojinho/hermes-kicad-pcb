---
title: <Board name>
revision: A
status: ENGINEERING DRAFT
author: <person or agent + model>
reviewed <drc_or_erc_type>: <fab/datasheet-backed justification, one line per accepted exception>
---

<!--
Design brief = the engineering narrative of the hardware design document
(`scripts/design_doc.py --brief this.md`). Write it WHILE designing, not after.
Every number the PDF shows about ERC/DRC/parity/board/BOM is measured by the
script; this file holds what a tool cannot measure: intent, reasoning, math and
what is still unknown.

Rules (same as the skill's):
- Separate verified facts, calculations, assumptions and TBDs explicitly.
- Cite the exact datasheet (part number, revision, section) for every limit used.
- Never invent part numbers, prices, stock, measurements or test results.
- A clean ERC/DRC is not a proof of correctness: say what was NOT verified.
- `reviewed <type>` front-matter keys turn ERC/DRC findings of that type into
  documented exceptions in the PDF. Use them only with a real justification.
Delete this comment block.
-->

# Purpose and scope

What the board does, for whom, and what it deliberately does not do.

# Requirements

| ID | Requirement | Source | Status |
|---|---|---|---|
| R1 | e.g. 5 V from USB-C, <= 500 mA | owner / standard | verified / assumed / TBD |

# Architecture

Blocks and the signals/power between them (power tree first, then data paths).

# Theory of operation

## Power

Input, protection, regulation, sequencing, expected currents per rail.

## <Next block>

How it works, why this circuit, which datasheet application circuit it follows.

# Design calculations

Show the arithmetic with units and the datasheet limits it is checked against
(e.g. LDO dissipation, LED current, pull-up values, trace widths, RC timing).

# Component choices

Why each key part (not passives) was chosen; alternatives considered.

# PCB layout notes

Stack-up, placement rules followed (edge connectors, antenna keepout, decoupling
at pins, return paths), and anything intentionally left for a later revision.

# Assumptions and unverified data

Everything that is assumed, simulated-only or TBD.

# Open issues

- Numbered, actionable, with the owner/next step. The PDF copies this section
  into "Open issues and limitations".

# Bring-up and test plan

First power-on steps, current limits, measurements to take, pass criteria.
