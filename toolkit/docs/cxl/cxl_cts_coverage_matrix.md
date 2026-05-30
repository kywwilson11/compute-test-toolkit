# CXL CTS coverage matrix

**Source:** CXL 2.0/3.x Compliance Test Specification (CTS) + the CXL base spec.
Official 2.0 compliance testing (incl. host) began Dec 2024.

**The CTS is member-distributed**, so per-test IDs aren't yet public — rows use
**spec-section surrogates** until the per-test tables are obtained, exactly the
NVMe-MI v2.4 playbook. Unlike the other Sprint-4 matrices, the CXL
**scheduled-debt budget starts high and pays down** per sprint (the Sprint 3.3
approach): the Advanced-CVME / Component-Identifier and CPER/GHES-delivery rows
are tracked `_scheduled_` debt pending the CTS + a real-bus spike.

CI gate (`tests/test_cxl_cts_coverage.py`): every `Mandatory` row's `pytest_node`
must resolve OR be explicitly `_scheduled_`; the scheduled count must not exceed
the current budget (2, paying down toward 0).

## Spine

| Group | Test ID | Title | Type | pytest_node |
|-------|---------|-------|------|-------------|
| Link | sec-6 | Alt-protocol negotiation + flit mode (no silent 68B/PCIe fallback) | Mandatory | tests/test_cxl_link.py::TestCxlLink::test_silent_pcie_fallback_fails |
| CCI | sec-8.2.9 | CCI mailbox command/response round-trip | Mandatory | tests/test_cxl_mailbox.py::TestMockMailbox::test_queued_response_round_trip |
| RAS | sec-8.2.8 | CXL.cachemem UE/CE decode + write-1-to-clear | Mandatory | tests/test_cxl_ras.py::TestW1C::test_clear_clears_set_bits |
| Events | sec-8.2.9.2 | Get/Clear Event Records across the four logs | Mandatory | tests/test_cxl_events.py::TestStore::test_clear_by_handle_removes_exactly |
| Poison | sec-8.2.9 | Poison list inject/clear + viral/DPC containment | Mandatory | tests/test_cxl_poison.py::TestPoisonList::test_inject_list_clear_round_trip |
| HDM | sec-8.2.4 | Decoder interleave HPA-to-DPA (cxl_calc_interleave_pos) | Mandatory | tests/test_cxl_hdm.py::TestInterleavePos::test_nested_position |
| Coherency | sec-8.2.4 | HDM-H vs HDM-D/DB capability consistency | Mandatory | tests/test_cxl_device.py::TestCoherency::test_type3_mem_only_consistent |
| Mgmt | sec-8.2.9 | FW/Sanitize media-not-ready-until-complete contract | Mandatory | tests/test_cxl_device.py::TestMediaReadyContract::test_well_behaved_lifecycle |
| Maint | 0600h | Perform Maintenance (PPR) background + busy flow | Mandatory | tests/test_cxl_maintenance.py::TestMaintenance::test_well_behaved_flow |
| Retimer | cts-retimer | Link-extension eye/EQ/PRBS via the Retimer ABC | Mandatory | tests/test_cxl_retimer.py::TestCxlRetimer::test_expected_part_good_eye_passes |
| CVME | sec-8.x | Advanced CVME Threshold + Component Identifier table | Mandatory | _scheduled_ (CTS Component-Identifier table number unverified) |
| Delivery | cper-ghes | FW-First (CPER/GHES) vs OS-First error delivery | Mandatory | _scheduled_ (needs a real-bus spike + the CTS per-test IDs) |

## How to pay down the budget

When the CTS lands (or a real-bus spike validates the path), replace a
`_scheduled_` row's `pytest_node` with a real test and lower
`EXPECTED_SCHEDULED_BUDGET` in `tests/test_cxl_cts_coverage.py` by one — never
raise it. The budget reaching 0 means CXL coverage is fully backed.
