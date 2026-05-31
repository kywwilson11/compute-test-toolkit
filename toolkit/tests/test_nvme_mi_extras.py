"""
Sprint 3.3: NVMe-MI fillers — NVM Subsystem Health Status Poll, VPD Read,
Management Endpoint Reset. These cover the three remaining UNH-IOL v25
matrix rows that were marked ``_scheduled_`` in Sprint 2.4.
"""
from __future__ import annotations

import pytest

from computetest.nvme.mi import (
    NvmSubsystemHealth,
    ResetFunction,
    ResetRequest,
    ResetResult,
    VpdBoundsError,
    VpdReadRequest,
    VpdReadResult,
    poll_nvm_subsystem_health,
    reset_management_endpoint,
    vpd_read,
)
from computetest.nvme.mi.subsystem_health import (
    NSS_DRIVE_FUNCTIONING,
    NSS_RESET_NOT_REQUIRED,
    SMART_WARNING_AVAILABLE_SPARE_BELOW_THRESHOLD,
    SMART_WARNING_READ_ONLY,
    SMART_WARNING_TEMPERATURE_THRESHOLD,
)


# ----------------------------------------------------------------------------
# NVM Subsystem Health Status Poll — UNH-IOL v25 test 7.5
# ----------------------------------------------------------------------------
class TestNvmSubsystemHealth:
    """Covers UNH-IOL v25 test 7.5 — NVM Subsystem Health Status Poll."""

    def test_mock_poll_returns_healthy_subsystem(self):
        """Mock poll surfaces a healthy NVM subsystem."""
        s = poll_nvm_subsystem_health(mock=True)
        assert isinstance(s, NvmSubsystemHealth)
        assert s.healthy
        assert s.drive_functioning
        assert s.smart_warnings == 0
        assert s.temperature_c == 41.0
        assert s.warnings() == []

    def test_drive_not_functioning_breaks_healthy(self):
        s = NvmSubsystemHealth(
            nss=0,                                            # NSS_DRIVE_FUNCTIONING clear
            smart_warnings=0,
            composite_temperature_k=314,
            percentage_drive_life_used=1,
            available_spare=100,
            spare_below_threshold=False,
            capacity_below_threshold=False,
        )
        assert not s.healthy
        assert not s.drive_functioning

    def test_smart_warning_decode(self):
        s = NvmSubsystemHealth(
            nss=NSS_DRIVE_FUNCTIONING | NSS_RESET_NOT_REQUIRED,
            smart_warnings=(SMART_WARNING_AVAILABLE_SPARE_BELOW_THRESHOLD
                            | SMART_WARNING_TEMPERATURE_THRESHOLD
                            | SMART_WARNING_READ_ONLY),
            composite_temperature_k=350,
            percentage_drive_life_used=80,
            available_spare=5,
            spare_below_threshold=True,
            capacity_below_threshold=False,
        )
        names = set(s.warnings())
        assert names == {
            "available_spare_below_threshold",
            "temperature_threshold", "read_only",
        }
        assert not s.healthy                                # warnings present

    def test_spare_below_threshold_breaks_healthy(self):
        s = NvmSubsystemHealth(
            nss=NSS_DRIVE_FUNCTIONING,
            smart_warnings=0,
            composite_temperature_k=314,
            percentage_drive_life_used=1,
            available_spare=2,
            spare_below_threshold=True,
            capacity_below_threshold=False,
        )
        assert not s.healthy

    def test_percentage_drive_life_used_above_2_breaks_healthy(self):
        s = NvmSubsystemHealth(
            nss=NSS_DRIVE_FUNCTIONING,
            smart_warnings=0,
            composite_temperature_k=314,
            percentage_drive_life_used=50,
            available_spare=100,
            spare_below_threshold=False,
            capacity_below_threshold=False,
        )
        assert not s.healthy

    def test_to_dict_round_trip(self):
        s = poll_nvm_subsystem_health(mock=True)
        d = s.to_dict()
        assert d["healthy"] is True
        assert d["temperature_c"] == 41.0
        assert d["drive_functioning"] is True
        assert d["smart_warning_names"] == []


# ----------------------------------------------------------------------------
# VPD Read — UNH-IOL v25 test 10.1 (FYI)
# ----------------------------------------------------------------------------
class TestVpdRead:
    """Covers UNH-IOL v25 test 10.1 — VPD Read bounds check."""

    def test_request_in_bounds_succeeds(self):
        req = VpdReadRequest(offset=0, length=16, vpd_capacity_bytes=256)
        assert req.length == 16

    def test_request_negative_offset_rejected(self):
        with pytest.raises(VpdBoundsError, match=">= 0"):
            VpdReadRequest(offset=-1, length=16)

    def test_request_zero_length_rejected(self):
        with pytest.raises(VpdBoundsError, match="> 0"):
            VpdReadRequest(offset=0, length=0)

    def test_request_negative_length_rejected(self):
        with pytest.raises(VpdBoundsError, match="> 0"):
            VpdReadRequest(offset=0, length=-1)

    def test_request_overflowing_capacity_rejected(self):
        """The spec's per-request bound: offset+length > capacity is illegal."""
        with pytest.raises(VpdBoundsError, match="overflows"):
            VpdReadRequest(offset=240, length=32, vpd_capacity_bytes=256)

    def test_request_exact_boundary_allowed(self):
        # offset + length == capacity is legal (reads the last byte).
        req = VpdReadRequest(offset=240, length=16, vpd_capacity_bytes=256)
        assert req.offset + req.length == req.vpd_capacity_bytes

    def test_mock_read_returns_requested_length(self):
        req = VpdReadRequest(offset=0, length=32)
        result = vpd_read(req, mock=True)
        assert isinstance(result, VpdReadResult)
        assert len(result.data) == 32

    def test_mock_read_returns_fru_header_bytes(self):
        req = VpdReadRequest(offset=0, length=8)
        result = vpd_read(req, mock=True)
        assert result.data[0] == 0x01                        # IPMI FRU header byte 0

    def test_explicit_real_with_no_transport_raises_not_fakes(self):
        # mock=False + no transport must raise, not silently return the canned blob
        # (the toolkit never fakes a PASS on missing hardware).
        req = VpdReadRequest(offset=0, length=8)
        with pytest.raises(ValueError, match="fabricated mock FRU data"):
            vpd_read(req, transport=None, mock=False)

    def test_mock_read_zero_pads_past_eeprom_end(self):
        # Request a slice that runs past the mock EEPROM; the mock pads with
        # zeros so the response length matches the request length.
        req = VpdReadRequest(offset=10_000, length=64)
        result = vpd_read(req, mock=True)
        assert len(result.data) == 64
        assert result.data == b"\x00" * 64


# ----------------------------------------------------------------------------
# Management Endpoint Reset — UNH-IOL v25 test 11.1
# ----------------------------------------------------------------------------
class TestManagementEndpointReset:
    """Covers UNH-IOL v25 test 11.1 — Management Endpoint Reset."""

    def test_mock_reset_default_is_nvm_subsystem_reset(self):
        result = reset_management_endpoint(mock=True)
        assert isinstance(result, ResetResult)
        assert result.accepted
        assert result.function == ResetFunction.NVM_SUBSYSTEM_RESET

    def test_mock_reset_inhibit_function(self):
        result = reset_management_endpoint(
            function=ResetFunction.SUBSYSTEM_RESET_INHIBIT, mock=True)
        assert result.function == ResetFunction.SUBSYSTEM_RESET_INHIBIT
        assert result.accepted

    def test_reset_function_enum_values(self):
        # The spec's RSF field codes.
        assert int(ResetFunction.NVM_SUBSYSTEM_RESET) == 0
        assert int(ResetFunction.SUBSYSTEM_RESET_INHIBIT) == 1

    def test_request_dataclass_default(self):
        req = ResetRequest()
        assert req.function == ResetFunction.NVM_SUBSYSTEM_RESET


# ----------------------------------------------------------------------------
# Module surface
# ----------------------------------------------------------------------------
def test_public_exports():
    import computetest.nvme.mi as mi
    expected = {"NvmSubsystemHealth", "poll_nvm_subsystem_health",
                "VpdReadRequest", "VpdReadResult", "VpdBoundsError",
                "vpd_read",
                "ResetFunction", "ResetRequest", "ResetResult",
                "reset_management_endpoint"}
    for sym in expected:
        assert hasattr(mi, sym), f"missing public export: {sym}"
