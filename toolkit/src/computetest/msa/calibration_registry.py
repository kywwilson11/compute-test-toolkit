"""
Calibration registry — minimal traceability for the instruments a test station
depends on.

A registry record names the instrument by role (``rx_eye_scope`` not
``MSO64-1234``), records the most recent calibration date, the next-due date,
and the certificate identifier. Loaded from a YAML file
(``calibration_registry.yaml``) co-located with the station configuration; CI
gates a run by checking every required role has an unexpired entry.

The intentionally tiny surface area:

* ``CalibrationEntry`` — one row
* ``CalibrationRegistry`` — list of rows plus due-date queries
* ``load_registry(path)`` — YAML loader (falls back gracefully if PyYAML isn't
  installed)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class CalibrationEntry:
    """One calibrated instrument.

    Dates are ISO ``YYYY-MM-DD`` strings on disk and ``datetime.date`` here.
    ``role`` is the abstract instrument role (e.g. ``rx_eye_scope``,
    ``power_smu``) the station configuration references — keep it stable
    across hardware swaps.
    """
    role: str
    manufacturer: str
    model: str
    serial: str
    last_calibrated: date
    next_due: date
    certificate: str = ""             # cal-lab certificate / report ID
    notes: str = ""

    def is_expired(self, today: date) -> bool:
        return today >= self.next_due

    def days_until_due(self, today: date) -> int:
        return (self.next_due - today).days

    def to_dict(self) -> dict:
        return {"role": self.role, "manufacturer": self.manufacturer,
                "model": self.model, "serial": self.serial,
                "last_calibrated": self.last_calibrated.isoformat(),
                "next_due": self.next_due.isoformat(),
                "certificate": self.certificate, "notes": self.notes}


@dataclass
class CalibrationRegistry:
    """A station's full calibration roster."""
    entries: list[CalibrationEntry] = field(default_factory=list)

    def by_role(self, role: str) -> CalibrationEntry | None:
        for e in self.entries:
            if e.role == role:
                return e
        return None

    def expired(self, today: date) -> list[CalibrationEntry]:
        return [e for e in self.entries if e.is_expired(today)]

    def due_within(self, today: date, days: int) -> list[CalibrationEntry]:
        """Entries that come due in the next ``days`` days (inclusive of today)."""
        return [e for e in self.entries
                if 0 <= e.days_until_due(today) <= days]

    def require(self, today: date, roles: list[str]) -> list[str]:
        """Return a list of failure reasons (missing roles or expired entries).

        Empty list ⇒ the registry covers every required role with an unexpired
        certificate. A CI gate should treat a non-empty return as a station
        not-ready-to-test signal.
        """
        reasons: list[str] = []
        for r in roles:
            e = self.by_role(r)
            if e is None:
                reasons.append(f"missing role: {r}")
            elif e.is_expired(today):
                reasons.append(
                    f"calibration expired for role {r!r} (due {e.next_due.isoformat()})")
        return reasons

    def to_dict(self) -> dict:
        return {"entries": [e.to_dict() for e in self.entries]}


def load_registry(path: str) -> CalibrationRegistry:
    """Load a registry from a YAML file (optionally JSON if PyYAML isn't
    installed).

    Expected schema::

        entries:
          - role: rx_eye_scope
            manufacturer: Tektronix
            model: MSO64
            serial: C012345
            last_calibrated: 2026-01-15
            next_due: 2027-01-15
            certificate: NIST-12-345
            notes: shipped to A2LA cal lab on 2026-01-08
    """
    raw_text = open(path).read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
            data = yaml.safe_load(raw_text)
        except ImportError:
            # JSON is a strict subset of YAML 1.2; try it as a fallback so the
            # registry works without PyYAML installed in CI.
            import json
            data = json.loads(raw_text)
    else:
        import json
        data = json.loads(raw_text)
    return CalibrationRegistry(entries=[
        CalibrationEntry(
            role=row["role"],
            manufacturer=row["manufacturer"],
            model=row["model"],
            serial=row["serial"],
            last_calibrated=_to_date(row["last_calibrated"]),
            next_due=_to_date(row["next_due"]),
            certificate=row.get("certificate", ""),
            notes=row.get("notes", ""),
        )
        for row in data.get("entries", [])
    ])


def _to_date(v) -> date:
    """Accept either a ``date`` (YAML 1.2 native) or an ISO ``YYYY-MM-DD`` string."""
    if isinstance(v, date):
        return v
    return date.fromisoformat(v)
