# OPEN Alliance TC8 L1 (PHY PMA) coverage matrix

**Source:** OPEN Alliance TC8 layer-1 (automotive-Ethernet PHY PMA) + UNH-IOL
Clause 96/97 (100/1000BASE-T1).

**Mandatory per OPEN Alliance / UNH-IOL.** The numeric TC8 limit lines are
paywalled, so the S-parameter masks are supplied at runtime (see `tsn.pma`); the
rows below gate the mask-comparison + PHY-telemetry framework. CI gate
(`tests/test_tsn_coverage.py`): every `Mandatory` row's `pytest_node` must
resolve; scheduled-debt budget is 0.

## Spine

| Group | Test ID | Title | Type | pytest_node |
|-------|---------|-------|------|-------------|
| PMA | OABR_PMA_TX | MDI return loss (Sdd11) + mode conversion (Scd21) vs mask | Mandatory | tests/test_tsn_pma.py::TestPma::test_conformant |
| PMA | OABR_PMA_RL | Return-loss mask violation detected | Mandatory | tests/test_tsn_pma.py::TestPma::test_return_loss_violation_fails |
| LINKUP | OABR_LINKUP_01 | Link-up time within budget | Mandatory | tests/test_tsn_pma.py::TestPma::test_slow_linkup_fails |
| SIGNAL | OABR_SIGNAL_01 | SQI coherence floor | Mandatory | tests/test_tsn_pma.py::TestPma::test_low_sqi_fails |
| CABLE | OABR_CABLE_01 | Cable TDR open/short + distance-to-fault | Mandatory | tests/test_tsn_cable.py::TestCableTdr::test_open_fault_fails_with_distance |
| PMA | PMA_BIST | PHY PRBS BIST error counting | Mandatory | tests/test_tsn_phy.py::TestMockPhy::test_prbs_bist |

## Masks — blocked on TC8 transcription

The OABR_PMA return-loss / mode-conversion numeric limits are paywalled and
supplied as `SParamMask` arguments to `tsn.check_pma_electrical`, which refuses
to run without them — the same blocked-but-honest pattern as the GMSL AN-2585
masks. Transcribe the TC8 limits, then add a bench row tying real masks to a run.
