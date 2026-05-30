"""Sprint 4.4.9: CXL Perform Maintenance (0600h) flow."""
from __future__ import annotations

from computetest.cxl import (
    PERFORM_MAINTENANCE_OPCODE,
    MaintenanceClass,
    MaintenanceDevice,
    MaintenanceHealth,
    check_maintenance,
)


class TestMaintenance:
    def test_opcode_and_class(self):
        assert PERFORM_MAINTENANCE_OPCODE == 0x0600
        assert MaintenanceClass.PPR == 0x01

    def test_well_behaved_flow(self):
        h = check_maintenance(MaintenanceDevice(), op_class=MaintenanceClass.PPR)
        assert isinstance(h, MaintenanceHealth) and h.ok
        assert h.checks == {"query_no_mutation": True,
                            "perform_background_started": True,
                            "second_returns_busy": True}

    def test_query_does_not_perform(self):
        dev = MaintenanceDevice()
        assert dev.perform(MaintenanceClass.PPR, query=True) == "success"
        assert dev.performed == []                        # nothing mutated

    def test_busy_when_running(self):
        dev = MaintenanceDevice()
        assert dev.perform(MaintenanceClass.PPR) == "background"
        assert dev.perform(MaintenanceClass.PPR) == "busy"
        dev.complete()
        assert dev.perform(MaintenanceClass.PPR) == "background"   # free again

    def test_summary_and_to_dict(self):
        h = check_maintenance(MaintenanceDevice(), op_class=MaintenanceClass.PPR)
        assert "CXL maintenance" in h.summary()
        assert h.to_dict()["op_class"] == 1
