# UNH-IOL Clause 99 frame-preemption coverage matrix

**Source:** UNH-IOL Clause 99 (802.1Qbu + 802.3br frame preemption) conformance
groups 99.1.1-99.7.1. Unlike the other TSN tables, **this test-ID list is public
and fully enumerable** — the single most back-fillable TSN matrix.

**Mandatory** rows map to the implemented preemption mechanism checks; the
AEC-TLV/LLDP and prioritization groups are enumerated as `FYI` pending their
per-test back-fill. CI gate (`tests/test_tsn_coverage.py`): every `Mandatory`
row's `pytest_node` must resolve; scheduled-debt budget is 0.

## Spine

| Group | Test ID | Title | Type | pytest_node |
|-------|---------|-------|------|-------------|
| 99.1 | 99.1.12 | Reception: Verify-accept handshake | Mandatory | tests/test_tsn_preemption.py::TestVerifyRespond::test_success |
| 99.1 | 99.1.A | addFragSize to minimum fragment-size mapping | Mandatory | tests/test_tsn_preemption.py::TestAddFragSize::test_mapping |
| 99.2 | 99.2.A | Rejection: below-floor fragment | Mandatory | tests/test_tsn_preemption.py::TestFragmentation::test_below_floor |
| 99.3 | 99.3.A | Transmission: SMD-S/C sequence + mCRC | Mandatory | tests/test_tsn_preemption.py::TestFragmentation::test_valid |
| 99.4 | 99.4.A | Verify-Tx: verifyTime 1-128 ms range | Mandatory | tests/test_tsn_preemption.py::TestVerifyTime::test_range |
| 99.5 | 99.5.A | Respond: handshake within 0.8x window | Mandatory | tests/test_tsn_preemption.py::TestVerifyRespond::test_late_respond_fails |
| 99.6 | 99.6.1 | AEC-TLV / LLDP preemption advertisement | FYI | _scheduled_ (LLDP back-fill) |
| 99.7 | 99.7.1 | Express vs preemptable prioritization | FYI | _scheduled_ (prioritization back-fill) |

## How to extend

The full Groups 1-7 (reception 99.1.1-99.1.13, rejection 99.2.x, transmission
99.3.x, Verify-tx 99.4.x, Respond 99.5.x, AEC-TLV 99.6.1, prioritization 99.7.1)
are public — enumerate each as its own row with a backing pytest as the
mechanism checks are expanded into per-test cases.
