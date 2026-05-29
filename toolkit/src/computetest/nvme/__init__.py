"""NVMe-domain subpackage.

* ``health``        — the legacy ``check_nvme`` / SMART / DST surface
  (re-exported here so ``from computetest.nvme import check_nvme`` continues
  to work after the package split).
* ``ocp_telemetry`` — OCP Datacenter NVMe SSD Spec v2.7 telemetry adapter:
  ``nvme ocp internal-log`` (07h Telemetry Host-Initiated + C9h Strings Log)
  → ingest into the ocp-diag schema.
* ``mi``            — NVMe-MI v2.1 out-of-band surface (Sprint 2.4 stub).
"""
# Re-export the stdlib modules the legacy nvme.py exposed at module scope so
# the existing corpus tests can ``monkeypatch.setattr(nvme.subprocess, ...)``
# after the package split.
from .health import (  # noqa: F401
    _SMART_KEY_ALIASES,
    NvmeHealth,
    _apply_limits,
    _history,
    _mock_smart,
    _normalize_smart_keys,
    check_nvme,
    poll_self_test,
    self_test_log,
    shutil,
    start_self_test,
    subprocess,
)

__all__ = [
    "NvmeHealth", "check_nvme", "start_self_test", "self_test_log",
    "poll_self_test",
    # Private helpers re-exported for the existing test suite.
    "_apply_limits", "_normalize_smart_keys", "_mock_smart",
    "_SMART_KEY_ALIASES", "_history",
]
