# Sprint 4 Roadmap — Compute-Platform Validation Toolkit

**Date:** 2026-05-29 · **Branch target:** `main` (sprint-atomic commits, same cadence as Sprints 1–3) · **Author:** automated synthesis · workflow `wf_93f8386c-b39`

**Source:** Round-2 deep-research workflow `wf_93f8386c-b39` — 9 agents (4 subsystem researchers → 4 accuracy/scope verifiers → 1 synthesis), ~925K subagent tokens, ~27 min, 2026-05-29. Scoped strictly to conformance / RAS-validation of the platform's *own* error-handling (no offensive content). Every integration point below was verified by the synthesis agent against the live codebase; the `_BERT_PATTERNS` PRBS24 gap, the `EyeMeasurement`/`EqLevels` field set, and the `EXPECTED_SCHEDULED_BUDGET = 0` gate were independently re-confirmed before publishing.

**Continues:** the Round-1 roadmap (`2026-05-29-roadmap.md`, run `wf_5dc3bf89-af9`), whose Sprint-3 step #10 called for exactly this second pass over GMSL3 / automotive-Ethernet TSN / DDR5 / CXL.

---

## 1. Executive summary

Sprints 1–3 built the *horizontal* spine of `computetest`: an ocp-diag-core result emitter (`io/ocpdiag.py`), a `pci_lmt`-compatible per-lane margining adapter (`lmt_adapter.py`), a vendor-agnostic instrument HAL (`instruments.py`) with a Mock/real SCPI split, a PCIe retimer abstraction (`pcie/retimer/base.py`), an NVMe-MI v2.1 management stack, and a UNH-IOL-style coverage matrix enforced by a CI gate at **debt budget = 0**. Those are now *table stakes* and proven against the codebase.

Sprint 4 goes *vertical*: it adds four new physical/protocol subsystems that a real compute-platform integrator (automotive AV compute, datacenter memory-expansion) must qualify before deployment. Each was chosen because it **reuses the existing five seams 1:1** rather than inventing infrastructure — the marginal cost is a new device abstraction + a coverage matrix + a CI-gate clone, not a new architecture:

| Subsystem | Why now | Dominant seam reuse |
|---|---|---|
| **GMSL3** automotive camera/display SerDes (ADI) | The `gmsl.py` we already ship only checks lock + a v4l2 frame grab; it has no register/PRBS/EOM/FEC layer. GMSL3 maps almost perfectly onto the **retimer abstraction** (in-system PRBS, eye telemetry, loopback, EQ readback). | retimer ABC → new `SerDesLink`; `ExternalBERT`/`Scope`/`PowerSupply`/`SwitchMatrix`; new VNA + thermal HAL drivers; `lmt_adapter` → EYE_V/EYE_H/VOLT/TEMP margin types |
| **Automotive Ethernet TSN** (100/1000BASE-T1 + 802.1AS/Qbv/Qbu/CB) | `ethernet.py` already carries `EthHealth.role` + cable-test TDR for single-pair automotive PHYs; TSN time-sync/scheduling/preemption/FRER is the conformance layer on top. Avnu launched the *first* TSN cert program (Jun 2024). | `ethernet.py` → `EthPhy` ABC (mirrors retimer); ocp-diag `emit_*` with `LESS_THAN_OR_EQUAL` validators; **3 new coverage matrices**; new `TimeIntervalAnalyzer`/`TSNTrafficGenerator` HAL |
| **DDR5 RAS** (JESD79-5D, on-die ECC / ECS / PPR / EINJ) | `memory.py` already reads EDAC CE/UE per-DIMM; the gap is the DDR5-specific RAS control plane (ECS/scrub/memory-repair sysfs new in **Linux 6.15**) + ACPI-EINJ-driven error-handling verification. | `memory.py` EDAC reader extension; `lmt_adapter`-shaped DDR5 training-margin records; ocp-diag `EQUAL 1`/`LESS_THAN_OR_EQUAL` validators; `EyeMeasurement` dataclass reused for DRAM-ball eye |
| **CXL 2.0/3.x** (protocol conformance + RAS event records) | The deepest *software-testable* RAS surface of the four; CXL.io rides PCIe so `aer.py`'s W1C engine and Lane Margining transfer verbatim. Official 2.0 (incl. host) compliance testing began **Dec 2024**. | `aer.py` W1C → `cxl/ras.py`; NVMe-MI `MctpTransport`/`MiMessage` CCI → `cxl/mailbox.py`; retimer ABC (CXL link-extension is a tested class); QEMU `cxl_type3` mirrors the `sim/qemu` QMP-inject pattern |

**Scope discipline (carried verbatim from the briefs):** every RAS/fault-injection item is *standards-defined diagnostic-coverage verification* — inject the fault a safety mechanism is designed to detect, then assert the platform's **own** error reporting fires (ERRB asserts, the right counter latches, EDAC logs the CE, the AER UE bit sets, the Event Record parses). The DDR5 RFM/PRAC item is restricted to **enablement + alert-path** verification (no activation-pattern/disturbance stress). No offensive content.

**Sequencing rationale.** GMSL3 ships first (4.1) because it is the largest *coverage gap relative to existing code* and is pure register/instrument work that reuses the retimer abstraction with no new external dependency. CXL ships last (4.4) because its in-band tier depends on QEMU `cxl_type3` fidelity and a member-distributed CTS — the highest open-question load — but the parser/RAS corpus tiers can land hardware-free immediately.

---

## 2. Per-subsystem sprint-atomic breakdown

Commit convention matches Sprints 1–3 (`Sprint 4.x: <focused change>`). Each row is one commit. **Seam** names the existing file/abstraction touched. Priorities (high/med/low) are inherited from the briefs.

### 4.1 — GMSL3 automotive SerDes (Analog Devices MAX96793 / MAX96792A)

> The existing `gmsl.py` (`check_gmsl`/`check_deserializer`, 160 lines, `"BAD"`-in-link mock convention) becomes the **v4l2 video-liveness layer on top of** a new register/link device layer. No GMSL multi-vendor plugfest exists — "conformance" = self-certification against ADI's AN-2585 GMSL3 Channel Spec + UG-2208 Hardware Design & Validation Guide. The matrix header must say so explicitly.

| Commit | Work item | Seam | Priority | Notes |
|---|---|---|---|---|
| **4.1.1** | New `gmsl/serdes.py`: abstract `SerDesLink(abc.ABC)` + deterministic `MockSerDes` (mirror `MockRetimer`'s injected-knob style). Methods: `info()`, `lock_status()`, `negotiated_mode()`, `force_relock()`, `read_eom(link)`, `run_prbs_bist(dir, pattern)`, `read_fec_stats()`, `set_loopback()`, error-counter reads. Single `SerDesError(RuntimeError)` mirroring `RetimerError`. | `pcie/retimer/base.py` (structural parallel) | high | Extends `"BAD"`-mock convention to a `"wrong-mode"` injected state. |
| **4.1.2** | **Lock + MODE negotiation** check: poll per-link `LOCKED` + LOCK GPIO; assert the *negotiated* rate/mode (GMSL3-PAM4-12G vs GMSL3-NRZ-6G vs GMSL2/1 fallback) — a "locked" link in the wrong mode is a defect. Bounded lock-time. Golden-value register verify (DriveOS errb-seq pattern). | `gmsl.py` (wrap existing) | high | DESERializer LOCK only asserts when *all* enabled links lock (MAX96792A=2, MAX96712-class=4). |
| **4.1.3** | **Forward+reverse PRBS BER, pre/post-FEC.** Reuse `ber.py` `confidence_le`/`ber_upper_bound` (chi-squared/Poisson) for the timed-window confidence bound. Post-FEC target ≤1e-30; the *real* margin oracle is pre-FEC FEC-block-input BER (stop at >1e-7 or any uncorrected block). Distinguish Link-PRBS vs Video-PRBS. | `ber.py` + `gmsl/serdes.py` | high | Flag links living on FEC headroom. |
| **4.1.4** | **FEC diagnostics** standalone: Reed-Solomon corrected-bit rate + uncorrectable-block count over a soak; rising corrected-bit rate at clean post-FEC output = earliest degradation signal. Emit as ocp-diag measurements; any uncorrectable block = diagnosis. | `io/ocpdiag.py` (`emit_health`-style) | high | Complements 4.1.3 (PRBS = test pattern; FEC stats = live traffic). |
| **4.1.5** | **EOM eye margin** read: vertical+horizontal openings after CTLE/DFE per link; PAM4 three-eye (upper/mid/lower). Map onto `EyeMeasurement` (`eye_ui`/`eye_mv`/`height_mv`/`width_ui`) + `eye_quality_verdict`. | `pcie/retimer/base.py` (`EyeMeasurement` reuse) | high | Open Q: EOM = absolute UI/mV or relative steps? (decides true-vs-relative margin). |
| **4.1.6** | **NEW HAL: 4-port VNA driver** (`Vna(SCPIInstrument)`) over SCPI + an S-parameter post-processor that applies the GMSL3 100 MHz filter (unfiltered <50 MHz / filtered ≥50 MHz) and compares `.s4p` to short/long-channel IL/RL masks + 2–10 MHz PoC RL carve-out. | `instruments.py` | high | **Blocked**: transcribe exact dB-vs-freq limit lines from AN-2585 PDF before coding the mask — do NOT hardcode guesses. |
| **4.1.7** | **`gmsl_lmt` adapter**: project EOM/PRBS/FEC V&T shmoo onto `LmtLaneRecord`-shaped rows (`step`, `sample_count`, `sample_count_bits`, `error_count`, `ber`); extend `margin_type` beyond TIMING\|VOLTAGE to **EYE_V\|EYE_H\|VOLTAGE\|TEMP**. | `lmt_adapter.py` | med | Drops GMSL margining into the same BigQuery/CSV pipeline as PCIe LMT. |
| **4.1.8** | **Functional-safety (RAS) mechanism verification**: open/short the coax via `SwitchMatrix` and `PowerSupply` (power-fault) → assert ERRB asserts, the specific error counter/flag latches, clearing de-asserts ERRB. Line-fault / lock-loss / FEC-uncorrectable / video-CRC paths. | `instruments.py` (`SwitchMatrix`,`PowerSupply`) + `io/ocpdiag.py` | high | ASIL-B/-D-decomposition diagnostic-coverage view only. |
| **4.1.9** | **Video payload integrity (CSI-2)** + **control-channel** (I2C/UART/SPI/GPIO tunnel, 16-bit CRC + seq# + ARQ retransmit). Extend existing v4l2 capture with video-CRC counter reads (assert unchanged) + VC/data-type map check. | `gmsl.py` | high (video) / med (ctrl) | Locked GMSL link can still deliver corrupt/mis-mapped CSI-2. |
| **4.1.10** | **V/T margining** two-axis shmoo (`PowerSupply` × **new thermal-chamber HAL**) across AEC-Q100 Grade-2 −40…+105 °C while running PRBS+EOM+FEC; on-die temp-sensor readback; AEQ re-convergence after T/V step. Add `ExternalBERT` **PRBS24** (`_BERT_PATTERNS`) or document PRBS31-correlation. | `instruments.py` + `lmt_adapter.py` (4.1.7) | med | Reuses the `pci_lmt` per-step record shape. SSC/EMI-mode interop is a low-priority follow-on. |
| **4.1.11** | **Coverage matrix + CI-gate clone**: `docs/gmsl/gmsl3_channel_validation_matrix.md` (## Spine, 5-col) + `tests/test_gmsl3_coverage.py` (copy the v25 gate machinery). Header: "Mandatory = ADI-spec-mandatory (AN-2585/UG-2208), NOT plugfest-mandatory." | coverage-gate pattern | high | See §4 for the rows. |

**Sequence:** 4.1.1 → (4.1.2, 4.1.5 in parallel; both pure register) → 4.1.3 → 4.1.4 → 4.1.7 → 4.1.8/4.1.9 → 4.1.6/4.1.10 (instrument-heavy, gated on PDF transcription) → 4.1.11 last (locks the gate once the rows have real nodes).

---

### 4.2 — Automotive Ethernet TSN (100/1000BASE-T1 + IEEE 802.1 TSN)

> Two grounded conformance domains: the single-pair **PHY** (OPEN Alliance TC8/TC12, UNH-IOL Cl.96/Cl.97) and the **IEEE 802.1 TSN feature set** (802.1AS gPTP, Qbv, Qbu+802.3br, Qav, Qci, CB FRER — consolidated into 802.1Q-2022) certified by **Avnu** (program launched Jun 2024). The toolkit produces the same artifact shape and gates a board's TSN posture in CI; it does **not** replace a certified CTT.

| Commit | Work item | Seam | Priority | Notes |
|---|---|---|---|---|
| **4.2.1** | Generalize `Retimer`-style telemetry into `EthPhy(abc.ABC)` + `MockPhy` (+ vendor stub, e.g. TI DP83TG721 / Marvell): `read_sqi()`, `read_link_up_time()`, `run_tdr()`, `master_slave_role()`, PRBS. Single `PhyError` mirroring `RetimerError`. | `ethernet.py` + `pcie/retimer/base.py` (structural) | high | Structurally identical to `read_eye`/`run_prbs_bist`/`lane_status`. Reuses `EthHealth.role` + `cable_test`. |
| **4.2.2** | **NEW HAL: `TimeIntervalAnalyzer(SCPIInstrument)`** (1PPS phase / Max\|TE\|) + `TSNTrafficGenerator` facade, behind the existing `_MockSCPI`/`_PyVisaTransport` split + `InstrumentError`. Unit-tests on macOS with no bench. | `instruments.py` | high | Keysight 53230A 1PPS; VIAVI TTworkbench+M1 / Spirent / Keysight as real backends. |
| **4.2.3** | **802.1AS gPTP Max\|TE\|**: recovered-clock Maximum Time Error over an observation window; Avnu criterion = lock within **6 s to ±80 ns** of the directly-connected master, held over a **5-minute** window; per-hop ~±80 ns (~540/7); 1 µs p-p / ≤7 hops design target. Emit max/mean/p-p TE + lock-acq time. | `io/ocpdiag.py` (`emit_*` + `LESS_THAN_OR_EQUAL` validator, like `emit_bert` target_ber) | high | Methods: 1PPS / Ingress-Reporting / Reverse-Sync; if multiple used, **all must pass**. |
| **4.2.4** | **802.1AS protocol conformance**: Pdelay turnaround, correction field = upstream delay + residence time, `neighborRateRatio`/syntonization, `asCapable` gating, BMCA election+failover, Sync/Announce interval+timeout. Negative tests (malformed/absent Follow_Up). | `EthPhy` (4.2.1) + offline `linuxptp` pmc/Wireshark gPTP dissector | high | Software-lab tier needs no full bench. |
| **4.2.5** | **802.1Qbv time-aware-shaper gate timing**: gate open/close at GCL-scheduled instants within tolerance; protected windows stay clear; guard-band suppresses a frame that can't finish before close. Window-edge jitter (ns) as a `measurementSeries` (one element/frame, like `emit_margin_series`). | `io/ocpdiag.py` (series) + `TSNTrafficGenerator` | high | Tester shares the DUT gPTP time base. |
| **4.2.6** | **802.1Qbu + 802.3br frame preemption (UNH-IOL Clause 99)**: the full public Groups 1–7 (99.1.1–99.7.1) — reception (13 tests incl. Verify-accept 99.1.12 / Respond-accept 99.1.13), rejection, transmission, Verify-tx, Respond, AEC-TLV/LLDP, prioritization. Verify/Respond handshake, verifyTime 1–128 ms × 0.8 window, addFragSize 0–3 → 64/128/192/256 B, SMD-S/C/E/V/R, mCRC. | each Group → an ocp-diag `testStep` + diagnosis | high | **Most immediately back-fillable** — the 7-group list is public; enumerate it into the matrix. |
| **4.2.7** | **802.1CB FRER**: R-TAG seq# at the split point; Sequence/Individual Recovery eliminate duplicates; sweep loss/dup/reorder; force a path failure mid-stream → uninterrupted (fail-operational) exactly-once delivery. | `TSNTrafficGenerator` (two impaired ports) | med | Platform's own redundancy validation — not an attack. |
| **4.2.8** | **802.1ASdm hot-standby / redundant-domain failover** (automotive fail-operational time-sync): force primary-GM loss mid-run; record slave TE transient + recovery; assert it stays in the holdover budget and the slave never drops `asCapable` on the surviving domain. | TE rig (4.2.3) + 2nd GM | med | Needs a **second/hot-standby grandmaster**. |
| **4.2.9** | **PHY PMA electrical (OPEN Alliance TC8 L1 / UNH-IOL Cl.96 & Cl.97)**: droop/jitter/PSD/MDI-return-loss(Sdd11)/mode-conversion/common-mode (OABR_PMA_TX_01..07), link-up time (LINKUP_01/02/03), SQI coherence (SIGNAL_01/02). Reuse `Scope` (≥4 GHz) + the **GMSL VNA driver from 4.1.6** for Sdd11/mode-conversion. | `instruments.py` (`Scope` + VNA) | med | Cross-subsystem HAL reuse: the 4.1.6 VNA serves both. |
| **4.2.10** | **PHY cable diagnostics / TDR** (OABR_CABLE_01/02 open/short + distance): reuse `ethernet.py` `_real_cable_test` (`ethtool --cable-test-tdr`, `status`/`faults[].code`/`distance_m`) on the automotive single pair. | `ethernet.py` (existing TDR) | low | Near-zero new code. |
| **4.2.11** | **`tsn_adapter.py`**: clone `lmt_adapter.py`'s stable-column pattern (`to_json`/`to_csv` for BigQuery/Looker) + a `call_ttworkbench()`/`call_linuxptp()` drop-in mirroring `is_pci_lmt_available()`/`call_pci_lmt()`, so a lab can use the certified CTT while keeping one output contract. | `lmt_adapter.py` | med | Qav credit-shaper + Qci/PSFP + end-to-end latency/jitter ride this adapter as low-pri follow-ons. |
| **4.2.12** | **Three coverage matrices + gate clones** under `docs/tsn/`: (a) Avnu/UNH-IOL gPTP, (b) UNH-IOL Clause 99 (rows 99.1.1–99.7.1, fully enumerable), (c) OPEN Alliance TC8 L1 (OABR_* IDs). Each reuses the v25 gate (`NODE_RE`, `SCHEDULED_MARKERS`, budget cap). | coverage-gate pattern | high | See §4. |

**Sequence:** 4.2.1 + 4.2.2 (foundations) → 4.2.6 (Clause 99 — public, highest back-fill) → 4.2.3/4.2.4 (gPTP core) → 4.2.5 (Qbv) → 4.2.7/4.2.8 (redundancy) → 4.2.9/4.2.10 (PHY PMA, reuse VNA) → 4.2.11/4.2.12.

---

### 4.3 — DDR5 SDRAM RAS (JEDEC JESD79-5D)

> Three layers the toolkit can own: SI/training conformance at the PHY, module conformance (SPD/RCD/SPD5-hub), and **RAS robustness** — verifying the platform's own on-die ECC / ECS / CRC / PPR / EDAC-reporting under controlled **ACPI-EINJ** injection. `memory.py` already reads EDAC CE/UE per-DIMM; the gap is the DDR5-specific RAS control plane (ECS/scrub/memory-repair sysfs new in **Linux 6.15**).

| Commit | Work item | Seam | Priority | Notes |
|---|---|---|---|---|
| **4.3.1** | DDR5 RAS adapter on `memory.py`'s EDAC reader: extend beyond `mc/*/{ce,ue}_count` to the Linux 6.15 RAS-control sysfs — `ecs_fruX` (`mode`/`log_entry_type`/`threshold {256,1024,4096}`/`reset`), `scrubX` (`addr`/`size`/`enable_background`/`*_cycle_duration`), `mem_repairX` (`repair_type`/`persist_mode`/`hpa`/`dpa`/bank/row/col/`repair`). Behind the existing `mock_mode()` split (deterministic in-memory model; real path `pragma:no-cover`). | `memory.py` | high | Makes ECS/PPR unit-testable on macOS exactly like the CE/UE counters. |
| **4.3.2** | **On-die ECC correction + reporting (EINJ)**: inject 1-bit CE (`error_type=0x8`) at a known addr → assert EDAC `mc` CE counter +1, correct DIMM silkscreen attribution, rasdaemon/dmesg CE log; inject 2-bit → assert reported **UE not silently corrected** (on-die ECC is SEC-only). Cross-check MR20 (Error Count) where a debug path exposes mode registers. | `memory.py` + `io/ocpdiag.py` (`EQUAL 1` validator on `ce_delta`) | high | **Caveat (open Q):** confirm the injector hits the *reportable* IMC CE path (Flags=0x9 component-syndrome / vendor `error_type` bit 0x80000000) and isn't absorbed by on-die ECC first. |
| **4.3.3** | **ECS control + counter readback**: drive `ecs_fruX` (settings stick on read-back), trigger a scrub cycle, read accumulated corrected-error count + "row with max errors" (MR16-18 / MR19 / MR20); assert full-array scrub ≤24 h (scaled). | `memory.py` (4.3.1) | high | Conformance of the scrub-reporting contract — not fault injection. Run HOT. |
| **4.3.4** | **PPR self-heal**: `mem_repairX` `repair_type=ppr`, `persist_mode=0` (sPPR/temporary) vs `1` (hPPR/permanent); target a failing addr, confirm it no longer reports errors, confirm sPPR reverts after power-cycle and hPPR survives. MR54-57 hPPR-resource advertisement. | `memory.py` + `instruments.py` (`PowerSupply` OUTP OFF/ON for the cycle) | high | Reuses HAL power path for persistence test. |
| **4.3.5** | **DDR5 training/margin adapter**: mirror `lmt_adapter.py` (`LmtLaneRecord` + `COLUMNS`) — per-byte-lane/per-DQ records with `timing_ui`/`voltage_mv`/DFE-tap columns projected from MRC/AGESA training results, in a stable column set so the same consumers ingest memory margin like PCIe lane margin. | `lmt_adapter.py` | med | Also the formal OEM bring-up gate (Intel MRC / AMD AGESA). |
| **4.3.6** | **DDR5 PHY/byte-lane eye+DFE object**: copy `EyeMeasurement`(eye_ui/eye_mv/height_mv/width_ui) + `EqLevels`(dfe_taps) + `eye_quality_verdict` shape for per-DQ/DQS eye at the DRAM ball (JEDEC methodology, interposer de-embed). Orchestrate or ingest a scope-vendor compliance app via a new SI instrument wrapper. | `pcie/retimer/base.py` (dataclass reuse) + `instruments.py` | med | App = **Keysight D9050DDRC** (NOT N8841A — that's a CAUI-4 100G app). |
| **4.3.7** | **Link CRC + C/A parity (ALERT_n)**: read MR CRC-enable bits + CRC-retry counters where exposed; assert ALERT_n recovery plumbing. In CI, assert config + reporting plumbing (physical CRC stimulus needs an interposer). | `memory.py` | med | Config-only where the IMC lacks host-readable CRC counters (open Q). |
| **4.3.8** | **RFM/PRAC enablement + alert-path** verification *only*: read MR enablement bits, assert RFM/PRAC enabled per the part's capability + OEM policy and the activation-count alert path is wired. | `memory.py` | med | **Strictly** enablement + alert-path — no activation-pattern/disturbance stress. |
| **4.3.9** | **SPD/RCD/SPD5-hub module conformance** + **SPD corpus**: parse SPD (JESD400-5) over SMBus/I3C-Basic to the SPD5118 hub, assert identity/timings/RAS bits vs `fixture_map`; TS readout (JESD300-5) + per-block write-protect; RCD (JESD82-511..514, DID 0x0051-0x0054). Add a `corpus/ddr5_spd/` tree with `expected.json` (like `corpus/nvme_ocp_internal_log`). | `fixtures.py` + corpus pattern | low | Pure parser tier — no hardware. |
| **4.3.10** | **ECC reporting-plumbing soak (HOT)** + **coverage matrix + gate clone**: extend `stress_memory`+`check_memory` to snapshot ECS/PPR across a `stressapptest` soak at a T/V corner; then `docs/memory/ddr5_jesd79_5_coverage_matrix.md` + `tests/test_ddr5_coverage.py`, with the **JESD79-5 rev (5C/5C.01/5D) as an explicit axis** and the high-pri RAS rows wired to real nodes. | `memory.py` + coverage-gate pattern | low (soak) / high (matrix) | See §4. |

**Sequence:** 4.3.1 (sysfs adapter) → 4.3.2 (EINJ CE/UE — highest-value, software-only) → 4.3.3/4.3.4 (ECS/PPR) → 4.3.9 (SPD corpus, parallel, hardware-free) → 4.3.5/4.3.6 (margin/eye) → 4.3.7/4.3.8 (CRC/PRAC config) → 4.3.10 (soak + matrix).

---

### 4.4 — CXL 2.0/3.x (protocol conformance + RAS)

> The deepest software-testable RAS surface: CXL.cachemem errors surface as PCIe **AER CIE/UIE** (so `aer.py`'s W1C engine transfers verbatim), device errors arrive as **Event Records over the Mailbox/CCI** (so the NVMe-MI `MctpTransport`/`MiMessage` pattern is the template), and every field/bit name is already enumerable from Linux (rasdaemon `ras-cxl-handler.c`, `cxl-cli`). New code lands under `computetest/cxl/` mirroring `pcie/` and `nvme/`. Official 2.0 (incl. host) compliance began **Dec 2024**; the CTS is member-distributed (use spec-section surrogates first, as the NVMe-MI matrix did).

| Commit | Work item | Seam | Priority | Notes |
|---|---|---|---|---|
| **4.4.1** | `cxl/` package + `cxl/mailbox.py` CCI abstraction mirroring `nvme/mi` (`MctpTransport` ABC + `MockMctpTransport`; `MiMessage`/`encode`/`decode`): Identify / Get Partition / Get Health / Get-Clear Event Records / Get Poison List / Get-Set Features / Perform Maintenance / FW / Timestamp / Sanitize. Backend-style Mock for tests + in-band sysfs/MMIO + QEMU real path. | `nvme/mi/mctp.py`+`messages.py` (template) | high | The CCI seam is already proven by NVMe-MI. |
| **4.4.2** | `cxl/ras.py`: CXL RAS-Capability + AER UE/CE decode patterned on `aer.py`'s W1C `_clear_one` (read status, write-back set bits, verify stuck). Encode the exact UE bits (CACHE/MEM DATA/ADDR/BE PARITY+ECC, REINIT_THRESH, POISON, INTERNAL_ERR, IDE_TX/RX_ERR) + CE bits (DATA_ECC, CRC/RETRY_THRESH, POISON, PHYS_LAYER_ERR) — **all verbatim from rasdaemon `ras-cxl-handler.c`**. RCH/RCD (RCRB MEMBAR0 + RCEC) vs VH path. | `aer.py` | high | W1C primitive transfers unchanged. |
| **4.4.3** | **Link bring-up / alternate-protocol negotiation**: read negotiated mode from the Flex Bus DVSEC + PCIe link-status; assert speed (32/64 GT/s), width, **flit mode** (68B vs 256B std vs 256B latency-optimized); flag silent degrade-to-68B / fallback-to-plain-PCIe as fail. Extend the `linkstate.check_link` expected-speed/width pattern with a CXL-aware "expected flit mode". | `linkstate.py` | high | Bench tier captures the LTSSM Config.Lanenum.Wait/Accept + Config.Complete modified-TS1/TS2 exchange. |
| **4.4.4** | `cxl/events.py`: Event Record dataclasses mirroring rasdaemon's field set (General Media / DRAM / Memory Module / Memory Sparing) + Get/Clear flow across all four logs (Informational/Warning/Failure/Fatal); Clear-by-handle removes exactly the cleared records; version-tag for rev3.2 format deltas. **Corpus** `corpus/cxl/` with `expected.json`. | `cxl/mailbox.py` + corpus pattern | high | Parser tier needs no hardware. |
| **4.4.5** | **Poison list & poison/viral containment**: model the `cxl-cli`/`ndctl` `cxl-poison.sh` flow (inject via debugfs hook → read list by memdev+region → assert HPA/DPA+length → clear → assert empty); source/trace/flag enums verbatim from rasdaemon; viral via DVSEC bits; DPC fallback where poison containment absent. | `cxl/ras.py` + `cxl/events.py` | high | Standards-defined containment/recovery view (OCP "Using Poison to Contain and Recover"). |
| **4.4.6** | **HDM Decoder programming & interleave**: read decoder/region/memdev sysfs; assert committed `interleave_ways`/`interleave_granularity` + HPA→DPA; compute expected endpoint position with `cxl_calc_interleave_pos` (`pos = pos*parent_ways + parent_pos`); MLD per-LD interleave. Topology fixture like `configs/example_topology.yaml`. | `cxl/hdm.py` + `topology.py`/`configs` | high | QEMU CXL topology (host bridge + switch + type3) for hardware-free CI. |
| **4.4.7** | **ocp-diag CXL projection**: each conformance item → a `testStep` with measurements (negotiated flit mode, speed/width, decoder interleave params) + a diagnosis (PASS/FAIL via `_DIAGNOSIS_TYPE`); each Event Record / AER decode → a `measurementSeries`/extension artifact. | `io/ocpdiag.py` | high | Reuses the schema-v2 emitter unchanged. |
| **4.4.8** | **Coherency (Type 1/2/3, HDM-H vs HDM-D/DB)** + **device management & FW lifecycle**: identify class from IDENTIFY+DVSEC, assert HDM-D[B]/BI-snoop capability + that a Type-3 doesn't claim cache; wrap Identify/Get Partition/Health/Timestamp/FW Update+Activate/Sanitize (mirror kernel `cxl_mem_*` helpers) with corpus parser tests + the media-not-ready-until-complete contract. | `cxl/mailbox.py` | med | Device-management/serviceability view (no secure-erase-bypass framing). |
| **4.4.9** | **Memory RAS maintenance** (PPR/sparing/ECS/patrol-scrub/BIST) via **Perform Maintenance Opcode 0600h** (class 06h "Maintenance"/sub 0h "Perform"), op-class selecting feature (PPR=01h sPPR/hPPR; sparing=separate class — **confirm exact codes from CTS/spec**, the brief's "03h/00h" was wrong); query-mode doesn't mutate; Background Command Status contract; second-CCI-returns-Busy. | `cxl/mailbox.py` | med | QEMU `cxl_type3` Maintenance+PPR is upstream. |
| **4.4.10** | **CXL link-extension (retimer)**: reuse the `Retimer` ABC verbatim (`info`/`read_eye`/`read_eq`/`read_temperature`/`set_loopback`/`run_prbs_bist`, PRBS31 default) + CXL-context lane thresholds; assert the retimer is the expected part via `info()` vs the fixture map. | `pcie/retimer/base.py` (verbatim) + `pcie/retimer/aries.py` stub | med | CXL retimers are a tested device class in the Compliance Program. |
| **4.4.11** | **QEMU `cxl_type3` injection client** mirroring `sim/qemu/inject.py` (the QMP `pcie_aer_inject_error` → `FakeQMPServer` pattern): RAS error injection + poison injection (emits a GMER) + Maintenance/PPR, for hardware-free RAS-path CI. | `sim/qemu/` + `tests/test_qmp_inject.py` (template) | med | **Audit QEMU fidelity** vs real silicon (which UE/CE bits, event types, Maintenance cmds emulated) — analogous to the Sprint-2 x86-TCG AER-bug discovery. |
| **4.4.12** | **Advanced CVME Threshold + Component Identifier**, **FW-First vs OS-First (CPER/GHES)**, and the **coverage matrix + gate clone**: `docs/cxl/cxl_cts_coverage_matrix.md` (5-col ## Spine) + `tests/test_cxl_cts_coverage.py`; start the scheduled-budget **high** and pay down per sprint exactly as Sprint 3.3 did for NVMe-MI (use spec-section surrogates until the CTS lands). | `io/ocpdiag.py` + coverage-gate pattern | med (CVME/CPER) / high (matrix) | See §4. |

**Sequence:** 4.4.1 + 4.4.2 (CCI + RAS engines, both off proven templates) → 4.4.4/4.4.5 (Event Records + poison, hardware-free corpus) → 4.4.3/4.4.6 (link + HDM) → 4.4.7 (ocp-diag) → 4.4.11 (QEMU CI) → 4.4.8/4.4.9/4.4.10 (mgmt/maintenance/retimer) → 4.4.12 (matrix, budget paid down over later sprints).

---

## 3. New instrumentation / HAL needs across all four subsystems

All new instruments extend `SCPIInstrument` behind the existing `_MockSCPI`/`_PyVisaTransport` split with the single `InstrumentError` type, so every one is unit-testable on macOS with no bench (the established Sprint-2.1 contract). Register each via `register_instrument_kind()` for `fixture_map.yaml` role binding.

| New HAL class / capability | Subsystems | Real backend(s) | Mock behavior | Notes |
|---|---|---|---|---|
| **`Vna(SCPIInstrument)`** — 4-port differential S-parameter capture + `.s4p` post-processor | **GMSL3 (4.1.6)**, **TSN PMA (4.2.9)** | Keysight / Copper Mountain VNA + FAKRA/Mini-FAKRA/HSD/STQ cal fixtures, TRL/SOLT | deterministic `.s4p` returning a pass/fail-able sweep | **One driver serves both** GMSL channel IL/RL and TSN MDI Sdd11/mode-conversion. GMSL mask blocked on AN-2585 PDF transcription. |
| **`ThermalChamber(SCPIInstrument)`** — setpoint + readback | **GMSL3 (4.1.10)**, **DDR5 (4.3.3/4.3.10)** | thermal chamber / thermo-stream | instant settle to setpoint | Drives the temperature axis of every V/T shmoo. AEC-Q100 Grade-2 −40…+105 °C for GMSL. |
| **`TimeIntervalAnalyzer(SCPIInstrument)`** — 1PPS phase / Max\|TE\| | **TSN (4.2.2/4.2.3/4.2.8)** | Keysight 53230A; or HW-timestamping TSN tester | deterministic TE samples | Avnu 1PPS clock-accuracy method. |
| **`TSNTrafficGenerator`** facade | **TSN (4.2.5/4.2.6/4.2.7)** | VIAVI TTworkbench+M1, Spirent, Keysight; AF_PACKET/DPDK HW-timestamp NIC (lab approximation) | scriptable frame/timestamp model | Drives Qbv gate timing, Clause-99 mPacket injection, FRER impaired streams. |
| **`CxlAnalyzer` / `CxlExerciser`** | **CXL (4.4.3)** | Teledyne Summit **T516/Z516** (cert'd 1.1/2.0) + **M64/M616** (64 GT/s 3.x); Keysight **P5562CXLA** analyzer + **P5561CXLA** exerciser | LTSSM/flit-capture mock | M64/M616 are 3.x-capable but **not** a published 3.x cert — don't claim otherwise. |
| **SI compliance-app wrapper** (DDR5) — orchestrate or ingest result file | **DDR5 (4.3.6)** | **Keysight D9050DDRC** DDR5 Tx app (NOT N8841A); Tek + SDLA; R&S | ingest a synthetic result file | Wraps the app the way `ExternalBERT` wraps a bench BERT. |
| **`ExternalBERT` PRBS24 addition** to `_BERT_PATTERNS` | **GMSL3 (4.1.3/4.1.10)** | existing Keysight M8040 / Anritsu MP1900A / Tek BSAVx40 | n/a (pattern-set extension) | Current set = {PRBS7,9,15,23,31,USER}; add PRBS24 *or* document PRBS31-only correlation. **Confirmed gap.** |

**Reused as-is (no new HAL):** `PowerSupply`/`DMM` (rail margining + power-fault + power-cycle, all four), `SwitchMatrix` (GMSL line-fault open/short), `Scope` ≥4 GHz (GMSL eye reference + TSN PMA droop/jitter/PSD/common-mode), `ElectronicLoad`/`SMU` (GMSL PoC supply characterization), and the existing `sim/qemu` QMP-inject harness (CXL `cxl_type3` injection, DDR5 EINJ is pure debugfs/sysfs — no instrument at all).

**Cross-subsystem leverage worth calling out:** the **4-port VNA driver (4.1.6) is shared by GMSL channel compliance and TSN MDI return-loss/mode-conversion (4.2.9)**, and the **thermal-chamber driver is shared by GMSL and DDR5 shmoos** — build each once.

---

## 4. New UNH-IOL-style coverage-matrix rows for the CI gate

Each new subsystem gets its own matrix file + a **clone of `tests/test_unh_iol_v25_coverage.py`** (the gate parses `## Spine`, exactly-5-cell `| Group | Test ID | Title | Type | pytest_node |` rows via `NODE_RE`, honors `SCHEDULED_MARKERS`, and enforces `EXPECTED_SCHEDULED_BUDGET`). The proven discipline: **start the scheduled budget high, pay it down one row per sprint** (the Sprint 3.3 → budget 0 playbook). A Mandatory row with an empty or unresolvable `pytest_node` fails CI; `_scheduled_*` rows are tracked debt under the budget cap.

**Per-matrix header note (mandatory framing):**
- **GMSL3** — "Mandatory = **ADI-spec-mandatory** (AN-2585/UG-2208), NOT plugfest-mandatory; no third-party conformance authority exists for GMSL."
- **TSN** — "Mandatory per Avnu / UNH-IOL / OPEN Alliance; rows traceable to public IDs first, paywalled per-test tables back-filled on membership."
- **DDR5** — "JESD79-5 **rev (5C/5C.01/5D) is an explicit matrix axis**; JEDEC spec text paywalled — assert against Linux ABI + ACPI mechanisms."
- **CXL** — "CTS member-distributed; **spec-section surrogates** until per-test IDs obtained (the NVMe-MI playbook)."

### 4.1 GMSL3 — `docs/gmsl/gmsl3_channel_validation_matrix.md` (gate: `tests/test_gmsl3_coverage.py`)

| Group | Test ID | Title | Type | Backing commit |
|---|---|---|---|---|
| Link | 1.1 | Lock from cold + after forced re-train, all enabled links | Mandatory | 4.1.2 |
| Link | 1.2 | Negotiated MODE assertion (PAM4-12G vs NRZ-6G vs GMSL2/1 fallback) | Mandatory | 4.1.2 |
| BER | 2.1 | Forward PRBS pre/post-FEC BER + confidence bound | Mandatory | 4.1.3 |
| BER | 2.2 | Reverse PRBS BER | Mandatory | 4.1.3 |
| FEC | 3.1 | RS corrected-bit rate + uncorrectable-block = 0 | Mandatory | 4.1.4 |
| Eye | 4.1 | EOM vertical+horizontal margin per link (PAM4 three-eye) | Mandatory | 4.1.5 |
| Channel | 5.1 | IL short/long-channel mask (filtered Sdd21/Sdd12) | Mandatory | 4.1.6 |
| Channel | 5.2 | RL mask + 2–10 MHz PoC carve-out | Mandatory | 4.1.6 |
| Video | 6.1 | CSI-2 video-CRC counter unchanged over soak + VC/data-type map | Mandatory | 4.1.9 |
| Control | 7.1 | Control-packet CRC + seq# + ARQ retransmit | Mandatory | 4.1.9 |
| Safety | 8.1 | ERRB asserts on line-fault; counter latches; clear de-asserts | Mandatory | 4.1.8 |
| Margin | 9.1 | V/T shmoo across Grade-2 −40…+105 °C | FYI→Mandatory | 4.1.10 |
| SSC | 10.1 | Lock+BER+eye with SSC enabled | FYI | 4.1.10 (follow-on) |

### 4.2 TSN — three matrices under `docs/tsn/`

**(a) `gptp_avnu_coverage_matrix.md`** (gate `tests/test_tsn_gptp_coverage.py`): gPTP-Max\|TE\| (Mandatory→4.2.3), Pdelay/correction-field/rateRatio/asCapable (Mandatory→4.2.4), BMCA election+failover (Mandatory→4.2.4), hot-standby/redundant-domain failover (Mandatory→4.2.8).

**(b) `clause99_preemption_coverage_matrix.md`** (gate `tests/test_tsn_clause99_coverage.py`) — **fully enumerable now**: rows **99.1.1–99.1.13** (reception incl. Verify-accept 99.1.12 / Respond-accept 99.1.13), **99.2.1–99.2.7** (rejection), **99.3.1–99.3.5** (transmission), **99.4.1–99.4.4** (Verify-tx), **99.5.1–99.5.2** (Respond), **99.6.1** (AEC-TLV/LLDP), **99.7.1** (prioritization) — all Mandatory → 4.2.6. *This is the single most back-fillable new matrix.*

**(c) `open_alliance_tc8_l1_coverage_matrix.md`** (gate `tests/test_tsn_tc8_l1_coverage.py`): OABR_PMA_TX_01..07, OABR_LINKUP_01/02/03, OABR_SIGNAL_01/02 (Mandatory→4.2.9), OABR_CABLE_01/02 (Mandatory→4.2.10). Qbv gate timing (Mandatory→4.2.5), FRER (Mandatory→4.2.7), and Qav/Qci/end-to-end-latency (FYI→4.2.11) can live in a small (d) `tsn_features_coverage_matrix.md` or fold into (a).

### 4.3 DDR5 — `docs/memory/ddr5_jesd79_5_coverage_matrix.md` (gate `tests/test_ddr5_coverage.py`)

| Group | Test ID | Title | Type | Backing commit |
|---|---|---|---|---|
| ECC | 1.1 | EINJ 1-bit CE → EDAC CE +1, correct DIMM, rasdaemon log | Mandatory | 4.3.2 |
| ECC | 1.2 | EINJ 2-bit → reported UE (not silently corrected) | Mandatory | 4.3.2 |
| ECS | 2.1 | ECS config sticks; corrected-count + max-error-row readback | Mandatory | 4.3.3 |
| ECS | 2.2 | Full-array scrub ≤24 h (scaled) | Mandatory | 4.3.3 |
| PPR | 3.1 | sPPR reverts on power-cycle; hPPR survives; MR54-57 advertised | Mandatory | 4.3.4 |
| CRC | 4.1 | Write/read-CRC enable + ALERT_n recovery plumbing | Mandatory* | 4.3.7 |
| RFM | 5.1 | RFM/PRAC enablement + alert-path wired | Mandatory* | 4.3.8 |
| Train | 6.1 | Per-DQ Vref/DFE/leveling margin floor across ±5% V & T | FYI | 4.3.5 |
| SI | 7.1 | Per-DQ eye/DFE at DRAM ball vs JESD79-5 mask | FYI | 4.3.6 |
| Module | 8.1 | SPD/RCD/SPD5-hub identity vs fixture map | FYI | 4.3.9 |

\* Config-only where the IMC lacks host-readable CRC/PRAC counters (open Q 4.3-CRC/PRAC). Axis: JESD79-5 rev.

### 4.4 CXL — `docs/cxl/cxl_cts_coverage_matrix.md` (gate `tests/test_cxl_cts_coverage.py`, **budget starts high, pays down**)

| Group | Test ID | Title | Type | Backing commit |
|---|---|---|---|---|
| Link | §6.x | Alt-protocol negotiation + flit-mode assertion (no silent 68B/PCIe fallback) | Mandatory | 4.4.3 |
| RAS-AER | §8.2.8 | CXL.cachemem UE/CE via AER CIE/UIE decode + W1C | Mandatory | 4.4.2 |
| Events | §8.2.9.2.2 | Get/Clear Event Records, 4 logs, GMER/DRAM/MemMod/Sparing parse | Mandatory | 4.4.4 |
| Poison | §8.2.9 | Poison list inject/clear round-trip + viral/DPC fallback | Mandatory | 4.4.5 |
| HDM | §8.2.4 | Decoder commit + interleave HPA→DPA (`cxl_calc_interleave_pos`) | Mandatory | 4.4.6 |
| Mgmt | §8.2.9 | Identify / Partition / Health / FW Update+Activate / Sanitize | Mandatory | 4.4.8 |
| Maint | 0600h | Perform Maintenance (PPR/sparing/ECS/scrub/BIST) | FYI→Mandatory | 4.4.9 |
| Retimer | — | Link-extension eye/EQ/PRBS via Retimer ABC | Mandatory | 4.4.10 |
| Coherency | §8.2.4 | HDM-H vs HDM-D/DB capability consistency | FYI | 4.4.8 |
| Delivery | — | FW-First (CPER/GHES) vs OS-First (MSI/tracepoint) | FYI | 4.4.12 |

---

## 5. Open questions / risks

**Cross-cutting (affect all four):**
- **Paywalled per-test tables.** UNH-IOL v25, Avnu/OPEN Alliance TSN plans, the JEDEC spec text, and the CXL CTS are all member-distributed. Mitigation (proven in Sprint 2.4): name groups + use spec-section surrogates, gate on `_scheduled_` debt, back-fill IDs on membership. **The TSN Clause-99 matrix is the exception — fully public and enumerable now.**
- **QEMU/Mock fidelity as CI ground truth.** Sprint 2 already discovered a real QEMU x86-TCG AER bug. Audit `cxl_type3` (which UE/CE bits, event types, Maintenance/sparing/ECS commands are emulated) before trusting it as CXL CI truth; same caution for the GMSL/DDR5/TSN deterministic mocks — they encode *expected* behavior, not silicon.

**GMSL3 (4.1):**
- **AN-2585 numeric IL/RL limit lines** (dB vs frequency, short/long masks) timed out on automated fetch — **must be transcribed from the official PDF before the S-parameter mask (4.1.6) is coded. Do NOT hardcode guesses.** Same for the MAX96793/MAX96792A per-part register addresses (EOM, Link-vs-Video PRBS, error counters, ARQ counter, FEC counters).
- **EOM units** — absolute UI/mV vs relative "EOM step"? Decides whether `gmsl_lmt` (4.1.7) emits true or relative margin. Needs UG-2208.
- **On-die PRBS pattern** — community refs cite PRBS24; `ExternalBERT` lacks it. Decides whether external-BERT correlation (4.1.10) is bit-identical or same-order-of-magnitude.
- **DriveOS golden-value OPCODE byte** unconfirmed (brief's 0x80 is *also* a default serializer I2C address). AEC-Q100 grade + exact rail set per part number still need the (fetch-blocked) datasheet.

**TSN (4.2):**
- **End-station vs bridge?** Avnu splits Component vs Switch certification; the Qbv/Qbu/FRER surface differs substantially (bridges add residence-time + GCL + relay). This changes which matrix rows are Mandatory.
- **Which TSN features are in silicon/driver** (Qbv via `tc-taprio`, preemption via `ethtool --set-mm`, Qav via `tc-cbs`)? The matrix should only gate implemented features.
- **Real numeric budgets** — Avnu's 6 s / ±80 ns / 5-min window is the floor; automotive integrators tighten via a CDS. The validators need the program's real thresholds (incl. hot-standby transient budget).
- **Certified CTT access** — does the lab get a real CTT for the `call_ttworkbench` drop-in, or stand alone on a linuxptp + HW-timestamp-NIC rig (protocol conformance, but not Avnu-grade Max\|TE\| certification)?

**DDR5 (4.3):**
- **EINJ reaches the reportable layer?** On-die ECC may silently correct a CE before it hits the IMC. Confirm the platform's injection exercises the reportable CE path (Flags=0x9 component-syndrome / vendor `error_type` bit 0x80000000) so 4.3.2 asserts the right error-handling stage.
- **Kernel ≥ 6.15 with the EDAC scrub/`ecs_fruX`/`mem_repair` drivers enabled for this IMC** (native DDR vs CXL)? Older kernels give only CE/UE counters → 4.3.3/4.3.4 degrade to read-only.
- **IMC exposes CRC/PRAC + host-readable counters?** (Consumer AM5/LGA1700 inconsistent vs server.) Determines whether 4.3.7/4.3.8 are config-only or measurable.
- **MRC/AGESA training telemetry at runtime vs only POST?** Decides whether 4.3.5 reads live values or parses a boot log. **Rev pinned (5C/5C.01/5D)?** — the matrix axis needs it.

**CXL (4.4):**
- **CTS version + per-test ID scheme + mandatory/optional split per device class** (host vs Type-3 vs switch vs retimer) — needs the member doc; matrix traces spec sections until then.
- **No confirmed UNH-IOL CXL Integrators-List path** — treat UNH-IOL strictly as the *analogous pattern*, not an actual CXL provider, until confirmed.
- **Unverified field refs to confirm from the spec/CTS before encoding as assertions:** the Component-Identifier table number (brief's "Table 8-43" — **UNVERIFIED**); the Perform-Maintenance per-feature operation-class/subclass codes (Opcode 0600h confirmed; PPR=01h; sparing/ECS/scrub/BIST class numbers **unconfirmed** — the brief's "03h/00h" was *wrong*); DSP0248 §9.1/v1.2.2 (**unverified**).
- **In-band tier vs analyzer tier boundary** — how much LTSSM/flit/coherency conformance is assertable purely from sysfs/DVSEC/mailbox (CI-friendly) vs requires an exerciser capture? Needs a real-bus spike (the post-Sprint-3 "real-bus NVMe-MI" work is the template). **3.2-specific items** are not yet in Test Events — tag as FYI.

---

**Suggested sprint ordering:** **4.1 GMSL3** (largest gap vs existing code; pure register/instrument; reuses retimer abstraction) → **4.2 TSN** (Clause-99 matrix is public and immediately back-fillable; reuses `ethernet.py` + the GMSL VNA) → **4.3 DDR5** (software-only EINJ/ECS/PPR lands fast; gated on kernel 6.15) → **4.4 CXL** (deepest RAS surface but highest open-question/QEMU-fidelity load; parser/corpus tiers land hardware-free first, matrix budget paid down over later sprints). The shared VNA (4.1.6 ↔ 4.2.9) and thermal-chamber drivers mean 4.1 front-loads HAL work that 4.2 and 4.3 then reuse.

---

**Relevant existing files this roadmap maps onto** (all verified, absolute paths):
- `/Users/kywwilson/Desktop/Projects/Zoox/toolkit/src/computetest/gmsl.py`, `ethernet.py`, `memory.py`, `aer.py`, `linkstate.py`, `lmt_adapter.py`, `instruments.py`, `ber.py`, `fixtures.py`, `topology.py`
- `/Users/kywwilson/Desktop/Projects/Zoox/toolkit/src/computetest/io/ocpdiag.py`
- `/Users/kywwilson/Desktop/Projects/Zoox/toolkit/src/computetest/pcie/retimer/base.py`, `aries.py`
- `/Users/kywwilson/Desktop/Projects/Zoox/toolkit/src/computetest/nvme/mi/mctp.py`, `messages.py`
- `/Users/kywwilson/Desktop/Projects/Zoox/toolkit/tests/test_unh_iol_v25_coverage.py` (the gate to clone)
- `/Users/kywwilson/Desktop/Projects/Zoox/toolkit/docs/nvme/unh_iol_v25_coverage_matrix.md` (the matrix to clone)
- `/Users/kywwilson/Desktop/Projects/Zoox/toolkit/sim/qemu/inject.py` + `tests/test_qmp_inject.py` (the QEMU-injection template for CXL)
- `/Users/kywwilson/Desktop/Projects/Zoox/toolkit/configs/example_topology.yaml`, `fixture_map.example.yaml` (fixture/topology pattern)
- `/Users/kywwilson/Desktop/Projects/Zoox/toolkit/corpus/nvme_ocp_internal_log/` (the `expected.json` corpus pattern for DDR5 SPD + CXL event records)
