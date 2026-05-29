# `computetest` documentation

Organized by [Diataxis](https://diataxis.fr/) — four documentation modes
serving four distinct user needs:

| Mode | Orientation | When to read |
|------|-------------|--------------|
| **[tutorials/](tutorials/)** | Learning | First time using `computetest`. Walk through end-to-end to a working result. |
| **[howto/](howto/)** | Task | You know what you want to do; just need the recipe. |
| **[reference/](reference/)** | Information | You need exact facts (every flag, every field, every exit code). |
| **[explanation/](explanation/)** | Understanding | You want to know *why* the toolkit measures the way it does. |

The four modes are complementary, not redundant: a tutorial doesn't try to be
a reference, a how-to doesn't argue theory, and an explanation doesn't tell
you what to type. If a section in this tree feels confused about its mode,
flag it — that's almost always a structural smell.

## Quick links

**New here?** Start with [tutorials/01-first-bert.md](tutorials/01-first-bert.md)
— 10 minutes to a green PCIe BERT verdict, no hardware required.

**Looking for a specific recipe?** [howto/](howto/) covers running on real
hardware, emitting ocp-diag-core, writing test plans, running gage R&R, and
the troubleshooting cookbook.

**Need exact information?** [reference/cli.md](reference/cli.md) is the
authoritative CLI surface. [reference/result-shapes.md](reference/result-shapes.md)
documents every Result/Health dataclass field.

**Want to understand the math?** [explanation/bert-confidence.md](explanation/bert-confidence.md)
covers the Poisson / chi-squared BERT model. [explanation/gen6-fec.md](explanation/gen6-fec.md)
covers why PCIe 6.0 reliability is measured differently from Gen1-5.

## Repo-level docs

Outside this tree:

* [`../README.md`](../README.md) — project overview, quick install
* [`../MANUAL.md`](../MANUAL.md) — the legacy single-page manual; still
  authoritative for content not yet split into this Diataxis tree
* [`../USAGE.md`](../USAGE.md) — the legacy task-oriented usage guide
* [`research/`](research/) — deep-research roadmap that drove the Sprint 1-3
  work plan
* [`realpath-simulation.md`](realpath-simulation.md) — the QEMU+QMP AER
  injection lane (Phase 2 real-path testing)
* [`nvme/`](nvme/) — UNH-IOL v25 coverage matrix
