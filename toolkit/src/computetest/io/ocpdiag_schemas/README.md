# OCP ocp-diag-core v2.0 JSON schemas

Vendored verbatim from
[opencomputeproject/ocp-diag-core](https://github.com/opencomputeproject/ocp-diag-core/tree/main/json_spec/output)
on **2026-05-29**.

Used by `tests/test_ocpdiag.py::TestSchemaConformance` to validate that every
artifact emitted by `computetest.io.ocpdiag.Emitter` conforms to the published
schema. Vendoring keeps the tests hermetic; refresh the directory in lock-step
with the upstream spec.

To refresh:

```bash
cd toolkit/src/computetest/io/ocpdiag_schemas
for f in *.json; do
  curl -fsSL "https://raw.githubusercontent.com/opencomputeproject/ocp-diag-core/main/json_spec/output/$f" -o "$f"
done
```
