# TSN gPTP + features coverage matrix (Avnu / UNH-IOL)

**Source:** Avnu Alliance TSN certification program (launched Jun 2024) +
IEEE 802.1AS / 802.1Qbv / 802.1CB / 802.1ASdm.

**Mandatory per Avnu / UNH-IOL.** Rows trace to public criteria first; the
paywalled per-test tables are back-filled on membership. CI gate
(`tests/test_tsn_coverage.py`): every `Mandatory` row's `pytest_node` must
resolve to a real test; scheduled-debt budget is 0.

## Spine

| Group | Test ID | Title | Type | pytest_node |
|-------|---------|-------|------|-------------|
| gPTP | TE-1 | Recovered-clock max time error within Avnu limit | Mandatory | tests/test_tsn_gptp.py::TestGptpTimeError::test_within_limits_passes |
| gPTP | TE-2 | Lock-acquisition within 6 s budget | Mandatory | tests/test_tsn_gptp.py::TestGptpTimeError::test_slow_lock_fails |
| gPTP | PROTO-1 | Pdelay mean-link-delay + turnaround | Mandatory | tests/test_tsn_gptp_proto.py::TestPdelay::test_mean_link_delay_and_turnaround |
| gPTP | PROTO-2 | Sync correction-field identity | Mandatory | tests/test_tsn_gptp_proto.py::TestCorrectionAndRateRatio::test_correction_field_identity |
| gPTP | PROTO-3 | BMCA grandmaster election + failover | Mandatory | tests/test_tsn_gptp_proto.py::TestBmca::test_failover_reelects_after_gm_loss |
| gPTP | PROTO-4 | asCapable gating on absent Follow_Up | Mandatory | tests/test_tsn_gptp_proto.py::TestAsCapable::test_requires_all_three |
| gPTP | HSB-1 | 802.1ASdm hot-standby failover within holdover | Mandatory | tests/test_tsn_hotstandby.py::TestHotStandbyFailover::test_clean_failover_passes |
| Qbv | QBV-1 | Time-aware-shaper gate timing + guard band | Mandatory | tests/test_tsn_qbv.py::TestQbvCheck::test_guard_band_violation |
| FRER | CB-1 | 802.1CB exactly-once delivery under path failure | Mandatory | tests/test_tsn_frer.py::TestCheckFrer::test_survives_mid_stream_path_failure |

## How to extend

Add a pytest in `tests/test_tsn_*.py`, then a row here with the `pytest_node`
pointer. Qav credit-shaper, Qci/PSFP, and end-to-end latency/jitter are the next
rows to back-fill. The gate refuses an empty or unresolvable Mandatory node.
