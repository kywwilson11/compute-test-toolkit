"""
Topology / expectation config: declare what a good board *should* look like (which
devices, at which speed/width) and compare reality against it. Data-driven so a new
board revision is a new config file, not new code (Guide A, §"Test station").

A config is a dict (or JSON/YAML file):

    target_ber: 1.0e-12
    confidence: 0.95
    devices:
      - name: GPU0
        match: {vendor_id: 0x10DE, class_code: 0x030000}
        expected_speed: 4        # Gen4
        expected_width: 16
        min_margin_ui: 0.25
      - name: NVMe
        match: {class_code: 0x010802}
        count: 2
        expected_speed: 4
        expected_width: 4
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .backend import Backend


@dataclass
class DeviceExpectation:
    name: str
    match: dict          # any of: bdf, vendor_id, device_id, class_code
    count: int = 1
    expected_speed: int | None = None
    expected_width: int | None = None
    min_margin_ui: float = 0.25

    def matches(self, dev) -> bool:
        m = self.match
        if "bdf" in m and dev.bdf != m["bdf"]:
            return False
        if "vendor_id" in m and dev.vendor_id != int(m["vendor_id"]):
            return False
        if "device_id" in m and dev.device_id != int(m["device_id"]):
            return False
        if "class_code" in m and dev.class_code != int(m["class_code"]):
            return False
        return True


@dataclass
class TopologyConfig:
    devices: list[DeviceExpectation] = field(default_factory=list)
    target_ber: float = 1e-12
    confidence: float = 0.95


def load_config(source) -> TopologyConfig:
    """Load a TopologyConfig from a dict, a JSON/YAML path, or a JSON string."""
    if isinstance(source, TopologyConfig):
        return source
    data: dict
    if isinstance(source, dict):
        data = source
    elif isinstance(source, str) and source.strip().startswith("{"):
        data = json.loads(source)
    else:  # a path
        with open(source) as fh:
            text = fh.read()
        if str(source).endswith((".yaml", ".yml")):
            try:
                import yaml  # optional dependency
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("PyYAML not installed; use a .json config or "
                                   "`pip install pyyaml`") from e
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)

    def to_int(v):
        return int(v, 0) if isinstance(v, str) else v

    devs = []
    for d in data.get("devices", []):
        match = {k: to_int(v) for k, v in d.get("match", {}).items()}
        devs.append(DeviceExpectation(
            name=d["name"], match=match, count=d.get("count", 1),
            expected_speed=d.get("expected_speed"), expected_width=d.get("expected_width"),
            min_margin_ui=d.get("min_margin_ui", 0.25)))
    return TopologyConfig(devs, float(data.get("target_ber", 1e-12)),
                          float(data.get("confidence", 0.95)))


@dataclass
class EnumerationReport:
    matched: dict[str, list[str]]   # expectation name -> [bdf, ...]
    missing: list[str]              # expectation names with too few devices
    unexpected: list[str]           # BDFs not matched by any expectation

    @property
    def ok(self) -> bool:
        return not self.missing  # unexpected devices are a warning, not a fail

    def summary(self) -> str:
        lines = [f"  {name}: {', '.join(bdfs) or '(none)'}"
                 for name, bdfs in self.matched.items()]
        if self.missing:
            lines.append("  MISSING: " + ", ".join(self.missing))
        if self.unexpected:
            lines.append("  unexpected: " + ", ".join(self.unexpected))
        return "Enumeration:\n" + "\n".join(lines)


def enumerate_against(backend: Backend, config: TopologyConfig) -> EnumerationReport:
    """Match actual devices to expectations; report missing and unexpected."""
    devs = {bdf: backend.get_device(bdf) for bdf in backend.list_devices()}
    matched: dict[str, list[str]] = {}
    claimed: set[str] = set()
    missing: list[str] = []
    for exp in config.devices:
        hits = [bdf for bdf, d in devs.items() if exp.matches(d) and bdf not in claimed]
        matched[exp.name] = hits[: exp.count] if exp.count else hits
        claimed.update(matched[exp.name])
        if len(matched[exp.name]) < exp.count:
            missing.append(f"{exp.name} (found {len(matched[exp.name])}/{exp.count})")
    unexpected = [bdf for bdf in devs if bdf not in claimed]
    return EnumerationReport(matched, missing, unexpected)
