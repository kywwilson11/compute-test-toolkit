A test program is itself a piece of software. If the *bench-side* code (the BERT, the
plan harness, the parsers, the dashboard) isn't held to the same engineering bar as the
code under test, the program is the bottleneck — false rejects, hidden regressions,
nightly mysteries — and you end up "trusting tools" you can't actually trust. This
chapter is the engineering bar: the tooling stack that turns "the suite passes on my
laptop" into a repeatable, auditable, defensible verdict the manufacturing line, the
Quality team, and the next on-call engineer all rely on.

The Python chapter introduced the basics of `pytest`, mocking, and a smattering of CI
patterns. This chapter is the **deep, holistic** version: what each tool exists for,
how to actually use it (not toy examples), and **why** you reach for it instead of the
nearest substitute. Each section is dense on purpose — every paragraph is meant to
change what you reach for tomorrow.

The Zoox toolkit in `/toolkit/` is the running example throughout. When a section says
"see `tests/test_parsers_property.py`" or "`.github/workflows/test.yml`", those are
real files in the repo you can open and trace.

---

## 1. The shape of a quality pipeline (and why a test engineer must own it)

A **quality pipeline** is the ordered set of automated checks every change passes
through between an engineer's keyboard and a production-rack-mounted release. For a
manufacturing-test program it has four nested layers, in increasing cost and decreasing
frequency:

1. **Local pre-commit** — Lint + format + type-check + the fast unit subset, in
   sub-second. Run on every Save / before every `git commit`. **Goal:** never push
   the wrong shape of code.
2. **CI on every push and pull request** — Full unit suite + coverage gate + types +
   lint + C unit tests, in single-digit minutes. **Goal:** never *merge* a regression.
3. **Nightly** — Mutation tests, slow integration / corpus suites, real-hardware
   verification (where applicable), corpus refresh from a real station. **Goal:**
   catch the bug classes the per-PR layer is too costly to run.
4. **Release** — Tag + changelog + reproducible artifact + signed distribution.
   **Goal:** what the line runs is what was reviewed.

The defining property of a good pipeline is **honesty**: a green build means the code
is good *to the standard you publicly committed to*. Every shortcut (skipping a flaky
test instead of fixing it, lowering the coverage gate to land a refactor, suppressing
a real warning) is a debt that compounds. The pipeline must surface every honest
finding and refuse to lie about a green that wasn't earned.

The test engineer **must own this pipeline end-to-end** because nobody else has the
context that the bench code is itself life-critical instrumentation. A flaky pytest in
a web app is a bad day; a flaky test in a station-side BERT means the wrong DUTs ship.
The rest of this chapter is the toolset for owning that pipeline.

---

## 2. pytest, at the depth that matters in CI

The Python chapter showed `pytest` fixtures, `parametrize`, and basic mocking. This
section is what you do **next** — the tools and patterns that turn a 50-test toy suite
into a 500-test production pipeline.

### 2.1 Discovery and the layout that scales

`pytest` discovers tests by walking `testpaths` (set in `pyproject.toml`'s
`[tool.pytest.ini_options]`) and importing any file matching `test_*.py` /
`*_test.py`. **Layout decisions you'll regret if you skip them:**

- Put production code in `src/` (the **src layout**), not at the repo root. With
  `pythonpath = ["src"]` in `pyproject.toml`, pytest imports your package the same way
  pip does — meaning a missing `__init__.py` or a circular import breaks the suite the
  way it'll break a fresh install, not the way it works in your editor's PYTHONPATH.
- One test file per source file is the default; allow yourself one *suffix* file for
  the "edge cases / property tests / corpus replay" content (e.g.
  `test_parsers.py` + `test_parsers_property.py` + `test_parsers_corpus.py`). The
  suffix tells future-you what's expensive without having to read the file.
- Shared fixtures live in `conftest.py` at the **closest** scope they're useful. A
  station-config fixture used by every test goes in `tests/conftest.py`; a fake AER
  fixture used only by AER tests goes in `tests/aer/conftest.py`.

`conftest.py` is *not* an import path — pytest reads it automatically based on the
directory walk. That is exactly why misplaced fixtures are the most common silent
"why does this work in test_a but not test_b?" — they live above only one of them in
the tree.

### 2.2 Marks: the vocabulary of selective execution

A **mark** is metadata on a test that lets you query, skip, or treat it specially.
Three you must use fluently:

- **`@pytest.mark.skip(reason=...)` / `@pytest.mark.skipif(condition, reason=...)`** —
  declare *why* the test isn't running. `skipif(sys.platform == "win32")` is honest;
  bare `skip` without a reason is debt.
- **`@pytest.mark.xfail(reason=..., strict=True)`** — "I expect this to fail."
  `strict=True` is mandatory in CI: it turns the test red the day the bug gets fixed,
  forcing you to delete the `xfail`. Without `strict`, an `xfail` that starts passing
  silently joins the "passing" bucket and you lose the diagnostic.
- **Custom marks (`@pytest.mark.slow`, `@pytest.mark.realhw`)** — register them in
  `pyproject.toml`'s `[tool.pytest.ini_options]` `markers = [...]` to avoid the
  "PytestUnknownMarkWarning". Then `pytest -m "not slow"` is your sub-second PR gate
  and `pytest -m slow` is your nightly. Toolkit example: the real-hardware Phase 3
  tests are not in the per-PR `test.yml`; they live in `qemu.yml` and run inside a
  Linux guest.

### 2.3 Fixture resolution and the rule of explicit dependency

Fixtures resolve **by name**: when a test function takes `nvme_device` as an argument,
pytest walks up `conftest.py` until it finds a fixture by that name. Two things go
wrong constantly:

- **Implicit dependency.** A fixture quietly mutates global state and a later test
  reads it. Solution: fixtures should be *pure* (return a value, never `import` your
  production module's `_global_singleton` and mutate it). When teardown is required,
  use `yield` — `pytest` guarantees teardown even on test failure.
- **Scope mismatch.** A `scope="session"` fixture that calls a `scope="function"`
  fixture is an error pytest will catch at collection. The rule: **a higher-scoped
  fixture cannot depend on a lower-scoped one** — session can use module/function;
  function can use session. Get this right and the fixture graph stays a DAG; get it
  wrong and you create test interdependence pytest's parallel runners will surface
  randomly.

Two patterns the Zoox toolkit uses heavily:

- **The fixture factory.** `def make_device(injected_ber, ...)` returns a fresh mock
  device per call, inside the test body, when the test wants to build the topology
  itself. Avoids the parametrize explosion and reads more like a story.
- **The `tmp_path` fixture for I/O isolation.** Every test that writes to disk takes
  `tmp_path` (built-in) and writes there. `tmp_path_factory` (session-scoped) is the
  variant when you want shared-but-isolated.

### 2.4 The plugin ecosystem you'll actually use

- **`pytest-cov`** vs running coverage manually: `pytest-cov` integrates `coverage.py`
  with pytest's plugin hooks, so a coverage failure aborts the run. The toolkit uses
  bare `coverage run -m pytest && coverage report` instead — slightly less integrated,
  but means coverage doesn't fight pytest-xdist if you add parallelism later.
  **Decision rule:** if you want parallel-test execution, prefer bare `coverage`; if
  you want the simpler single command, use `pytest-cov`.
- **`pytest-xdist`** for parallel execution (`pytest -n auto`). Worth it once your
  suite hits 30+ seconds. Two warnings: (a) tests that mutate shared state will fail
  randomly under `-n auto`; (b) coverage with xdist needs `coverage[toml]` and
  `parallel = true` in `[tool.coverage.run]`.
- **`pytest-timeout`** — a global timeout that kills a hung test. Mandatory the day
  you have any real-hardware or socket-bound test; without it, a wedge in production
  code wedges CI until the runner times the whole job out, and you lose the
  diagnostic. *Concretely:* during the 2026 audit a new thermal-chamber settle-poll
  spun to its 600-second timeout against a mock that never reported "settled," and the
  whole suite hung — `--timeout=60` would have failed *that test* in a minute with its
  name, instead of a silent 10-minute wall. The toolkit doesn't ship `pytest-timeout`
  yet; that hang is the argument for adding it. Two lessons it drove home: a hang is not
  a slow test (you need a per-test deadline, not a bigger job timeout), and **the
  per-step "just run the tests I touched" gate could not see it** — only the *full*
  suite did. Run the whole suite before you trust the green; targeted gates hide
  cross-test interactions (that same audit had a second one — a corpus count that only
  broke when an unrelated module's fix changed a shared expectation).
- **`hypothesis`** — property-based testing (§5). Doesn't conflict with pytest;
  registers as a plugin and adds the `@given(...)` decorator.

### 2.5 Anti-patterns to spot in PRs

These are the test-engineer-blocking findings you should send back without merging:

- **A `try/except` inside the test** that swallows an assertion. Tests should fail
  loud; if you have to catch something, the assertion goes outside the try.
- **`time.sleep(N)`** in a test (other than a few-millisecond yield). It's slow
  and flaky. Use a monkeypatched clock, a polled condition with a timeout, or a
  pre-computed deterministic timeline (the toolkit uses an injectable `clock=` and
  `sleep=` on `bert.run_bert` for exactly this).
- **Hard-coded paths.** `open("/tmp/foo")` collides under parallel runs and fails on
  Windows CI runners. Use `tmp_path`.
- **Tests that import production code with side effects on import.** A module that
  opens a serial port or connects to an instrument *at import time* is broken design
  — but it'll only get caught when the test that imports it runs first on a fresh
  machine. Test for it explicitly with `import myproduction.module` in a fixture.

---

## 3. `unittest` — when stdlib is enough, and migrating away

`unittest` is Python's standard-library xUnit framework (`TestCase` subclasses,
`assertEqual`, `setUp/tearDown`, `discover`). You will meet it, but you should reach
for it **only** in two cases:

1. **You cannot add a dependency** — secure-environment Python where `pip install
   pytest` is blocked. (Rare in test-engineering contexts, but real for some
   manufacturing partners.)
2. **You're maintaining a legacy suite written in `unittest`** and the migration cost
   exceeds the benefit. Note: pytest discovers and runs `unittest.TestCase` classes
   natively, so you can run a mixed suite during migration.

For everything else, pytest's friction is dramatically lower. Compare:

```python
# unittest
class TestSpeed(unittest.TestCase):
    def setUp(self):
        self.fake = FakeBackend()
    def test_gen5_x16(self):
        self.assertAlmostEqual(self.fake.bps(5, 16), 5.041e11, places=-9)

# pytest
def test_gen5_x16():
    assert FakeBackend().bps(5, 16) == pytest.approx(5.041e11, rel=1e-3)
```

The pytest version is half the lines, more readable, and `pytest.approx` is more
expressive than `assertAlmostEqual`'s `places=` argument (which counts decimal
*places*, not significant figures, a footgun on values that span orders of magnitude).

**Migration recipe.** When you inherit a `unittest` suite:

1. Add pytest as a dep; commit.
2. Run `pytest` against the existing `unittest` files — they pass unchanged.
3. Replace one `TestCase` at a time: remove the class, replace `setUp` with a fixture,
   `assert*` with `assert ...`. Add the new test, delete the old, commit per
   conversion. The suite stays green throughout.

A test-engineering note on `pytest.approx` vs `unittest.assertAlmostEqual`: in BER
math (`tests/test_ber.py`) you assert `gammaincinv(...)` to `rel=1e-9` — that's
relative tolerance, the right concept for floating-point. `assertAlmostEqual` with
`places=` is wrong here. Knowing the right tolerance for the right measurement is
half the battle of writing tests that don't flake.

---

## 4. Unity — unit testing in C

Unity is a minimal, single-pair-of-files C test framework (`unity.h` + `unity.c`, no
build system, no allocator). The toolkit uses it for the BERT engine
(`c/test/test_pcie_bert.c`). It exists because **C test runners exist and you should
use one**: the alternative is `assert(...)` in `main()` and `printf` — which is fine
for one file and untenable across modules.

### 4.1 The runner shape

```c
#include "unity.h"
#include "pcie_bert_core.h"
#include "fake_cfgspace.h"

void setUp(void)    { /* per-test setup */ }
void tearDown(void) { /* per-test teardown */ }

void test_w1c_clears_only_set_bits(void) {
    fake_cfg_t fake = {0};
    fake_cfg_init(&fake, /* aer at offset */ 0x100);
    cfg_io io = fake_cfg_io(&fake);
    fake.aer_corr_status = (1u << 6) | (1u << 12);   /* BadTLP + ReplayTO */

    uint32_t got = read_and_clear(&io, 0x100 + AER_CORR_STATUS, 0xFFFFFFFFu, 4);

    TEST_ASSERT_EQUAL_HEX32((1u << 6) | (1u << 12), got);
    TEST_ASSERT_EQUAL_HEX32(0, fake.aer_corr_status);   /* both bits cleared */
}

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_w1c_clears_only_set_bits);
    return UNITY_END();
}
```

Build: `cc -g -O0 -I. -Itest test/test_pcie_bert.c c/pcie_bert_core.c test/fake_cfgspace.c test/unity.c -o test/runner`. Run: `./test/runner`. Exit code = number of failures.

### 4.2 The dependency-injection seam (why Unity unit tests aren't optional)

The toolkit's C engine doesn't talk to sysfs directly — it goes through a `cfg_io`
struct of function pointers (`read`, `write`, `ctx`). Production passes a `cfg_io`
whose `read` is `pread()` on a `/sys/.../config` file descriptor; tests pass a `cfg_io`
whose `read` reads from an in-memory `fake_cfgspace_t`. This is the same idea as a
Python `unittest.mock.MagicMock` but at the C ABI level.

The seam is **non-negotiable** for testable C. Without it your test has to actually
open `/sys/bus/pci/devices/.../config` — which means running as root, on a specific
Linux box, with a real PCIe device, while not being able to inject specific bit
patterns. Unity tests against a `cfg_io` seam can: (a) verify W1C semantics
(set-cleared-by-write-1) bit-for-bit; (b) walk capability lists with malformed-loop
inputs that real hardware can't safely produce; (c) test the `find_ext_cap` linked-list
walk by directly setting "next" pointers. These are the bugs hardware *will* eventually
present you with, and Unity catches them weeks before silicon does.

### 4.3 When to step up to cmocka

cmocka is Unity's heavier cousin: stack mocks (function-replacement at link time),
parameterized tests, group setup/teardown. Reach for it when (a) you need to mock
syscalls deeper than a function-pointer seam can reach (e.g. mocking `ioctl`), or
(b) your test program crosses translation-unit boundaries enough that link-time
substitution is cleaner than a `cfg_io` per file. For the toolkit's BERT engine the
seam is enough; cmocka would be appropriate the day we add a kernel-driver test.

### 4.4 Memory and undefined-behavior checking: where the *real* wins are

Unity catches *logic* bugs. The wins in C come from the *language*: out-of-bounds
writes, use-after-free, signed overflow. Build the test runner twice — once with
`-fsanitize=address,undefined` (AddressSanitizer + UBSan), once without. Run both in
CI. The sanitizer build catches every bug the logic tests can't see; the unsanitized
build catches the bugs the sanitizer accidentally hides (rare, but real).

```makefile
test/runner-asan: $(TEST_SRC) ; $(CC) -g -O1 -fsanitize=address,undefined $^ -o $@
ctest-asan: test/runner-asan ; ./test/runner-asan
```

For the bench, this is the equivalent of running the BERT under a logic analyzer:
strictly more information for one extra build step.

---

## 5. Hypothesis — property-based testing

A property-based test asserts **invariants over a generated input distribution**
instead of asserting one specific output for one specific input. The framework
(`hypothesis` for Python) tries hundreds of inputs and, when it finds a failing one,
**shrinks** it to the minimum failing case. Two minutes after you wrote the test you
know not "an input failed" but "*the smallest possible* input that violates your
invariant is X."

### 5.1 The mental flip: pinning behavior vs proving a property

Example/regression tests (the body of pytest) say "input `X` produces output `Y`." A
property test says "for *any* input drawn from this distribution, output Z holds."
Either alone is incomplete; together they cover orthogonal classes of bug.

The toolkit pins two real bugs via Hypothesis in `tests/test_parsers_property.py`:

- `_parse_speed`: `Speed: . G` (a malformed Speed line a real `ethtool` produced)
  fed `float('.')` and crashed. Property: **`_parse_speed` must never raise for any
  string** (real test below).
- `_stat`: `'5²'` (a value containing a Unicode digit, which `str.isdigit()` accepts
  but `int()` does not) crashed. Property: **`_stat` returns an int for any
  `(stats, key)` pair**.

```python
from hypothesis import given, strategies as st

@given(st.text())
def test_parse_speed_never_raises(text):
    assert isinstance(_parse_speed(text), int)
```

This is **four lines** of test that exhaust an input space pytest's hand-written
parametrize never could. When the toolkit added this test in the audit pass, Hypothesis
found and shrank the two bugs above in seconds. The property generalizes; the manual
regression doesn't.

### 5.2 Strategies — describing the input distribution

A `strategy` is "how to draw an input." Built-in: `st.text()`, `st.integers(min,
max)`, `st.lists(of)`, `st.dictionaries(keys=, values=)`, `st.binary(max_size=4096)`,
`st.from_regex(r"...")`. Composable: `st.lists(st.integers())` is a list of ints.

For PCIe register tests, `st.binary(max_size=4096)` is exactly the input shape of a
config space; the toolkit's `test_find_ext_cap_terminates_and_never_raises` passes a
random 4 KB blob to `find_ext_cap` and asserts the capability walk always
terminates (no infinite loops, no crashes). That's a class of bug — the malformed
"next" pointer that loops back — that real hardware *can* produce under bus error,
and that a regression test would need to know about ahead of time.

### 5.3 Shrinking, settings, and `@example` for permanent regressions

When a property fails, Hypothesis shrinks the input. The output you see in CI is the
*minimal* failing case — exactly the regression you want to pin. The right next step
is to **save it**:

```python
@given(st.text())
@example(text="Speed: . G")              # the shrunk case Hypothesis found
def test_parse_speed_never_raises(text):
    assert isinstance(_parse_speed(text), int)
```

`@example` runs in addition to generated inputs; the shrunk case becomes a
permanent, named regression. The toolkit pins the `'rx_errors_phy' shadows
'rx_errors'` and `'5²'` cases this way.

`@settings(max_examples=300)` — for slower properties, raise the count. Default 100
is fine for fast ones; for `find_ext_cap` over 4 KB blobs we use 300.

### 5.4 The `HealthCheck` argument and why `large_base_example` will bite you

Hypothesis emits warnings when a strategy is "unhealthy" (too many filter-rejected
examples, base example too large, etc.). The default is to fail the test on these
warnings. For a strategy of `st.binary(max_size=4096)` Hypothesis complains about
`large_base_example` — the "first example" is the empty string, which is small;
when we ask for 4 KB blobs the variance triggers the heuristic.

The fix is **never** to suppress the warning globally. It's to set `min_size=0` on
the strategy, so the empty case is a legal example, and the framework stops
complaining. The toolkit learned this the hard way in the Phase 0 commit; the
correct strategy is `st.binary(max_size=4096)` with the implicit `min_size=0`.

---

## 6. mutmut — mutation testing, and what it actually buys you

Coverage tells you what *executed*. Mutation testing tells you what your tests
**would detect a change in**. The difference matters: a line can be 100 %
"covered" by a test that asserts nothing about it (touched but not validated).
Mutation testing exposes that by *modifying* your code, re-running the suite, and
reporting which mutations the suite *failed to notice*.

### 6.1 Mechanics

`mutmut run` parses every source file in `paths_to_mutate` (the toolkit configures
this in `setup.cfg` to `ber.py,bert.py,aer.py,linkstate.py,topology.py,
diagnostics.py` — the decision/math/logic modules) and emits **one mutant at a
time**: `x < y` becomes `x <= y`; `+ 1` becomes `+ 2`; `True` becomes `False`;
strings get prefixed with `XX` and suffixed with `XX`. For each mutant the runner
applies the change, runs `pytest -x -q`, and records:

- **`ok_killed`** — the test suite caught the change (failed). Good.
- **`bad_survived`** — the test suite still passed despite the change. **A gap.**
- **`bad_timeout`** — the change broke the suite into a loop. Usually the same
  signal as killed.

The aim is the **survivor count** trending toward zero. **It will never reach
zero** — mutations of equivalent code (a tolerance constant that doesn't change
results within float precision, a `_HAVE_SCIPY = True/False` import probe on a
machine where scipy is installed) cannot be killed by *any* test that doesn't
change the runtime environment. Those are called **equivalent mutants**, and you
document them in your audit notes; the goal is "no *meaningful* survivor", not
zero survivors.

### 6.2 What a survivor actually tells you

Three categories, in priority order:

1. **A real gap.** The toolkit had a survivor on `GEN4_X16_BPS = link_bits_per_second(4, 16)`
   — mutmut mutated `(4, 16)` to `(5, 16)`, every test still passed, because no
   test pinned the constant's value. The fix is a one-line test:
   `assert ber.GEN4_X16_BPS == pytest.approx(2.520e11, rel=1e-3)`. Survivor killed.
2. **An equivalent mutant.** `tiny = 1e-300` mutated to `tiny = 1e-299` doesn't
   change the continued-fraction convergence in `_gcf` within float precision; no
   test can kill this without artificially restricting the input. Document and
   accept.
3. **A spec-internal constant you've been carrying for years.** The audit-2 round
   turned up several of these in `ber.py`'s scipy-fallback path — they survived
   because the scipy path is the default and the fallback only runs when scipy is
   uninstalled. The kill: a test that `monkeypatch.setattr(ber, "_HAVE_SCIPY",
   False)` and asserts the fallback produces the same numbers. Real value.

### 6.3 The runner cost, and why mutmut belongs in nightly CI

A killed mutant exits as soon as the first test fails (with `pytest -x`), so
~1 second. A survivor runs the whole suite to discover *nothing* fails — at the
toolkit's 13 second baseline, ~13 seconds. With ~1264 mutants and a 30 % survivor
rate, that's about 105 minutes of wall-clock. **Mutmut belongs nightly, never on
the per-PR gate.** The toolkit's `.github/workflows/mutmut.yml` runs at 07:00 UTC
with `timeout-minutes: 150`; the result is an artifact a human triages weekly,
not a blocker on the PR cycle.

The toolkit deliberately scopes `paths_to_mutate` to the *decision/math/logic*
modules. Mutating `nvme.py`'s `subprocess.run` line just turns the call into a
crash that the test suite has no business catching (the real path is pragma'd out
of coverage). Scoping mutmut to the modules the unit suite *fully* exercises is
what keeps the survivor count meaningful.

### 6.4 When NOT to run mutation testing

If your suite has *coverage* gaps, mutmut will produce a flood of "survivors" that
are really "lines no test ever touched." Fix coverage first. Mutation testing is
the next layer up: it asks "is each covered line *asserted on*?", which is only a
useful question once coverage is solidly above 90 %.

---

## 7. coverage.py — what to measure, what *not* to

Coverage (Python's `coverage.py`, integrated by `pytest-cov`) measures which lines
your tests execute. The Zoox toolkit measures **branch coverage**
(`[tool.coverage.run] branch = true`), which counts the **decisions** (both legs of
each `if`/`while`) rather than the lines. Branch coverage is **strictly stronger**:
a test that runs `if x: foo()` with only truthy `x` shows 100 % line coverage but
50 % branch coverage; line coverage hides the missed false-branch.

### 7.1 The `exclude_lines` philosophy: declare what you're NOT measuring

Some code is *deliberately* not unit-testable. The toolkit's real-hardware paths
(`subprocess.run(["nvme", ...])`) are validated on the bench, not in the unit
suite; the unit suite would have to mock or assume what nvme-cli produces, neither
of which gives you confidence. The honest move is to **declare** that those lines
are deliberately uncovered and exclude them from the coverage *gate*:

```toml
[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",                # real-hw sysfs/subprocess paths
    "if __name__ == .__main__.:",      # module demos
    "raise NotImplementedError",       # placeholder paths
    "if TYPE_CHECKING:",               # static-typing-only imports
]
```

Then `# pragma: no cover` on the real path's branch and the gate counts only what
*should* be covered. The toolkit sits at ~97 % branch coverage with this rule;
without the rule it would sit far lower and everyone would learn to ignore the
warning, which is much worse than the honest 97 %.

### 7.2 `fail_under` is the gate, not the goal

`fail_under = 95` in `[tool.coverage.report]` makes `coverage report` exit
non-zero below 95 %; CI's "Run coverage" step fails. You set this **just below
where you actually sit** (we're at 98 %, gate is 95 %) — that gives a small honest
buffer for the corpus or fixture work that occasionally swings ±1 %. Setting it
*above* where you actually sit makes CI red on day one; setting it *equal* to
where you sit makes refactors that legitimately swing -0.1 % red the next day.
Both errors destroy operator trust in the gate; the right answer is "a few
percent below."

### 7.3 Coverage isn't quality. Read the misses, not the percentage.

`coverage report -m` produces the line-by-line miss list. When you do that on the
toolkit you see things like `src/computetest/cli.py:226-227` (the
`KeyboardInterrupt` handler) and `backend.py:268` (the speed-code fall-through for
an unknown `speed_str`). Those are the gaps mutation testing will eventually
exploit — closing them is more valuable than chasing the last 0.5 %.

The audit-2 round turned coverage misses **into bug fixes**: the
`KeyboardInterrupt` miss was "no test exists, because no test had been written for
this exit semantic"; once we wrote `test_keyboard_interrupt_maps_to_exit_130` the
miss closed *and* a real CLI behavior got pinned.

---

## 8. The static-analysis stack — ruff, black, mypy

Static analysis runs your code through a tool that **infers properties without
executing it**. The wins are speed (millisecond, not second), independence from
tests (you'd never catch some of these bugs at runtime), and the discipline of "the
code can't even merge in this shape." The three tools below each target a different
property; you want all of them, run as a single pre-commit / PR gate.

### 8.1 ruff — the linter and now-also-the-formatter

`ruff` (Rust-written, ~100× faster than the Python `flake8`/`pylint` tools it
replaces) does **lint** (warn on bad code shapes) and, since 0.1, **format** (rewrite
to a canonical style). The toolkit configures it in `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP"]
```

The `select = [...]` is the rule families:

- **`E`/`W`** (pycodestyle) — whitespace and style consistency. Not a value judgment,
  but predictable formatting reduces review friction.
- **`F`** (pyflakes) — *unused imports, undefined names, shadowed variables*. These
  catch real bugs, not style: an unused import means a deleted feature left a stub.
- **`I`** (isort) — sorted, grouped imports. Mechanical, but `--fix` does it for free
  and PR diffs stop containing churn-changes.
- **`B`** (`flake8-bugbear`) — **real-bug findings**: `B904` (`raise ... from None`
  vs nothing inside an except — preserves traceback), `B008` (function call in
  argument default — the mutable-default antipattern), `B905` (`zip` without
  `strict=` — silently drops elements if lengths differ). Audit-1 found two of
  these in real toolkit code; both were real bugs, not style issues.
- **`UP`** (`pyupgrade`) — keeps you on modern Python idioms: `% format` → f-string,
  redundant `()` in type annotations, `Tuple[int]` → `tuple[int]` on `py310+`. Easy,
  mechanical, no behavior change.

What ruff *won't* catch: anything that needs to know your types. That's what mypy is
for.

### 8.2 mypy — types as a category of test you don't have to write

`mypy` reads your type hints (`def f(x: int) -> str:`) and reports inconsistencies.
A useful way to think about types: **types are the cheapest property tests in the
language**. Where a Hypothesis test would say "for any `str` input, this never
raises," a type hint says "this only takes `str` — the caller passing `bytes` is a
PR-time error, not a runtime crash."

The toolkit runs mypy with three strictness knobs from `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.10"
files = ["src"]
strict_optional = true            # `None` is never silently a `str`
no_implicit_optional = true       # `def f(x: int = None)` becomes an error
warn_unused_ignores = true        # rot the `# type: ignore` comments out over time
```

`strict_optional` is the one that catches the most real bugs. Without it, mypy
treats `None` as compatible with any type — so `f(): -> str` returning `None` when
the file isn't found compiles. With it, you get a type error at the location your
code is silently producing the wrong thing, and you write the explicit `str | None`
or `assert x is not None`.

Audit-1 (`margining.py:67`, `diagnostics.py:59`) found real `AttributeError`-on-`None`
bugs this way: `worst_lane` returned `LaneMargin | None`, and the code accessed
`.lane` without checking. The right fix is a real `assert ... is not None` with a
comment explaining the invariant, not a `# type: ignore`. Those `assert`s now
document the property and crash early if the property breaks.

**Reasonable mypy goal:** zero errors on `src/` with `strict_optional = true`,
ratcheting up to `disallow_untyped_defs` once the codebase is fully annotated. The
toolkit hits the former; not yet the latter.

### 8.3 black (and why ruff format mostly replaces it)

`black` is Python's most popular auto-formatter — opinionated, near-zero config,
"the resulting style is the same regardless of who pushed the commit." Its value
isn't aesthetic; it's that **review diffs are about logic, not formatting**. Once
your codebase is black-formatted, every PR's diff is signal.

`ruff format` (since ruff 0.1) implements the same formatting model as black, with
the same defaults; the toolkit could use either. For a new project today, just use
`ruff format` — one tool, one config, one CI step. Keep `black` only if you're in a
codebase that already uses it and the team prefers it.

The unenforceable disagreement: `black` enforces double-quote strings; the toolkit
doesn't use either formatter and the codebase has mixed quotes. **This is fine
until it isn't** — the moment a PR review starts including "fix the quotes," add
the formatter and resolve the entire codebase in one commit.

### 8.4 Composition: pre-commit, the run-this-locally orchestrator

`pre-commit` (a separate tool, `pip install pre-commit`) is the local-side
orchestrator: a `.pre-commit-config.yaml` lists the hooks (ruff, mypy, etc.) to
run on every `git commit`. Configured well, it catches the lint/type findings
**before** they leave your laptop, so CI on push never sees them.

A minimal `pre-commit` config for a Python toolkit:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
        additional_dependencies: [pytest, types-PyYAML]
```

Then `pre-commit install` writes a git hook; `pre-commit run --all-files` reruns
everything on demand. The Zoox toolkit doesn't yet ship a `.pre-commit-config.yaml`
— a reasonable next addition once the CI gates are settled.

---

## 9. GitHub Actions, from first principles

A **workflow** is a YAML file under `.github/workflows/` that GitHub runs on
specified events (`push`, `pull_request`, `schedule`, `workflow_dispatch`). The
workflow contains **jobs**, each running on a fresh **runner** (GitHub-hosted VM by
default). Each job is a sequence of **steps**: a checkout, a Python setup, a `run:`
command. **You read a workflow top-down, like a script.**

### 9.1 The minimum viable workflow

```yaml
name: test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12', cache: 'pip' }
      - run: pip install -e '.[dev]'
      - run: python -m pytest
```

Eight lines of config, ten seconds of wall-clock per push. **Every test repo should
have this**, before anything else.

### 9.2 The matrix — why you fan out

```yaml
strategy:
  fail-fast: false
  matrix:
    os: [ubuntu-latest, macos-latest]
    python-version: ['3.10', '3.11', '3.12', '3.13']
```

Eight combinations run in parallel. `fail-fast: false` is mandatory: without it, the
first failure cancels the rest and you can't see *which* combinations broke. The
toolkit's matrix found two real CI-only bugs (a mypy `import yaml` failure absent
locally, a macOS-only flake in the BERT test) that single-config CI would never have
caught.

The shape of a matrix follows your customer surface. The toolkit supports Linux and
macOS station-side; not Windows. The matrix reflects that.

### 9.3 Triggers, paths, and the cost of running on every push

```yaml
on:
  push:
    paths: ['toolkit/**', '.github/**']
  pull_request:
  workflow_dispatch:                  # let humans trigger from the UI
  schedule:
    - cron: '0 7 * * *'               # 07:00 UTC daily
```

`paths:` is the underrated cost-control: a docs-only PR doesn't need to spin up the
full matrix. `workflow_dispatch` lets you run the workflow on demand from the
Actions UI — the toolkit uses this for `mutmut.yml` so you can re-run nightly off
the schedule. `schedule:` runs unattended; **always set `timeout-minutes`** on
scheduled jobs because a runaway one consumes your minute budget.

### 9.4 Secrets, artifacts, and the test-engineer's CI hygiene

- **Secrets** (`${{ secrets.NAME }}`) — never check a token into source. The Actions
  UI is the only place secrets live; jobs can reference them by name.
- **Artifacts** (`actions/upload-artifact`) — files the job produces (`.mutmut-cache`,
  coverage XML, serial-log dump on failure). The job uploads them; a human downloads
  via the run page. The toolkit's `qemu.yml` uploads `.work/serial.log` `if:
  failure()` so a failed boot is debuggable without rerunning.
- **Caching** (`actions/setup-python@v5 with: cache: 'pip'`) — `pip install` runs
  in seconds instead of minutes on warm cache. The Python version is the cache key,
  so the matrix correctly caches per-version.

### 9.5 `continue-on-error` and honest CI

A step with `continue-on-error: true` shows in the UI as a warning instead of a
fail. **Use it sparingly and honestly**: the toolkit marks the Phase 2 e2e step
`continue-on-error: true` because the x86 TCG path is documented-best-effort
(*not* because we want to hide a failure). The README explains why. **The
moment you set `continue-on-error: true` to "make CI green," you're lying to the
team about a failure**, and the next person to read the workflow has to discover
the lie. Don't do that.

### 9.6 Mapping workflow files to intents

The toolkit ships three workflows; the design is intentional:

- **`test.yml`** — the per-PR gate. Lint + types + tests + coverage. Eight-cell
  matrix. **Blocks merges.**
- **`qemu.yml`** — the privileged real-hardware-path verification: boot a Linux
  guest, drive QMP from the host, run the toolkit's real backend inside the guest.
  Slow (10–15 min). Runs on `toolkit/sim/qemu/**` or `toolkit/c/**` path changes.
- **`mutmut.yml`** — nightly mutation testing. 150-min timeout. Uploads
  `.mutmut-cache` as artifact; a human triages weekly. **Doesn't block merges.**

This split — **fast + blocking on PR, slow + not-blocking on schedule** — is the
ergonomic shape every test-engineering repo should converge to.

---

## 10. Test data: fixtures, fakes, corpus, contract tests

Where do your test inputs come from? Three sources, in increasing strength:

1. **Hand-written fixtures** (a literal in the test file). Fast and clear; **only
   valid for cases small enough to read at a glance**.
2. **Verified fakes** — a mock that behaves like the real thing, validated by a
   *contract test* that runs the same assertions over the mock *and* the real
   (or real-shape) backend.
3. **Corpus** — actual output from the real tool, captured to a versioned text
   file, replayed in tests.

### 10.1 Verified fakes and contract tests (the toolkit's `RealBackend`+fixture pattern)

A mock that's only ever tested against itself is **not** a substitute for the
real thing — it's a fixed point of confusion that drifts from reality. The
toolkit's `tests/sysfs_fixture.py` builds a tmp-dir fake `/sys/bus/pci` tree with
byte-accurate config-space blobs and real symlinks, and
`tests/test_backend_contract.py` runs the **same 22 assertions** against the
`MockBackend` and a `RealBackend(sys_root=fake)`. That's a contract test: any
property the real backend must have is asserted against both implementations.
Drift between mock and real becomes a red CI immediately, not a Heisenbug at the
bench.

The pattern generalizes. Whenever you have a "fake" anything, write the assertion
in a function and call it twice — once for the fake, once for whatever you have
of the real thing.

### 10.2 Corpus tests — pinning real-tool output as ground truth

`tests/test_parsers_corpus.py` opens `corpus/nvme/2.10/samsung-pm9a3/smart-log.json`
(literal output of `nvme smart-log -o json` captured from a real station) and asserts
the toolkit's parser produces the expected verdict. Two reasons to do this:

1. **Format drift.** `nvme-cli 2.11` renamed `avail_spare` to
   `available_spare`. The corpus captures **both versions**; the test runs against
   each; the day a parser quietly stops handling the old name, a corpus replay
   catches it.
2. **Bug pinning.** The "abbreviated keys" bug (the toolkit's
   `_normalize_smart_keys`) is recorded as a corpus entry — the exact JSON shape
   that triggered the regression — so the fix is permanent.

Corpus tests are the right answer to "we don't trust the mock, we don't have the
hardware in CI." Capture once, replay forever, refresh on a schedule.

### 10.3 Refreshing corpus — the `corpus-refresh.yml` workflow

A nightly workflow can run `nvme smart-log` inside a container with the latest
`nvme-cli` and diff against the committed corpus. When the schema shifts, it opens
a PR; the parser test that fails on the new corpus is your warning to update the
parser. **You learn about a format change before manufacturing does**, on the
tool maintainer's release rhythm, not yours.

### 10.4 The tautology trap — the oracle must be the spec, not the code

§6 asked "is each covered line *asserted* on?" This goes one level deeper: **what is the
assertion compared against?** A test's *oracle* is the source of its expected value, and
the single most common way a green suite hides a spec-*wrong* program is an oracle
**derived from the implementation itself**:

- A corpus `expected.json` generated by *running the parser you're testing* and saving
  its output. The replay then asserts the parser reproduces what the parser produced — it
  can never fail, and it freezes in whatever bug the parser had the day you captured it.
- A round-trip: `decode(encode(x)) == x`. That proves the codec is self-*consistent*, not
  that the wire format is *correct*. A register field packed at the wrong bit offset
  round-trips perfectly and ships.
- A "golden" number copied from the code's current output instead of computed from the
  datasheet.

Each passes on day one and stays green through every refactor — while the device on the
bench, which speaks the *actual* spec, is decoded wrong. The toolkit's 2026 audit found
dozens: a DDR5 SPD corpus built to satisfy a parser reading the wrong byte (ECC at DDR4's
byte 13, not DDR5's byte 235); an NVMe-MI header round-trip that "passed" with every field
at the wrong bit position; CXL error-bit maps asserted against the code's own off-by-two
table. **Anchor the oracle outside the code:**

- **Golden vectors from the standard.** Hand-derive the expected bytes from the spec
  figure or a published worked example, write them as literals, assert the code reproduces
  *those*. The toolkit re-anchored its NVMe-MI test to a golden NMP byte (`0x09` for a
  request / MI-command / CSI=1) computed from the spec, not from the encoder.
- **A second, independent implementation.** Cross-check against `linuxptp`, the Linux
  kernel decode tables, `libnvme`, QEMU's QAPI schema. Agreement with an independent source
  is evidence; agreement with yourself is not.
- **A real-hardware capture** (§10.2) whose ground truth is hand-verified once, replayed
  forever.

Litmus test for any test you inherit: *if the implementation were subtly wrong, could this
test still pass?* If yes, its oracle is the code, and it is theater.

> **A tool that flags a bug is itself a fallible oracle — verify before you "fix."** When
> you run a static analyzer, a coverage report, or (as the toolkit did) a large automated
> audit over your own test program, the findings are *hypotheses*, not facts. The toolkit's
> audit had a meaningful **false-positive rate** — several "bugs" were the audit
> misreading a spec (a density table that was already correct, a gPTP delay formula that
> matched `linuxptp`, a coherency rule that was right). Applying those blindly would have
> *introduced* the bug the audit imagined. Verify every finding against the authoritative
> source before changing code; a test engineer who "fixes" an unverified finding has just
> shipped a regression with a confident commit message.

---

## 11. The pipeline you build at the bench (a concrete worked example)

Stitch the above into the pipeline the Zoox toolkit's `.github/workflows/` codifies:

**On every commit (locally, via pre-commit):** ruff + mypy on the staged files.
Sub-second; runs the format-fixer too.

**On every push and PR (`test.yml`):**
1. **Lint** (job 1, fastest, fails earliest) — `ruff check src tests`.
2. **Types** (still job 1) — `mypy src`.
3. **Tests** (job 2, the matrix, parallel) — `coverage run -m pytest && coverage
   report` with `fail_under = 95`. Ubuntu+macOS × py3.10–3.13.
4. **C tests** (job 3, ubuntu only) — `make ctest`.

Total wall-clock: ~2 min on a warm cache. Blocks merge.

**On every push to a sim-changing path (`qemu.yml`):**
1. **Unit** (job 1, same machine) — `pytest tests/test_qmp_inject.py`.
2. **E2E** (job 2, ubuntu, 30-min timeout) — boot a TCG guest, run Phase 2 AER
   injection, run Phase 3 real-kernel-path vdev checks.

Total: ~12 min. Doesn't block merge for Phase 2 (TCG-vs-HVF caveat); does for the
unit + Phase 3 paths.

**Nightly (`mutmut.yml`):**
1. Spin a ubuntu runner. Install dev deps. Run `mutmut run` on the 6 scoped
   modules with `timeout-minutes: 150`. Upload `.mutmut-cache` as artifact.

Total: 60–150 min. Doesn't block anything; produces a triage queue.

**Release (when you cut one):**
1. Tag the commit. CI runs the test workflow against the tag. Tag-bound artifact
   gets attached to the GitHub release. The line consumes that artifact.

This is the pipeline shape **every** Zoox test-engineering repo should converge
to. The compositions matter:

- **Lint blocks types blocks tests.** If lint fails, types and tests don't run —
  why pay for an 80-second matrix when the formatter would have caught it?
- **Tests block the matrix scope.** Run *one* fast cell first (Ubuntu / 3.12); if
  it passes, the matrix runs in parallel. If it fails, you save runner minutes.
- **Nightly is for what's too slow to gate.** Mutation testing, corpus refresh,
  long-soak real-hardware. Their finding rate is "weekly" not "per-PR", and their
  cost is multi-minute — wrong gate, right schedule.

---

## 12. Reading CI output: what to ignore, what to fix today

A real CI failure history reads like a debugger session — you should be able to look
at the run page and infer *what* broke, *where*, and *why*. Build the habit of
treating the run page as data, not noise:

- **Read the failed step's output bottom-up.** Test failures print the stack and
  the assertion at the bottom; everything above is noise. The toolkit's
  `test/macos-3.13` failure in the audit round was *one line at the bottom* of a
  3000-line log: `assert 'pass' == 'fail'`, with the test name. Diagnosing it took
  90 seconds because the test was small and the assertion was clear.
- **A failure that reproduces only on CI is real.** Don't write it off as "CI
  weirdness." The macOS-only BERT flake was a wall-clock dependency the test
  shouldn't have had; CI surfaced it because CI's wall-clock is different from
  yours. The fix (inject a deterministic clock) made the test better forever, not
  just for CI.
- **A failure on *one* cell of a matrix is information.** Python 3.13 on Ubuntu
  failing while Python 3.12 on Ubuntu passes tells you the bug is a
  3.13-typing-only finding (a `mypy` strict-optional false negative resolved in
  3.13's typeshed, e.g.). Read the matrix as a coordinate system.
- **A timeout is a hung test, not a slow test.** The right fix is `pytest-timeout`
  per-test, not bumping the workflow timeout. A test that should take 100 ms and
  takes 5 minutes is broken; making CI wait longer for the broken test hides the
  break.

The CI output is the only person on the team who's seen every failure. Read it
carefully.

---

## 13. What this entire stack does for you

Pick any production toolkit and ask: *can you delete a 300-line module, refactor the
caller, and merge it with confidence by Tuesday?* If yes — your tests + types +
coverage + linting + mutation testing + CI gate make the rewrite **provable** rather
than scary. If no — you've been running on hope, and hope is what fails on the
production line at 03:00 in a manufacturing context where calling a senior engineer
isn't possible.

That is what every section in this chapter, taken seriously, buys: **the budget to
refactor.** A test program isn't done when it works; it's done when the next person
can change it. The path to "the next person can change it" is the stack above.

The Zoox compute toolkit's metrics today — ~1,166 tests, 97 % branch coverage,
ruff + mypy clean, a nightly mutation lane, three CI workflows totaling 11
build-gate minutes per push — are not the goal. **Trust** is the goal. The metrics
are how you and the rest of the manufacturing organization decide whether to trust
the verdict the BERT just printed. Build the pipeline so that every signal — lint
clean, types clean, tests green, coverage above the gate, no surviving mutants in
your scoped modules — is one a downstream operator can act on without re-checking
your work.
