# UNH-IOL NVMe-MI Conformance Test Suite v25 — coverage matrix

**Source:** UNH-IOL NVMe-MI Conformance Test Suite v25 (Feb 2026, supersedes
v23/Jan 2025 + v24/Jul 2025).

**Purpose:** a living traceability table from each *Mandatory* UNH-IOL test
to the pytest node in `tests/test_nvme_mi*.py` that exercises it. CI gate
goal: every `Mandatory` row must have a `pytest_node` populated. `FYI` rows
are aspirational and don't gate.

UNH-IOL groups its NVMe-MI tests into 12 categories per the v25 TOC:

| #  | Group                          | Tests | Status    |
|----|--------------------------------|-------|-----------|
| 1  | MCTP Base                      | TBD   | scaffolded |
| 2  | MCTP Control Messages          | TBD   | scaffolded |
| 3  | MCTP Commands                  | TBD   | scaffolded |
| 4  | NVMe Error Handling            | TBD   | scaffolded |
| 5  | MI Message Processing          | TBD   | scaffolded |
| 6  | Control Primitives             | TBD   | scaffolded |
| 7  | Management Commands            | TBD   | scaffolded |
| 8  | Admin Command Set              | TBD   | scaffolded |
| 9  | Management Enhancement         | TBD   | scaffolded |
| 10 | VPD (Vital Product Data)       | TBD   | scaffolded |
| 11 | Management Endpoint Reset      | TBD   | scaffolded |
| 12 | Specialized / Misc.            | TBD   | scaffolded |

Per-test rows are populated as the matrix is back-filled against the
UNH-IOL doc (which is paywalled / member-distributed). The verified
research note for the v25 release (2026-05-29 deep-research run) names
the 12 groups but does not enumerate every test ID; the spine below covers
the rows we are most confident about.

## Spine — high-confidence mandatory tests

These rows are tied to pytest nodes already exercising the relevant code
path. As the full v25 doc gets walked, extend this table; CI fails when a
Mandatory row's `pytest_node` column is empty.

| Group | Test ID | Title                                            | Type      | pytest_node                                                                  |
|-------|---------|--------------------------------------------------|-----------|------------------------------------------------------------------------------|
| 1     | 1.1     | MCTP framing — EID range validation              | Mandatory | tests/test_nvme_mi.py::TestMctpEndpointId::test_eid_range_validation         |
| 1     | 1.2     | MCTP framing — broadcast EID handling            | Mandatory | tests/test_nvme_mi.py::TestMctpEndpointId::test_broadcast_eid_is_broadcast   |
| 2     | 2.1     | MCTP control — Get Endpoint ID                   | Mandatory | tests/test_nvme_mi.py::TestMctpMessageRoundTrip::test_get_endpoint_id_via_mock |
| 5     | 5.1     | MI Message Processing — NMP byte field decode    | Mandatory | tests/test_nvme_mi.py::TestMiMessageEncode::test_encode_decode_round_trip    |
| 5     | 5.2     | MI Message Processing — MIC (CRC-32C) verify     | Mandatory | tests/test_nvme_mi.py::TestMiCRC::test_crc_round_trip_detects_corruption    |
| 6     | 6.1     | Control Primitive — Pause                        | Mandatory | tests/test_nvme_mi.py::TestControlPrimitive::test_pause_opcode               |
| 6     | 6.2     | Control Primitive — Resume                       | Mandatory | tests/test_nvme_mi.py::TestControlPrimitive::test_resume_opcode              |
| 7     | 7.5     | NVM Subsystem Health Status Poll                 | Mandatory | tests/test_nvme_mi_extras.py::TestNvmSubsystemHealth::test_mock_poll_returns_healthy_subsystem |
| 7     | 7.6     | Controller Health Status Poll                    | Mandatory | tests/test_nvme_mi.py::TestControllerHealthPoll::test_mock_poll_is_healthy   |
| 9     | 9.1     | Management Enhancement — Async Event Subscribe   | Mandatory | tests/test_nvme_mi.py::TestAemSubscription::test_subscribe_requires_event_set |
| 9     | 9.2     | Management Enhancement — AEM delivery coalesce   | Mandatory | tests/test_nvme_mi.py::TestAemSubscription::test_coalesce_within_window      |
| 9     | 9.3     | Management Enhancement — AEM delivery rate limit | Mandatory | tests/test_nvme_mi.py::TestAemSubscription::test_rate_limit_per_second       |
| 10    | 10.1    | VPD Read — bounds check                          | FYI       | tests/test_nvme_mi_extras.py::TestVpdRead::test_request_overflowing_capacity_rejected |
| 11    | 11.1    | Management Endpoint Reset                        | Mandatory | tests/test_nvme_mi_extras.py::TestManagementEndpointReset::test_mock_reset_default_is_nvm_subsystem_reset |

## How to extend

When the next UNH-IOL doc revision lands (v26+):

1. Diff the v25 → vNew table of contents.
2. For each new Mandatory row, add a pytest in `tests/test_nvme_mi*.py`
   exercising that branch, then add a row here with the `pytest_node`
   pointer.
3. Open a PR; the CI gate refuses an empty Mandatory `pytest_node` column.

(The CI gate itself is Sprint 3 work — for now, this matrix is a manual
traceability artifact.)
