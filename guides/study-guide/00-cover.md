---
title: "Zoox Compute Test Engineer"
subtitle: "Study & Reference Guide"
date: "May 2026"
---

# How to Use This Guide {.unnumbered}

This is a **day-to-day technical reference** for the Compute (Manufacturing Test &
Diagnostics) role — not interview prep, and not an onboarding plan. The onboarding and
career material (the first 90 days — technical *and* relational — working with contract
manufacturers, the leadership principles) lives in the companion *Success* guide; live
PCIe root-causing lives in the *PCIe Debug* guide. Read the opener for context, then
use the chapters as deep references while you work a board, a bring-up, or a yield issue.

- **Chapters 1–3 — the toolbox:** Python for test automation, Bash, and the parts of
  Linux you actually debug hardware from.
- **Chapters 4–8 — the interfaces, at register level:** PCIe, NVMe, GPUs, DRAM/memory,
  and the automotive/serial buses (GMSL, CAN, Automotive Ethernet, I²C/SPI/UART).
- **Chapters 9–13 — the discipline:** manufacturing-test principles, the math and
  statistics behind limits and confidence, networking on the floor, power/bring-up/
  functional safety, and control-systems fluency.

A companion `toolkit/` implements much of the PCIe/NVMe/GPU/GMSL material described here
(the BERT engine, AER decode, lane margining, the chain monitor, the interface checks).
Read a section, then go read and run the code that does it.
