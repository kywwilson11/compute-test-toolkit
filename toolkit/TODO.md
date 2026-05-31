# TODO — follow-ups from the 2026-05-30 code audit

The 26-step audit-fix campaign is complete (see `docs/audits/2026-05-30-full-code-audit.md`
and commits `55380a4..2165e64`). The items below are the parts that were **deliberately not
guessed**: the verifiable logic is already fixed and committed, but fully closing each one
needs a real capture or a ratified spec number. Each notes what to provide and where it lands.

## Needs a hardware/tool capture (the Jetson can produce the first two)

- [ ] **NVMe self-test-log key casing** (audit step 13, `src/computetest/nvme/health.py`).
      The fail-safe logic is in (a failed DST no longer reads as PASS), but the exact
      `nvme-cli` JSON key strings vary by version. Capture on a box:
      `sudo nvme self-test-log /dev/nvme0 -o json` — one **passing** and one **failing**
      entry — commit under `corpus/nvme/<ver>/...` and add a replay test pinning the keys.

- [ ] **Ethernet TDR multiline layout** (audit step 14, `src/computetest/ethernet.py`).
      The fractional-metre regex, returncode checks, and negotiated-role fix are in; the
      exact `ethtool --cable-test-tdr` multiline attribute layout (pair code + fault length
      on separate lines) is reconstructed, not captured. Capture from a fault-injected
      automotive PHY (open/short on 1000BASE-T1): `sudo ethtool --cable-test-tdr <iface>`
      → commit under `corpus/ethtool/<ver>/<model>/` and add a replay test.

- [ ] **OCP telemetry byte payload** (audit step 26, `src/computetest/nvme/ocp_telemetry.py`).
      Decoder is now tested against hand-derived bytes, but a real-device OCP internal-log
      capture (or a spec-worked payload) would replace the remaining synthetic fixture.

## Needs a ratified spec document

- [ ] **NVMe-MI 2.1 NSS bit indices** (audit step 9, `src/computetest/nvme/mi/subsystem_health.py`).
      Composite Temperature (signed Celsius) and Reset Type (only 00h) are fixed. The exact
      NVM Subsystem Status (NSS) bit map is flagged with a TODO in-code — transcribe from the
      ratified NVMe-MI 2.1 spec (a web check confirmed "Drive Functional" is NOT bit 0).

- [ ] **QEMU query-qmp-schema pin** (audit step 8, `sim/qemu/cxl_inject.py`).
      The `cxl-inject-general-media-event` required members are correct per `qapi/cxl.json`
      @v11.0.0; pin a live `query-qmp-schema` snapshot to the exact target `qemu-system` build
      (commit a snapshot or add a version-equality assert) to discharge the fidelity note.

- [ ] **Spec-mask numbers (BLOCKED)** — AN-2585 GMSL3 insertion/return-loss masks and
      OPEN Alliance TC8 PMA S-parameter masks. `gmsl/channel.py` and `tsn/pma.py` keep these
      as `None` and the compliance check RAISES rather than fake a pass; transcribe the mask
      values when the documents are available.

## Notes
- 7 audit findings were verified to be **false positives** and intentionally not applied
  (DDR5 density, CXL `mem_capable`, gPTP formula, CXL flit-speed, ocp-telemetry emit-FAIL,
  cxl & nvme/mi `__all__`). Do not re-apply them from the audit report without re-verifying.
