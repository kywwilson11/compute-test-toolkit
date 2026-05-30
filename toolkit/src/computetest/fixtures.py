"""
Fixture / instrument role mapping.

A test sequence on station S references instruments by **role**
(``rx_eye_scope``, ``power_smu``, ``external_bert``), not by physical
resource string (``TCPIP::192.168.0.5::INSTR``). That indirection is what
lets the SAME sequence run unchanged when the lab swaps a Keysight scope for
a Tektronix one, or when a station moves between bay 3 and bay 7.

Sprint 2.1 deliverable per the research roadmap: "test sequences reference
instruments by role." This file is the role registry — load
``fixture_map.yaml`` at station start, then ``fixtures.get('rx_eye_scope')``
returns the right ``Scope`` already opened against the right resource.

Schema (YAML / JSON; PyYAML is optional, JSON works without it)::

    station: bay-3
    instruments:
      - role: rx_eye_scope
        kind: Scope
        resource: TCPIP::192.168.0.5::INSTR
        idn: 'Tektronix,MSO64,C012345,2.8.5'        # asserted on open()
        timeout_ms: 5000
        options: {read_termination: "\\n"}
      - role: power_smu
        kind: SMU
        resource: GPIB0::24::INSTR
        idn: 'Keysight Technologies,B2902B,MY54321001,...'

The optional ``idn`` is asserted on open: if the connected instrument's
``*IDN?`` doesn't match the prefix you stored, the registry refuses to
return it (catches the operator wiring the wrong cable to the wrong box,
a real and persistent class of station bug).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import instruments
from .instruments import (
    DMM,
    SMU,
    ElectronicLoad,
    ExternalBERT,
    InstrumentError,
    PowerSupply,
    Scope,
    SCPIInstrument,
    SwitchMatrix,
    ThermalChamber,
    TimeIntervalAnalyzer,
    TSNTrafficGenerator,
    Vna,
)

# Symbolic kinds the YAML accepts -> concrete instrument class.
_KIND_REGISTRY: dict[str, type[SCPIInstrument]] = {
    "Scope": Scope,
    "PowerSupply": PowerSupply,
    "DMM": DMM,
    "ElectronicLoad": ElectronicLoad,
    "SMU": SMU,
    "ExternalBERT": ExternalBERT,
    "SwitchMatrix": SwitchMatrix,
    "Vna": Vna,
    "ThermalChamber": ThermalChamber,
    "TimeIntervalAnalyzer": TimeIntervalAnalyzer,
    "TSNTrafficGenerator": TSNTrafficGenerator,
}


def register_instrument_kind(name: str, cls: type[SCPIInstrument]) -> None:
    """Add a vendor/site-specific SCPIInstrument subclass to the registry so
    a YAML map can reference it by ``kind:``."""
    _KIND_REGISTRY[name] = cls


@dataclass
class InstrumentSpec:
    """One row in ``fixture_map.yaml``: how to find the instrument that plays
    role ``role`` on this station."""
    role: str
    kind: str                                                # key in _KIND_REGISTRY
    resource: str = "MOCK::INSTR"                            # VISA resource string
    idn: str = ""                                            # optional *IDN? assertion (prefix)
    timeout_ms: int = 5000
    options: dict[str, Any] = field(default_factory=dict)    # passthrough to the constructor

    def to_dict(self) -> dict:
        return {"role": self.role, "kind": self.kind, "resource": self.resource,
                "idn": self.idn, "timeout_ms": self.timeout_ms,
                "options": dict(self.options)}


@dataclass
class FixtureMap:
    """A loaded ``fixture_map.yaml`` for one station."""
    station: str = "station-1"
    instruments: list[InstrumentSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for inst in self.instruments:
            if inst.role in seen:
                raise InstrumentError(
                    f"duplicate role {inst.role!r} in fixture map for {self.station}")
            seen.add(inst.role)
            if inst.kind not in _KIND_REGISTRY:
                raise InstrumentError(
                    f"unknown instrument kind {inst.kind!r} (role {inst.role!r}); "
                    f"known: {sorted(_KIND_REGISTRY)}")

    @property
    def roles(self) -> list[str]:
        return [inst.role for inst in self.instruments]

    def spec(self, role: str) -> InstrumentSpec:
        for inst in self.instruments:
            if inst.role == role:
                return inst
        raise InstrumentError(f"no instrument with role {role!r} in fixture map")

    def open(self, role: str, *, mock: bool | None = None) -> SCPIInstrument:
        """Construct, open, optionally IDN-check the instrument for ``role``.

        ``mock``: forward to the SCPIInstrument constructor — None defers to
        the toolkit's mock-mode detection (no pyvisa / mock backend).
        """
        spec = self.spec(role)
        cls = _KIND_REGISTRY[spec.kind]
        inst = cls(spec.resource, mock=mock, timeout_ms=spec.timeout_ms,
                   **spec.options)
        inst.open()
        if spec.idn:
            actual = inst.idn()
            if not actual.startswith(spec.idn.split(",")[0]):
                # Compare the *manufacturer* (first comma-separated field) — the
                # serial and firmware drift naturally and aren't an error signal.
                # If you need a tighter check (the operator wired a Keysight
                # E36312 when E36313 was expected), assert on more fields here.
                inst.close()
                raise InstrumentError(
                    f"role {role!r}: connected instrument IDN {actual!r} does "
                    f"not match expected prefix {spec.idn.split(',')[0]!r}")
        return inst

    def open_all(self, *, mock: bool | None = None) -> dict[str, SCPIInstrument]:
        """Open every instrument in the map. Returns a dict ``role -> instrument``."""
        return {spec.role: self.open(spec.role, mock=mock)
                for spec in self.instruments}

    def to_dict(self) -> dict:
        return {"station": self.station,
                "instruments": [i.to_dict() for i in self.instruments]}


def load_fixture_map(path: str) -> FixtureMap:
    """Load a ``fixture_map.yaml`` (or ``.json``).

    Schema is documented at the top of this module. PyYAML is optional — JSON
    works without it (and YAML 1.2 is a strict JSON superset, so plain-JSON
    YAML files load via the JSON path).
    """
    text = open(path).read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
            data = yaml.safe_load(text)
        except ImportError:
            data = json.loads(text)
    else:
        data = json.loads(text)
    return FixtureMap(
        station=data.get("station", "station-1"),
        instruments=[
            InstrumentSpec(
                role=row["role"], kind=row["kind"],
                resource=row.get("resource", "MOCK::INSTR"),
                idn=row.get("idn", ""),
                timeout_ms=int(row.get("timeout_ms", 5000)),
                options=dict(row.get("options", {})),
            ) for row in data.get("instruments", [])
        ],
    )


__all__ = [
    "FixtureMap", "InstrumentSpec", "load_fixture_map",
    "register_instrument_kind",
    # Re-export the instruments themselves for the test-sequence-author surface.
    "DMM", "ElectronicLoad", "ExternalBERT", "PowerSupply", "SCPIInstrument",
    "SMU", "Scope", "SwitchMatrix", "instruments",
]
