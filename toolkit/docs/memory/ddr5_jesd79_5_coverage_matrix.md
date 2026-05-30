# DDR5 RAS coverage matrix (JESD79-5)

**Source:** JEDEC JESD79-5 (DDR5) + the Linux 6.15 EDAC RAS ABI
(`ecs_fruX` / `scrubX` / `mem_repairX`) + ACPI-EINJ.

**The JESD79-5 revision is an explicit axis** (5C / 5C.01 / 5D): RAS-feature
availability (PRAC/RFM, ECS) differs by rev, so the rows target the 5D superset
and a real run pins the part's rev. The numeric JEDEC limits are paywalled, so
the checks assert against the **Linux ABI + ACPI mechanisms**, not spec values.

CI gate (`tests/test_ddr5_coverage.py`): every `Mandatory` row's `pytest_node`
must resolve; scheduled-debt budget is 0.

## Spine

| Group | Test ID | Title | Type | pytest_node |
|-------|---------|-------|------|-------------|
| ECC | 1.1 | EINJ 1-bit CE to EDAC CE +1 on the correct DIMM | Mandatory | tests/test_ddr5_ecc.py::TestCeReporting::test_ce_reported_on_correct_dimm |
| ECC | 1.2 | EINJ 2-bit reported as UE (not silently corrected) | Mandatory | tests/test_ddr5_ecc.py::TestUeReporting::test_ue_reported_not_corrected |
| ECS | 2.1 | ECS config sticks; corrected-count + max-error row readback | Mandatory | tests/test_ddr5_ecs.py::TestEcs::test_settings_stick_and_scrub_in_budget |
| ECS | 2.2 | Full-array scrub within the 24-hour budget | Mandatory | tests/test_ddr5_ecs.py::TestEcs::test_scrub_time_over_24h_fails |
| PPR | 3.1 | sPPR reverts; hPPR survives power cycle; MR54-57 advertised | Mandatory | tests/test_ddr5_ppr.py::TestPpr::test_hppr_survives_power_cycle |
| CRC | 4.1 | Write/read-CRC + C/A parity enable + ALERT_n plumbing | Mandatory | tests/test_ddr5_crc.py::TestCrcParity::test_full_plumbing_passes |
| RFM | 5.1 | RFM/PRAC enablement + alert-path (enablement only) | Mandatory | tests/test_ddr5_rfm.py::TestRfmPrac::test_supported_and_enabled_passes |
| Train | 6.1 | Per-DQ training margin to pci_lmt schema | Mandatory | tests/test_ddr5_lmt.py::TestTrainingToLmt::test_two_records_per_dq |
| SI | 7.1 | Per-DQ eye/DFE at the DRAM ball | Mandatory | tests/test_ddr5_eye.py::TestDqEye::test_good_eye_passes |
| Module | 8.1 | SPD identity vs the fixture map (corpus) | Mandatory | tests/test_ddr5_spd.py::TestCorpus::test_check_passes_against_expected |

## How to extend

Add a pytest in `tests/test_ddr5_*.py`, then a row here. When the part's
JESD79-5 rev gates a feature (PRAC/RFM are rev-dependent), note the rev in the
title. The gate refuses an empty or unresolvable Mandatory node.
