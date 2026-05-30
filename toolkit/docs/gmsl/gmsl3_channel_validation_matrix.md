# GMSL3 channel validation — coverage matrix

**Source:** Analog Devices GMSL3 Channel Specification (AN-2585) + GMSL3
Hardware Design and Validation Guide (UG-2208).

**Mandatory = ADI-spec-mandatory (AN-2585 / UG-2208), NOT plugfest-mandatory.**
GMSL is a single-vendor ecosystem: there is no third-party GMSL conformance
authority (no UNH-IOL / Avnu / PCI-SIG analog), so "conformance" here means
self-certification against ADI's own channel spec and validation guide.

**Purpose:** a living traceability table from each *Mandatory* GMSL3 validation
item to the pytest node in `tests/test_gmsl_*.py` that exercises it. CI gate
(`tests/test_gmsl3_coverage.py`): every `Mandatory` row must have a `pytest_node`
that resolves to a real test. `FYI` rows are aspirational and don't gate.

## Spine — GMSL3 validation items

| Group   | Test ID | Title                                                      | Type      | pytest_node                                                                            |
|---------|---------|------------------------------------------------------------|-----------|----------------------------------------------------------------------------------------|
| Link    | 1.1     | Lock from cold + after forced re-train, all enabled links  | Mandatory | tests/test_gmsl_checks.py::TestCheckSerdesLink::test_healthy_top_mode_passes            |
| Link    | 1.2     | Negotiated MODE assertion (PAM4-12G vs NRZ-6G vs fallback)  | Mandatory | tests/test_gmsl_checks.py::TestCheckSerdesLink::test_degraded_mode_fails_mode_only      |
| BER     | 2.1     | Forward PRBS pre-FEC BER + confidence bound                | Mandatory | tests/test_gmsl_prbs_ber.py::TestPrbsBer::test_clean_forward_link_proves_ber            |
| BER     | 2.2     | Reverse PRBS BER                                            | Mandatory | tests/test_gmsl_prbs_ber.py::TestPrbsBer::test_clean_reverse_link_proves_ber            |
| FEC     | 3.1     | RS corrected-symbol rate + uncorrectable-block = 0         | Mandatory | tests/test_gmsl_fec.py::TestFecCheck::test_uncorrectable_block_fails                    |
| Eye     | 4.1     | EOM vertical+horizontal margin per link (PAM4 three-eye)   | Mandatory | tests/test_gmsl_eom.py::TestEomCheck::test_pam4_eye_maps_to_eyemeasurement_worst_subeye |
| Channel | 5.1     | Insertion-loss mask (filtered Sdd21/Sdd12)                 | Mandatory | tests/test_gmsl_channel.py::TestComparison::test_insertion_loss_violation_below_limit   |
| Channel | 5.2     | Return-loss mask + 2-10 MHz PoC carve-out                  | Mandatory | tests/test_gmsl_channel.py::TestComparison::test_return_loss_violation_above_limit      |
| Video   | 6.1     | CSI-2 video-CRC counter clean + VC/data-type map           | Mandatory | tests/test_gmsl_video_integrity.py::TestVideoIntegrity::test_clean_video_passes         |
| Control | 7.1     | Control-packet CRC + sequence # + ARQ retransmit           | Mandatory | tests/test_gmsl_video_integrity.py::TestControlChannel::test_clean_control_passes       |
| Safety  | 8.1     | ERRB asserts on fault; counter latches; clear de-asserts   | Mandatory | tests/test_gmsl_safety.py::TestSafetyMechanism::test_full_assert_latch_clear_cycle      |
| Margin  | 9.1     | V/T shmoo across AEC-Q100 Grade-2 -40..+105 C              | Mandatory | tests/test_gmsl_shmoo.py::TestVtShmoo::test_healthy_link_passes_all_corners             |
| SSC     | 10.1    | Lock + BER + eye with spread-spectrum clocking enabled     | FYI       | _scheduled_ (SSC/EMI interop follow-on)                                                 |

## Channel masks — blocked on AN-2585 transcription

Rows 5.1 / 5.2 gate the IL/RL **mask-comparison framework**
(`gmsl.channel.check_against_mask`), not the numeric AN-2585 limit lines, which
are paywalled/PDF and shipped as `None` placeholders
(`gmsl.channel.AN2585_*_MASK`). `gmsl.channel.check_channel_compliance` refuses
to pass with no mask supplied, so the gate can never be satisfied by an absent
limit. Transcribe the spec masks, populate the placeholders, then add a bench
row tying the real masks to a hardware run.

## How to extend

When ADI revises AN-2585 / UG-2208, or a new safety item is added: add a pytest
in `tests/test_gmsl_*.py` exercising the branch, then add a row here with the
`pytest_node` pointer. The CI gate refuses an empty or unresolvable Mandatory
`pytest_node`; the scheduled-debt budget is 0.
