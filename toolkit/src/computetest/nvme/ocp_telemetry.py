"""
OCP NVMe Cloud SSD telemetry — ``nvme ocp internal-log`` adapter.

OCP Datacenter NVMe SSD Spec v2.7 defines two log pages the OCP plugin in
``nvme-cli`` decodes into human-readable text/JSON:

* **07h Telemetry Host-Initiated** — FW-developer debug data (sizable binary
  blob carved into Data Areas; vendor-private interpretation, but the OCP
  decoder normalises the framing).
* **C9h OCP Strings Log** — key-value strings (last fatal error, FW build
  signature, sled identifier, ...). The shape an MT or RMA pipeline actually
  wants on every drive.

The command (per ``man nvme-ocp-internal-log``) is:

  nvme ocp internal-log <dev> -o json [--telemetry-log] [--string-log]

This module shells out, parses the JSON, and exposes:

* ``read_telemetry(device)`` → ``OcpTelemetryResult``
* ``read_strings(device)``   → ``OcpStringsResult``
* ``emit_to_ocpdiag(em, ...)`` — surface each field as an ocp-diag measurement

A mock path returns a deterministic synthetic decode so the module runs and
unit-tests off-hardware.

Reference: OCP Datacenter NVMe SSD v2.7 §6 (Cloud Specific OCP Log Pages);
``man nvme-ocp-internal-log``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from ..backend import mock_mode


class OcpTelemetryError(RuntimeError):
    """A failure invoking or parsing ``nvme ocp internal-log``."""


# ----------------------------------------------------------------------------
# Result types
# ----------------------------------------------------------------------------
@dataclass
class OcpTelemetryResult:
    """Parsed 07h Telemetry Host-Initiated log."""
    device: str
    log_id: int                         # 0x07
    version: int | None = None
    data_areas: dict[str, int] = field(default_factory=dict)  # name -> block count
    header: dict[str, Any] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def total_blocks(self) -> int:
        return sum(self.data_areas.values())

    def to_dict(self) -> dict:
        return {"device": self.device, "log_id": self.log_id,
                "version": self.version,
                "data_areas": dict(self.data_areas),
                "header": dict(self.header), "raw": dict(self.raw),
                "total_blocks": self.total_blocks}


@dataclass
class OcpStringsResult:
    """Parsed C9h OCP Strings Log."""
    device: str
    log_id: int                         # 0xC9
    strings: dict[str, str] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def get(self, key: str, default: str = "") -> str:
        return self.strings.get(key, default)

    def to_dict(self) -> dict:
        return {"device": self.device, "log_id": self.log_id,
                "strings": dict(self.strings), "raw": dict(self.raw)}


# ----------------------------------------------------------------------------
# Mock decodes (deterministic, off-hardware)
# ----------------------------------------------------------------------------
_MOCK_TELEMETRY: dict = {
    "logId": 7,
    "header": {
        "version": 1, "host_initiated_data_generation_number": 12,
        "controller_initiated_data_available": False,
    },
    "dataAreas": {
        "1": {"sizeBlocks": 32}, "2": {"sizeBlocks": 16},
        "3": {"sizeBlocks": 0},  "4": {"sizeBlocks": 0},
    },
}

_MOCK_STRINGS: dict = {
    "logId": 201,                                       # 0xC9
    "strings": {
        "model": "MockSSD-1TB",
        "firmware": "MK1.0",
        "boot_count": "42",
        "last_fatal_error": "",
        "build_signature": "0xDEADBEEF",
        "sled_id": "rack-7-bay-3",
    },
}


def _mock_telemetry(device: str) -> OcpTelemetryResult:
    raw = json.loads(json.dumps(_MOCK_TELEMETRY))        # deep copy
    return _parse_telemetry(device, raw)


def _mock_strings(device: str) -> OcpStringsResult:
    raw = json.loads(json.dumps(_MOCK_STRINGS))
    return _parse_strings(device, raw)


# ----------------------------------------------------------------------------
# Parsers (shared by mock and real paths)
# ----------------------------------------------------------------------------
def _parse_telemetry(device: str, raw: dict) -> OcpTelemetryResult:
    """Project nvme-cli's JSON into ``OcpTelemetryResult``."""
    header = raw.get("header", {}) or {}
    log_id = int(raw.get("logId", 7))
    version = header.get("version")
    data_areas: dict[str, int] = {}
    for name, body in (raw.get("dataAreas") or {}).items():
        blocks = body.get("sizeBlocks", 0) if isinstance(body, dict) else 0
        data_areas[str(name)] = int(blocks)
    return OcpTelemetryResult(
        device=device, log_id=log_id,
        version=int(version) if version is not None else None,
        data_areas=data_areas, header=header, raw=raw)


def _parse_strings(device: str, raw: dict) -> OcpStringsResult:
    log_id = int(raw.get("logId", 0xC9))
    strings = raw.get("strings") or {}
    return OcpStringsResult(
        device=device, log_id=log_id,
        strings={str(k): str(v) for k, v in strings.items()},
        raw=raw)


# ----------------------------------------------------------------------------
# Real-host shell-outs
# ----------------------------------------------------------------------------
def _require_nvme_cli() -> str:
    """Locate ``nvme`` on PATH; raise ``OcpTelemetryError`` if missing."""
    path = shutil.which("nvme")
    if path is None:
        raise OcpTelemetryError(
            "nvme-cli not found on PATH (install with `apt install nvme-cli`)")
    return path


def _shell_internal_log(device: str, flag: str) -> dict:  # pragma: no cover - real-hw path
    """Invoke ``nvme ocp internal-log <dev> -o json <flag>`` and JSON-decode."""
    nvme = _require_nvme_cli()
    cmd = [nvme, "ocp", "internal-log", device, "-o", "json", flag]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        raise OcpTelemetryError(f"failed to spawn nvme-cli: {e}") from e
    if proc.returncode != 0:
        raise OcpTelemetryError(
            f"nvme ocp internal-log failed (rc={proc.returncode}): "
            f"{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise OcpTelemetryError(
            f"nvme ocp internal-log produced non-JSON output: {e}") from e


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------
def read_telemetry(device: str = "/dev/nvme0", *,
                    mock: bool | None = None) -> OcpTelemetryResult:
    """Read the 07h Telemetry Host-Initiated log from a drive."""
    use_mock = mock_mode() if mock is None else mock
    if use_mock:
        return _mock_telemetry(device)
    return _parse_telemetry(                              # pragma: no cover - real-hw path
        device, _shell_internal_log(device, "--telemetry-log"))


def read_strings(device: str = "/dev/nvme0", *,
                  mock: bool | None = None) -> OcpStringsResult:
    """Read the C9h OCP Strings Log from a drive."""
    use_mock = mock_mode() if mock is None else mock
    if use_mock:
        return _mock_strings(device)
    return _parse_strings(                                # pragma: no cover - real-hw path
        device, _shell_internal_log(device, "--string-log"))


def emit_to_ocpdiag(em, telemetry: OcpTelemetryResult | None = None,
                     strings: OcpStringsResult | None = None) -> str:
    """Wrap one drive's OCP-decoded logs as an ocp-diag testStep.

    Pass either or both of ``telemetry`` and ``strings`` — each becomes a
    block of measurements under the same step. A final diagnosis carries
    the device path so multi-drive runs surface clearly.
    """
    if telemetry is None and strings is None:
        raise OcpTelemetryError("at least one of telemetry or strings is required")
    device = (telemetry.device if telemetry is not None
              else strings.device)                        # type: ignore[union-attr]
    sid = em.step_start(f"nvme.ocp_internal_log {device}")

    if telemetry is not None:
        em.measurement(name="ocp.telemetry.log_id",
                       value=int(telemetry.log_id), unit="hex")
        if telemetry.version is not None:
            em.measurement(name="ocp.telemetry.version",
                           value=int(telemetry.version))
        em.measurement(name="ocp.telemetry.total_blocks",
                       value=int(telemetry.total_blocks), unit="count")
        for area, blocks in telemetry.data_areas.items():
            em.measurement(name=f"ocp.telemetry.data_area_{area}.blocks",
                           value=int(blocks), unit="count")

    if strings is not None:
        em.measurement(name="ocp.strings.log_id",
                       value=int(strings.log_id), unit="hex")
        # Surface every K/V as a measurement. Values are strings (the OCP
        # measurement schema permits string|bool|number); downstream pipelines
        # join on these by key.
        for key, value in strings.strings.items():
            em.measurement(name=f"ocp.strings.{key}", value=str(value))

    em.diagnosis(verdict=f"nvme.ocp_internal_log.{device}.pass",
                 type_="PASS",
                 message=f"OCP internal-log decoded for {device}")
    em.step_end("pass", step_id=sid)
    return sid
