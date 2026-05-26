# Zoox Compute Test Engineer — Prep + Toolkit

Everything for starting as a Test Engineer in the Zoox Compute program (manufacturing
test & diagnostics): two deep prep guides and a working, tested test toolkit.

## Contents

```
.
├── Zoox_Study_Guide-11.pdf       # the original 296-page interview-prep guide
├── guides/                       # NEW — forward-looking, doing-the-job guides
│   ├── 01-job-prep-deep-dive.md  # + pdf/  (28 pp): deep technical, per JD interface
│   ├── 02-success-guide.md       # + pdf/  (15 pp): ramp, CMs, debugging, CI, the LPs
│   ├── build.sh / header.tex      # rebuild PDFs: `bash guides/build.sh`
│   └── pdf/                        # generated PDFs
└── toolkit/                      # NEW — the compute-platform test toolkit
    ├── README.md                  # full toolkit docs
    ├── c/pcie_bert.c              # C BERT engine (AER arm/clear, count, JSON)
    ├── src/computetest/           # PCIe BERT + diagnostics + NVMe/GPU/GMSL/Eth/CAN + harness
    ├── dashboard/app.py           # FastAPI results + heartbeat dashboard
    ├── configs/                   # example test plans (JSON + YAML)
    ├── tests/                     # 281 pytest tests (all run on the mock backend)
    └── demo_bert.py
```

## Start here

1. **Read the guides** (`guides/pdf/`). Guide 01 is the technical deep-dive on every JD
   interface; Guide 02 is how to ramp and succeed in the role.
2. **Run the toolkit** (no hardware needed — it uses a mock backend):
   ```bash
   cd toolkit
   make demo        # PCIe BERT across a simulated board
   make plan        # full multi-interface test plan
   make test        # 281 tests
   ```
   See **`toolkit/USAGE.md`** for the step-by-step usage guide (per-command reference,
   exit codes, `--json` shapes, writing test plans, running on a real Linux station,
   safety, and troubleshooting).

## The toolkit in one line

A modern rebuild of the X-ES PCIe BERT (C engine + Python confidence math, to a 1e-12 /
95% target) wrapped in a full diagnostic suite — AER decode, link-degradation and
retrain detection, lane margining, an eq-preset sweep — plus NVMe/GPU/GMSL/Ethernet/CAN
checks, a config-driven pytest harness, SQLite results, and a dashboard. It runs on a
real Linux test station or on a laptop via the mock backend. See `toolkit/README.md`.

Optional extras (the toolkit works without them — pure-python BER fallback, JSON configs):
`pip install scipy pyyaml fastapi uvicorn`.
