"""
Topology / expectation config: declare what a good board *should* look like (which
devices, at which speed/width) and compare reality against it. Data-driven so a new
board revision is a new config file, not new code (Guide A, §"Test station").

This is Layer 1 of the plan — the PCIe LINK layer. `pcie_devices` declares the
endpoints whose *links* must be tested (enumeration, link-health, AER, BERT, lane
margining). Layer 2 — the functional/DEVICE health checks under `functional_checks`
(NVMe SMART, GPU ECC/thermal, etc.) — is consumed separately by the harness; see
harness.py and USAGE.md "## Config reference".

A config is a dict (or JSON/YAML file):

    target_ber: 1.0e-12
    confidence: 0.95
    pcie_devices:
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

from .backend import DOWNSTREAM_PORTS, Backend


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
    for d in data.get("pcie_devices", []):
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


# --- PCIe chain analysis (every link in an endpoint's path) -------------------- #
@dataclass
class ChainMember:
    """One BDF in the path. Its AER reports ONE direction of ONE link (its receiver
    side), so bit errors are evaluated per-BDF — 4 BDFs => 4 independent error counts."""
    bdf: str
    port_type: int
    peer: str | None        # the device at the other end of this BDF's receiver-side link
    direction: str          # e.g. "0000:03:00.0->0000:04:00.0" (peer -> this BDF)


@dataclass
class ChainLink:
    """One external link. Speed/width (and LBMS/LABS) are a per-LINK property — both ends
    report the same negotiated state — so a downgrade is evaluated ONCE here, at the
    downstream port that owns the link, never double-counted across its two BDFs."""
    downstream_bdf: str     # root port / switch-downstream port (owns the link + LBMS/LABS)
    upstream_bdf: str       # the device below it (switch-upstream port / endpoint)

    @property
    def name(self) -> str:
        return f"{self.downstream_bdf}<->{self.upstream_bdf}"


def analyze_chain(backend: Backend, endpoint_bdf: str) -> tuple[list[ChainMember], list[ChainLink]]:
    """Decompose an endpoint's path into per-BDF error directions and per-link downgrade
    targets. The switch-internal upstream<->downstream connection is excluded: only a
    downstream port (root / switch-downstream) owns an external link."""
    chain = backend.link_chain(endpoint_bdf)
    types = {b: backend.read_port_type(b) for b in chain}

    members: list[ChainMember] = []
    for i, b in enumerate(chain):
        # A downstream port's receiver faces its child; everyone else faces its parent.
        if types[b] in DOWNSTREAM_PORTS:
            peer = chain[i + 1] if i + 1 < len(chain) else None
        else:
            peer = chain[i - 1] if i > 0 else None
        direction = f"{peer}->{b}" if peer else f"{b} (no upstream link)"
        members.append(ChainMember(b, types[b], peer, direction))

    links: list[ChainLink] = []
    for i in range(1, len(chain)):
        parent, child = chain[i - 1], chain[i]
        if types[parent] in DOWNSTREAM_PORTS:        # parent owns an external link to child
            links.append(ChainLink(parent, child))   # (else parent is a switch-up port: internal)
    return members, links
