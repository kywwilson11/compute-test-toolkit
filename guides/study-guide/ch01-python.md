## Python for Test Automation

### CPython Memory Model

Everything in CPython is an object on the heap. A variable is a name that binds to an object; assignment copies the reference, not the object.

```python
a = [1, 2, 3]
b = a          # b and a point at the same list
b.append(4)
print(a)       # [1, 2, 3, 4]  -- alias bug

b = a[:]       # shallow copy breaks the alias
b = list(a)    # equivalent
import copy
b = copy.deepcopy(a)  # deep copy for nested structures
```

CPython uses reference counting as the primary GC mechanism. Every object carries a `ob_refcnt` field; it drops to zero the moment the last reference goes away and the object is immediately reclaimed. A cyclic garbage collector (generation-based) handles reference cycles that reference counting cannot break. `sys.getrefcount(x)` returns the count + 1 (the argument slot itself adds one).

**Small-integer interning**: CPython pre-allocates integers in `[-5, 256]`, so `a is b` is `True` for those values. Outside that range, identical int literals may or may not be the same object depending on the compile unit — never use `is` to compare integers or strings in production code.

```python
x = 256; y = 256
print(x is y)   # True  (interned)
x = 257; y = 257
print(x is y)   # True inside one code block (compiler optimization)
                # may be False if constructed at runtime
```

**Pass-by-object-reference**: Functions receive a reference to the object, not a copy of it. Rebinding the parameter name inside the function does not affect the caller's binding. Mutating the object does.

```python
def bad_reset(lst):
    lst = []      # rebinds local name; caller unchanged

def good_clear(lst):
    lst.clear()   # mutates the object; caller sees it
```

#### Mutable vs Immutable and Hashability

| Type | Mutable | Hashable |
|------|---------|----------|
| `int`, `float`, `bool`, `str`, `bytes`, `tuple` (if contents hashable), `frozenset` | No | Yes |
| `list`, `dict`, `set`, `bytearray` | Yes | No |

Hashability gates dict key and set membership eligibility. A `tuple` is only hashable if every element is hashable:

```python
t = (1, [2, 3])
hash(t)   # TypeError: unhashable type: 'list'
```

Custom classes are hashable by default (id-based hash). If you define `__eq__`, Python sets `__hash__ = None` — you must also define `__hash__` explicitly if you want instances to be usable as dict keys.

---

### Numeric Types

#### Integers

Python `int` has arbitrary precision — no overflow. Bit-manipulation for hardware registers is exact.

```python
REG = 0xABCD_1234

def extract_field(value: int, lsb: int, width: int) -> int:
    """Extract a contiguous bit field from an integer register value."""
    mask = (1 << width) - 1
    return (value >> lsb) & mask

# PCIe Link Status register (16-bit)
LINK_STATUS = 0x1043
speed_enc  = extract_field(LINK_STATUS, 0, 4)   # bits [3:0]
width_enc  = extract_field(LINK_STATUS, 4, 6)   # bits [9:4]
print(f"speed=0x{speed_enc:X}  width=x{width_enc}")

# Formatting
val = 0b1010_1100
print(f"hex={val:#010x}  bin={val:#010b}  dec={val}")
# hex=0x000000ac  bin=0b10101100  dec=172
```

Bitwise operators: `&` AND, `|` OR, `^` XOR, `~` NOT, `<<` left-shift, `>>` right-shift. All work at arbitrary width.

```python
# Set bit N
def set_bit(val, n):    return val | (1 << n)
def clear_bit(val, n):  return val & ~(1 << n)
def toggle_bit(val, n): return val ^ (1 << n)
def test_bit(val, n):   return bool(val & (1 << n))
```

#### Floats and IEEE 754

`float` is a C `double` — 64-bit IEEE 754. Never use `==` for float comparison.

```python
import math

EXPECTED_VOLTAGE = 3.3
measured = 3.299_987

# bad
assert measured == EXPECTED_VOLTAGE

# good: rel_tol handles large magnitudes, abs_tol handles near-zero
assert math.isclose(measured, EXPECTED_VOLTAGE, rel_tol=1e-4)

# absolute tolerance for specs where relative doesn't make sense
assert math.isclose(current_A, 0.0, abs_tol=1e-6)   # "near zero"
```

`rel_tol` defaults to `1e-9`; `abs_tol` defaults to `0.0`. For hardware specs you usually want `abs_tol` when the expected value is near zero and `rel_tol` otherwise.

```python
from decimal import Decimal, ROUND_HALF_UP

# Exact decimal arithmetic for spec limits
limit = Decimal("3.3000")
reading = Decimal("3.2997")
within = abs(reading - limit) <= Decimal("0.0010")
```

#### Integers as Enum/IntFlag for Registers

```python
from enum import IntFlag, auto

class LinkSpeed(IntFlag):
    GEN1 = 0x1
    GEN2 = 0x2
    GEN3 = 0x4
    GEN4 = 0x8
    GEN5 = 0x10

cap = 0x1F   # all speeds supported
if LinkSpeed.GEN4 in LinkSpeed(cap):
    print("GEN4 capable")
```

---

### Strings and Bytes

#### Core String Methods (Daily Reference)

```python
s = "  PCIe Gen4 x16 link  "
s.strip()              # "PCIe Gen4 x16 link"
s.lstrip() / s.rstrip()
s.upper() / s.lower() / s.title()
s.startswith("PCIe") / s.endswith("link  ")
s.find("Gen")          # 7; returns -1 if not found
s.index("Gen")         # 7; raises ValueError if not found
s.replace("Gen4", "Gen5")
s.split()              # ["PCIe", "Gen4", "x16", "link"] (whitespace default)
s.split(":")           # split on delimiter
":".join(["a", "b"])   # "a:b"
s.count("e")           # 2
"42".zfill(6)          # "000042"
s.partition("Gen")     # ("  PCIe ", "Gen", "4 x16 link  ")  -- key idiom

# Python 3.9+
s.removeprefix("  PCIe ")
s.removesuffix("  ")
```

`str.partition(sep)` is the idiomatic way to split once and keep the separator context — heavily used when parsing `key: value` lines from lspci/dmesg output.

#### f-String Format Spec

```python
val = 3.141592653
f"{val:.4f}"            # "3.1416"
f"{val:10.4f}"          # "    3.1416"  (width 10, right-aligned)
f"{val:<10.4f}"         # "3.1416    "  (left-aligned)
f"{val:+.3f}"           # "+3.142"
reg = 0xDEAD_BEEF
f"0x{reg:08X}"          # "0xDEADBEEF"
f"{reg:#010x}"          # "0xdeadbeef"
f"{42:06b}"             # "101010"
n = 1_234_567
f"{n:,}"                # "1,234,567"
f"{n:_}"                # "1_234_567"

# Expressions and calls inside braces
items = [3, 1, 4, 1, 5]
f"sorted: {sorted(items)!r}"
f"{'OK' if val > 0 else 'FAIL'}"

# Multi-line (no backslash needed inside braces)
result = (
    f"device={device!r} "
    f"speed={speed_gbps:.2f} Gb/s "
    f"status={'PASS' if pass_ else 'FAIL'}"
)
```

#### Bytes and Encoding

Serial ports, binary firmware files, and SCPI instrument responses all work in bytes.

```python
# str <-> bytes
raw = b"\x41\x42\x43"
raw.decode("ascii")           # "ABC"
"ABC".encode("ascii")         # b'ABC'

# Error modes
"café".encode("ascii", errors="ignore")     # b'caf'
"café".encode("ascii", errors="replace")    # b'caf?'
"café".encode("ascii", errors="backslashreplace")  # b'caf\\xe9'

# Struct: pack/unpack binary protocol frames
import struct
frame = struct.pack(">HHI", 0x0001, 0x0004, 0xDEADBEEF)  # big-endian
cmd, length, payload = struct.unpack(">HHI", frame)

# struct format character quick reference:
#  Byte order prefix: > big-endian  < little-endian  = native  ! network (big)
#  B unsigned byte (1)   b signed byte (1)
#  H unsigned short (2)  h signed short (2)
#  I unsigned int (4)    i signed int (4)
#  Q unsigned long long (8)  q signed long long (8)
#  f float (4)  d double (8)  s char[] (prefix with count: "4s")
#  x pad byte (no corresponding Python value)

# struct.calcsize tells you the on-wire byte count before you allocate a buffer
assert struct.calcsize(">HHI") == 8

# unpack_from reads from a buffer at an offset without copying
# (useful when parsing frames inside a larger bytearray)
raw = bytearray(16)
raw[4:12] = frame
cmd2, length2, payload2 = struct.unpack_from(">HHI", raw, offset=4)

# iter_unpack: parse a binary log file of fixed-width records efficiently
RECORD_FMT = struct.Struct("<IHH")   # compile once; use .unpack_from / .iter_unpack
with open("telemetry.bin", "rb") as f:
    data = f.read()
for ts_ms, temp_raw, flags in RECORD_FMT.iter_unpack(data):
    process(ts_ms, temp_raw * 0.125, flags)

# bytes are immutable; bytearray is mutable
buf = bytearray(256)
buf[0] = 0xAA
buf[1:3] = b"\xBB\xCC"
```

---

### Collections

#### list

`list` is a dynamic array. Appends and pops from the right are O(1) amortized; inserts/deletes at position 0 are O(n).

```python
readings = []
readings.append(3.301)           # O(1)
readings.insert(0, 3.295)        # O(n) -- avoid in hot loops
readings.extend([3.302, 3.300])
readings.sort(reverse=True)
readings.sort(key=lambda x: abs(x - 3.3))  # sort by distance from nominal
top3 = readings[:3]              # slicing; creates a new list

# Sorting stability: sort by secondary key first, primary key second
pairs = [(1, "b"), (2, "a"), (1, "a")]
pairs.sort(key=lambda t: (t[0], t[1]))  # (1,'a'), (1,'b'), (2,'a')
```

#### tuple

Immutable, hashable (if contents are), lighter than list. Prefer tuples for fixed-length records.

```python
Point = (x, y, z) = 1.0, 2.0, 3.0   # unpacking
a, *rest, z = [1, 2, 3, 4, 5]        # star unpacking

# Swap without temp
x, y = y, x
```

#### NamedTuple

```python
from typing import NamedTuple

class LinkStatus(NamedTuple):
    bdf: str
    speed: str
    width: int
    retrain_count: int = 0

ls = LinkStatus("0000:03:00.0", "16 GT/s", 16)
print(ls.speed)        # "16 GT/s"
print(ls._asdict())    # OrderedDict for JSON serialization
bdf, speed, *_ = ls   # still iterable/unpackable
```

#### dict

Python 3.7+ guarantees insertion order. Keys must be hashable.

```python
config = {"device": "nvme0", "timeout": 30, "retries": 3}

# Access patterns
config.get("missing", "default")   # safe get
config.setdefault("log_level", "INFO")  # insert only if absent

# Iteration
for key, val in config.items():    pass
for key in config:                 pass  # same as config.keys()

# Merge (Python 3.9+)
defaults = {"timeout": 30, "retries": 3}
overrides = {"timeout": 60, "verbose": True}
merged = defaults | overrides      # {"timeout":60,"retries":3,"verbose":True}
defaults |= overrides              # in-place

# dict comprehension
squares = {x: x**2 for x in range(10)}

# Deleting
val = config.pop("retries", None)  # safe pop
del config["device"]               # KeyError if absent
```

#### set

Unordered, no duplicates, O(1) lookup. Use for device/ID bookkeeping.

```python
active = {"nvme0", "nvme1", "nvme2"}
failed = {"nvme1"}

healthy  = active - failed          # difference
all_seen = active | {"nvme3"}       # union
common   = active & {"nvme0","nvme3"}  # intersection
xor      = active ^ failed          # symmetric difference

active.add("nvme3")
active.discard("nvme99")  # no KeyError if missing
"nvme0" in active         # O(1)

# frozenset: hashable set (usable as dict key)
device_combo = frozenset({"nvme0", "pcie0"})
```

#### defaultdict and Counter

```python
from collections import defaultdict, Counter

# Grouping test results by device
results_by_dev = defaultdict(list)
for dev, result in raw_results:
    results_by_dev[dev].append(result)

# Counting error codes from dmesg
errors = defaultdict(int)
for line in dmesg_lines:
    if "error" in line.lower():
        code = parse_error_code(line)
        errors[code] += 1

# Counter is a specialized subclass
error_counts = Counter(parse_error_code(l)
                       for l in dmesg_lines
                       if "error" in l.lower())
top5 = error_counts.most_common(5)
error_counts.update(more_lines)      # merge counts
```

#### deque

Double-ended queue, O(1) at both ends. Essential for sliding-window and ring-buffer patterns.

```python
from collections import deque
import time

# Ring buffer for live telemetry
class RingBuffer:
    def __init__(self, maxlen):
        self._buf = deque(maxlen=maxlen)

    def push(self, val):
        self._buf.append(val)   # old values auto-evicted

    def values(self):
        return list(self._buf)

# Sliding-window error burst detection
def find_error_bursts(log_lines, window_seconds=60, threshold=5):
    from datetime import datetime
    errors = deque()
    bursts = []
    for line in log_lines:
        if "[ERROR]" not in line:
            continue
        ts = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
        errors.append(ts)
        while errors and (ts - errors[0]).total_seconds() > window_seconds:
            errors.popleft()
        if len(errors) >= threshold:
            bursts.append((errors[0], len(errors)))
    return bursts
```

#### OrderedDict for LRU Cache

```python
from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity: int):
        self._cap = capacity
        self._cache = OrderedDict()

    def get(self, key):
        if key not in self._cache:
            return -1
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key, val):
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = val
        if len(self._cache) > self._cap:
            self._cache.popitem(last=False)  # evict oldest
```

---

### Control Flow

#### if / elif / else

```python
if speed == "16GT/s" and width == "x16":
    status = "PASS"
elif speed == "16GT/s":
    status = "WARN - degraded width"
else:
    status = "FAIL"
```

Python uses `and`, `or`, `not` (not `&&`, `||`, `!`). These are **short-circuit** operators: `and` stops at the first falsy operand, `or` stops at the first truthy operand, and they return the operand value (not necessarily a bool).

```python
x = a or "default"      # x is a if a is truthy, else "default"
y = a and a.strip()     # avoids calling .strip() on None
```

**Truthiness:** empty containers (`[]`, `{}`, `()`, `set()`, `""`), `0`, `0.0`, `None`, and `False` are falsy. Everything else is truthy. Test for emptiness Pythonically:

```python
if not devices:         # GOOD: empty list/dict/str is falsy
    ...
if len(devices) == 0:   # works but verbose
    ...
```

#### Ternary expression

```python
status = "PASS" if measured <= limit else "FAIL"
# Chaining is legal but hurts readability; prefer a lookup or a function:
# grade = "A" if s >= 90 else "B" if s >= 80 else "C"
```

#### for loops and the iteration helpers

```python
for device in devices:                          # iterate elements directly
    test(device)

for i, device in enumerate(devices):            # index + element
    print(f"{i}: {device}")

for i, device in enumerate(devices, start=1):   # 1-based index
    print(f"Test {i}: {device}")

for name, result in zip(names, results):        # parallel iteration (stops at shortest)
    print(f"{name}: {result}")

# range variants
for i in range(10): ...           # 0..9
for i in range(2, 10): ...        # 2..9
for i in range(0, 100, 5): ...    # 0, 5, 10, ..., 95
for i in range(10, 0, -1): ...    # 10, 9, ..., 1 (descending)
```

Prefer `enumerate` over manual index counters, and `zip` over indexing two lists in lockstep. Avoid `for i in range(len(xs))` unless you genuinely need the index.

#### for/else and while/else

The `else` block runs **only if the loop completed without hitting `break`**. This replaces the "found" flag pattern.

```python
for dev in devices:
    if dev.degraded:
        print(f"FAIL: {dev}")
        break
else:
    # Reached only if we NEVER broke out -> all devices passed
    print("All devices passed link check")
```

```python
retries = 0
while retries < max_retries:
    if connect():
        break
    retries += 1
    time.sleep(0.5 * (2 ** retries))   # exponential backoff
else:
    raise ConnectionError("Max retries exceeded")   # ran out of retries
```

#### break, continue, pass

- `break` exits the innermost loop immediately.
- `continue` skips to the next iteration.
- `pass` is a no-op placeholder (an empty body that is syntactically required).

#### Walrus operator := (Python 3.8+)

Assigns and returns a value in a single expression. Useful in `while` conditions and comprehensions to avoid computing something twice.

```python
# Read lines until EOF without a separate pre-read
while (line := f.readline()):
    process(line)

# Filter + transform without calling strip() twice
cleaned = [c for raw in data if (c := raw.strip())]
# Keeps only non-empty stripped lines, computing the strip exactly once.

# Reuse an expensive result inside the condition
if (n := len(devices)) > 8:
    print(f"Too many devices: {n}")
```

#### match / case (Python 3.10+): structural pattern matching

More than a `switch`: it destructures and binds.

```python
match command:
    case "start":
        start_test()
    case "pause":
        pause_test()
    case str(x) if x.startswith("set_"):   # guard clause with binding
        set_parameter(x[4:])
    case _:                                 # wildcard (default)
        print(f"Unknown command: {command}")

# Destructuring dicts and matching on values
match event:
    case {"type": "error", "code": code, "message": msg}:
        log_error(code, msg)
    case {"type": "result", "value": float(v)} if v > threshold:
        handle_high_value(v)
    case {"type": "result", "value": float(v)}:
        handle_normal(v)

# Destructuring sequences
match point:
    case (0, 0):
        print("origin")
    case (x, 0):
        print(f"on x-axis at {x}")
    case (x, y):
        print(f"at {x}, {y}")
```

---

### Comprehensions

List, dict, set comprehensions and generator expressions share the same syntax template: `[expr for item in iterable if condition]`. The `if` clause is a filter — it controls which items enter the loop. The conditional expression `val if cond else other` is different: it transforms the value for every item.

```python
# Filter: only passing items
passing_bdfs = [bdf for bdf, status in results.items() if status == "PASS"]

# Transform every item
gb_values = [bytes_val / 1e9 for bytes_val in byte_readings]

# Both: filter then transform
gb_values = [bytes_val / 1e9 for bytes_val in byte_readings
             if bytes_val is not None]

# Nested loops (order matches for-loop reading order)
combos = [(dev, speed)
          for dev in devices
          for speed in ["GEN3", "GEN4", "GEN5"]]

# Dict and set comprehensions
error_map = {dev: count for dev, count in errors.items() if count > 0}
seen_codes = {parse_code(l) for l in log_lines}

# Generator expression — lazy, no list allocated
total = sum(v**2 for v in readings if not math.isnan(v))
```

Avoid deeply nested comprehensions (more than two `for` clauses) — a regular loop is clearer and easier to debug.

---

### Functions

#### LEGB Scope and Closures

```python
GLOBAL_TIMEOUT = 30       # module-level global

def make_threshold_checker(threshold):
    """Closure: threshold is captured in the enclosing scope."""
    def check(value):
        return value >= threshold   # 'threshold' from enclosing scope
    return check

is_hot = make_threshold_checker(85.0)
is_hot(90.0)   # True

# nonlocal to mutate enclosing variable
def make_counter():
    count = 0
    def inc():
        nonlocal count
        count += 1
        return count
    return inc
```

#### Argument Forms

```python
def send_command(
    port: str,              # positional-or-keyword
    cmd: bytes,             # positional-or-keyword
    /,                      # everything left of / is positional-only
    timeout: float = 1.0,   # keyword-or-positional with default
    *,                      # everything right of * is keyword-only
    retries: int = 3,
    verbose: bool = False,
) -> bytes:
    ...
```

**Mutable default trap** — the default is evaluated once at definition time:

```python
# WRONG
def log_event(msg, history=[]):
    history.append(msg)    # all callers share the same list

# CORRECT: sentinel pattern
_SENTINEL = object()
def log_event(msg, history=_SENTINEL):
    if history is _SENTINEL:
        history = []
    history.append(msg)
    return history
```

**Late-binding in lambdas**:

```python
# WRONG: all lambdas capture the same 'i' variable
fns = [lambda: i for i in range(5)]
fns[0]()   # 4, not 0

# CORRECT: bind at definition via default argument
fns = [lambda i=i: i for i in range(5)]
fns[0]()   # 0
```

```python
# *args and **kwargs
def run_all(*test_fns, **options):
    for fn in test_fns:
        fn(**options)

# Unpacking into call
args = ("nvme0", b"identify")
kwargs = {"timeout": 2.0, "retries": 1}
send_command(*args, **kwargs)
```

---

### Decorators

A decorator is a callable that takes a callable and returns a callable. `functools.wraps` preserves the wrapped function's metadata (name, docstring, annotations) — essential for pytest fixtures and logging.

```python
import functools, time, logging

log = logging.getLogger(__name__)

def timed(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        log.debug("%s took %.3fs", fn.__name__, time.perf_counter() - t0)
        return result
    return wrapper

@timed
def read_temperature(device: str) -> float:
    ...
```

#### Decorator Factory (Three-Layer Pattern)

When a decorator needs its own arguments, add an outer function that captures them and returns the actual decorator.

```python
def retry(times=3, delay=0.5, exceptions=(Exception,)):
    """Decorator factory: @retry(times=5, delay=1.0)"""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(times):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    log.warning("%s attempt %d/%d failed: %s",
                                fn.__name__, attempt + 1, times, exc)
                    time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator

@retry(times=5, delay=0.1, exceptions=(IOError, TimeoutError))
def read_register(addr: int) -> int:
    ...
```

#### Class-Based Decorator (Stateful)

```python
class CallCount:
    """Count how many times a function is called."""
    def __init__(self, fn):
        functools.update_wrapper(self, fn)
        self._fn = fn
        self.count = 0

    def __call__(self, *args, **kwargs):
        self.count += 1
        return self._fn(*args, **kwargs)

@CallCount
def poll_status(): ...

poll_status()
print(poll_status.count)   # 1
```

#### Stacking Order

Decorators apply bottom-up; the top decorator is the outermost wrapper.

```python
@timed        # outermost: runs first/last
@retry(3)     # middle
@validate     # innermost: runs first on the actual call
def measure(): ...
# equivalent to: timed(retry(3)(validate(measure)))
```

---

### Dataclasses, Typing, Enum

#### Dataclasses

```python
from dataclasses import dataclass, field, asdict, astuple
from typing import Optional

@dataclass
class TestConfig:
    device: str
    iterations: int = 100
    timeout_s: float = 30.0
    tags: list[str] = field(default_factory=list)   # never use [] as default
    notes: Optional[str] = None

    def __post_init__(self):
        if self.iterations <= 0:
            raise ValueError(f"iterations must be > 0, got {self.iterations}")

cfg = TestConfig("nvme0", iterations=500, tags=["perf", "regression"])
d = asdict(cfg)   # -> dict for JSON serialization

@dataclass(frozen=True, order=True)
class SpecLimit:
    """Immutable, hashable, comparable spec limit."""
    parameter: str
    low: float
    high: float

    def check(self, value: float) -> bool:
        return self.low <= value <= self.high
```

`frozen=True` makes the instance hashable (usable as dict key or set member). `order=True` generates `<`/`<=`/`>`/`>=` based on field order.

#### Type Hints and typing Module

```python
from typing import (
    Optional, Union, Any,
    Callable, TypeVar,
    Sequence, Mapping,
)
from collections.abc import Iterator, Generator

T = TypeVar("T")

def first_passing(
    items: Sequence[T],
    pred: Callable[[T], bool],
) -> Optional[T]:
    return next((x for x in items if pred(x)), None)

# Python 3.10+ union shorthand
def process(value: int | float | None) -> str:
    ...

# TypedDict for structured dicts (e.g., JSON payloads)
from typing import TypedDict

class NVMeDevice(TypedDict):
    DevicePath: str
    ModelNumber: str
    Firmware: str
    SectorSize: int
```

Type hints are not enforced at runtime by default — use `mypy` or `pyright` for static analysis. At Zoox, `Optional[X]` means `Union[X, None]`.

#### Enum

```python
from enum import Enum, IntFlag, auto

class TestResult(Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"

    def is_terminal(self) -> bool:
        return self in (TestResult.PASS, TestResult.FAIL)

# Membership lookup by value (safe)
result = TestResult("pass")   # TestResult.PASS

class ErrorFlags(IntFlag):
    NONE        = 0
    LINK_DOWN   = auto()   # 1
    CRC_ERROR   = auto()   # 2
    TIMEOUT     = auto()   # 4
    POWER_FAULT = auto()   # 8

flags = ErrorFlags.LINK_DOWN | ErrorFlags.CRC_ERROR  # 3
if ErrorFlags.CRC_ERROR in flags:
    ...
```

`IntFlag` members support bitwise operations and integer comparison — ideal for hardware status register decoding.

---

### Context Managers

`__enter__` runs on `with` entry; `__exit__` runs on exit whether or not an exception occurred. The three arguments to `__exit__` are the exception type, value, and traceback; returning a truthy value suppresses the exception.

```python
class InstrumentSession:
    def __init__(self, resource_string: str):
        self._addr = resource_string
        self._inst = None

    def __enter__(self):
        import pyvisa
        rm = pyvisa.ResourceManager()
        self._inst = rm.open_resource(self._addr)
        self._inst.timeout = 5000
        return self._inst

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._inst:
            self._inst.close()
        return False   # don't suppress exceptions

with InstrumentSession("TCPIP::192.168.1.10::INSTR") as inst:
    inst.write("*RST")
    voltage = float(inst.query("MEAS:VOLT? DC"))
```

#### @contextmanager

```python
from contextlib import contextmanager, suppress, ExitStack

@contextmanager
def exclusive_device(path: str):
    import fcntl
    fd = open(path, "rb")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()

# suppress: swallow specific exceptions
with suppress(FileNotFoundError):
    Path("/tmp/stale.lock").unlink()

# ExitStack: dynamic number of context managers
def open_all_devices(paths):
    with ExitStack() as stack:
        handles = [stack.enter_context(open(p, "rb")) for p in paths]
        return process_all(handles)
        # all handles closed even if process_all raises
```

---

### Generators and itertools

A generator function contains `yield`. Calling it returns a generator object; execution is suspended at each `yield` and resumed on the next `next()` call. Generator objects implement the iterator protocol (`__iter__` returns self, `__next__` advances).

```python
def poll_temperature(device: str, interval_s: float = 1.0):
    """Infinite generator: yields (timestamp, temp_C) until stopped."""
    import time
    while True:
        temp = read_device_temp(device)
        yield time.monotonic(), temp
        time.sleep(interval_s)

# Consume with for loop + break condition
for ts, temp in poll_temperature("gpu0"):
    record(ts, temp)
    if temp > 95.0:
        trigger_thermal_alert()
        break

# yield from: delegate to sub-generator
def chain_logs(*paths):
    for path in paths:
        yield from open(path)   # yields lines from each file in sequence

# Generator expression vs list comprehension
# Generator: lazy, no memory allocation
total = sum(float(line.split()[-1]) for line in open("data.csv"))
# List: eager, all values in memory -- only when you need random access
```

#### itertools

```python
import itertools

# chain: flatten iterables without loading all into memory
all_lines = itertools.chain(file1, file2, file3)

# groupby: group consecutive equal-key items -- MUST be pre-sorted
data = sorted(results, key=lambda r: r.device)
for device, group in itertools.groupby(data, key=lambda r: r.device):
    device_results = list(group)

# product: Cartesian product for parametric test matrices
devices = ["nvme0", "nvme1"]
speeds  = ["GEN3", "GEN4"]
qd_values = [1, 4, 16, 32]
test_cases = list(itertools.product(devices, speeds, qd_values))
# [("nvme0","GEN3",1), ("nvme0","GEN3",4), ...]

# islice: lazy slice of any iterator
first_100_errors = list(itertools.islice(
    (l for l in log_lines if "ERROR" in l), 100
))

# zip_longest: pair sequences of unequal length
from itertools import zip_longest
for a, b in zip_longest(baseline, current, fillvalue=None):
    compare(a, b)

# accumulate: running totals
from itertools import accumulate
import operator
cumulative_bytes = list(accumulate(transfer_sizes))
running_max = list(accumulate(readings, func=max))
```

---

### Error Handling

#### Exception Hierarchy Best Practices

```python
# Most specific first -- Python checks handlers top-down
try:
    result = read_register(addr)
except TimeoutError:
    log.error("register read timed out: addr=0x%X", addr)
    raise
except IOError as exc:
    log.error("IO error reading addr=0x%X: %s", addr, exc)
    return None
except Exception:
    log.exception("unexpected error reading addr=0x%X", addr)
    raise

# Bare raise re-raises the current exception without wrapping
try:
    connect()
except ConnectionError:
    cleanup()
    raise             # preserves original traceback

# Exception chaining
try:
    raw = serial_read()
except serial.SerialTimeoutException as exc:
    raise InstrumentError("read timed out") from exc

# Swallow-and-log (rare in test code -- prefer letting failures propagate)
try:
    cache.invalidate()
except Exception:
    log.warning("cache invalidate failed", exc_info=True)
```

#### Custom Exception Hierarchy

```python
class TestInfraError(Exception):
    """Base for all test infrastructure errors."""

class InstrumentError(TestInfraError):
    def __init__(self, msg, resource=None):
        super().__init__(msg)
        self.resource = resource

class DeviceNotFoundError(TestInfraError):
    pass

class SpecViolation(TestInfraError):
    def __init__(self, param, value, limit):
        super().__init__(
            f"{param}={value} violates limit {limit}"
        )
        self.param, self.value, self.limit = param, value, limit
```

Easier to Ask Forgiveness than Permission (EAFP) is idiomatic Python: try the operation, handle the exception. Look Before You Leap (LBYL) uses guard checks before attempting the operation. EAFP is preferred when the operation is likely to succeed; LBYL is fine for configuration validation at startup.

---

### Regular Expressions — the `re` Module

Parsing tool output (`lspci`, `dmesg`, `nvme`, `ethtool`, instrument replies) is most of the string work in test automation, and `re` is the workhorse. Always write patterns as **raw strings** (`r"..."`) so backslashes reach the regex engine untouched — `r"\d"`, never `"\\d"`.

#### Module functions vs. compiled patterns

| Call | Returns | Use for |
|------|---------|---------|
| `re.search(p, s)` | first `Match` anywhere, else `None` | "is it in there / pull one field" |
| `re.match(p, s)` | `Match` only if `s` *starts* with `p` | anchored-at-start check |
| `re.fullmatch(p, s)` | `Match` only if `p` matches *all* of `s` | strict validation |
| `re.findall(p, s)` | `list` of strings (tuples if >1 group) | collect every hit |
| `re.finditer(p, s)` | iterator of `Match` (with positions) | every hit, lazily, with spans |
| `re.sub(p, repl, s)` | new string | replace / redact / normalize |
| `re.subn(p, repl, s)` | `(new_string, n)` | replace + count |
| `re.split(p, s)` | `list` | split on a pattern |
| `re.escape(s)` | string | treat data/user text as a literal |

`re.compile(pattern, flags)` once and reuse the object in loops — clearer, and it attaches flags to the pattern. The module-level convenience functions (`re.search`, `re.match`, etc.) do cache compiled patterns internally (up to 512 unique patterns in CPython's dict-based cache), but explicit `compile()` at module level is still preferred: it makes the pattern's scope and flags obvious, avoids re-hashing strings on every call, and keeps patterns testable in isolation. The single biggest gotcha: **`match` is anchored at the start of the string, `search` is not.** Reach for `search` unless you specifically mean "starts with."

```python
import re
pat = re.compile(r"count=(\d+)")
pat.search("nvme0: reset count=7")   # <re.Match...>, .group(1) == "7"
pat.match("nvme0: reset count=7")    # None -- string does not START with count=
```

#### The Match object

```python
m = re.search(r"(?P<dev>\w+):.*count=(?P<n>\d+)", "nvme0: reset count=7")
m.group(0)                  # whole match: 'nvme0: reset count=7'
m.group(1), m.group("dev")  # group 2 is '7'; 'nvme0' by name
m.groups()                  # ('nvme0', '7')
m.groupdict()               # {'dev': 'nvme0', 'n': '7'}
m.span(2)                   # (start, end) indices of group 2
m["dev"]                    # subscript form (3.6+)

# Walrus folds the match-test and the capture into one line:
if (m := pat.search(line)):
    handle(int(m.group(1)))
```

#### Pattern syntax you reach for

```text
Classes    \d \D digit/non   \w \W word/non   \s \S space/non   .  any (not \n)
           [abc] set    [^abc] negated    [a-f0-9] ranges
Anchors    ^ start   $ end   \b word-boundary   \B non-boundary   \A \Z string start/end
Quantify   * 0+   + 1+   ? 0/1   {m} exactly   {m,n} range    (append ? for LAZY: *? +? ??)
Groups     (...) capture   (?:...) non-capture   (?P<name>...) named   \1 / (?P=name) backref
Alternate  a|b
Lookahead  (?=...) followed-by      (?!...) not-followed-by
Lookbehind (?<=...) preceded-by     (?<!...) not-preceded-by    (fixed-width only)
```

Flags, as a constructor arg or inline: `re.I` ignorecase, `re.M` multiline (`^`/`$` match each line), `re.S` dotall (`.` also matches `\n`), `re.X` verbose, `re.A` ASCII-only `\w\d\s`. Combine with `|` (`re.I | re.M`); set inline at the front `(?im)` or scoped `(?i:abc)` (3.6+).

#### Greedy vs. lazy (the classic bug)

```python
xml = "<tag>value</tag>"
re.findall(r"<.*>",  xml)   # ['<tag>value</tag>']  greedy: longest
re.findall(r"<.*?>", xml)   # ['<tag>', '</tag>']   lazy:   shortest
```

`*`, `+`, `{m,n}` are greedy by default; append `?` for lazy. (3.11+ also has possessive `*+`/`++` and atomic groups `(?>...)` that never give matches back — see pitfalls.)

#### Substitution — string, backreference, or callable

```python
re.sub(r"\s+", " ", raw).strip()                      # collapse runs of whitespace
re.sub(r"(\d{4})-(\d{2})-(\d{2})", r"\3/\2/\1", s)     # reorder via backrefs (\g<1> also works)

# The replacement can be a FUNCTION -- compute each replacement:
def redact(m): return m.group(0)[:4] + "..."
safe = re.sub(r"SN[0-9A-F]{8}", redact, log)          # mask serials in a shared log
text, n = re.subn(r"\bFAIL\b", "PASS", report)        # also returns how many it changed
```

#### Splitting that keeps the delimiters

```python
re.split(r"[,\t|]+", line)          # split on any run of comma / tab / pipe
re.split(r"(\d+)", "ch12blk3")       # ['ch', '12', 'blk', '3'] -- a capture group keeps the splitters
```

#### Performance and pitfalls

- **Compile patterns used in hot loops** (parsing thousands of dmesg lines per unit).
- **Catastrophic backtracking:** nested quantifiers over overlapping classes — e.g. `(a+)+$` against `"aaaa…!"` — are exponential and can wedge a test station. Fix by being specific, anchoring, or using atomic groups `(?>...)` / possessive `++` (3.11+).
- **`.` excludes newline** unless `re.S`; **`^`/`$` are whole-string** unless `re.M`.
- **`re.X` (verbose) strips unescaped whitespace** in the pattern — escape real spaces as `\ ` or put them in `[ ]`, or a "prettified" pattern silently stops matching. (This is a common self-inflicted bug.)
- **Don't regex structured formats.** Parse JSON/CSV with `json`/`csv`, `key=value` with `str.split`, nested grammars with a real parser. Regex is for flat, line-oriented text.

#### Worked examples — parsing the tools you live in

```python
import re

# 1) A dmesg / log line -> named fields.
#    NOTE: no re.X here -- the spaces between fields are LITERAL and must match.
LOG = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    r"\[(?P<level>\w+)\] "
    r"(?P<dev>\S+): "
    r"(?P<msg>.*)"
)
m = LOG.search("2024-01-15 08:43:12 [ERROR] nvme0: link reset count=7")
m.groupdict()   # {'ts': '...', 'level': 'ERROR', 'dev': 'nvme0', 'msg': 'link reset count=7'}

# 2) PCIe LnkSta / LnkCap out of `lspci -vvv`
LNKSTA = re.compile(r"LnkSta:\s+Speed\s+(?P<speed>\S+),\s+Width\s+x(?P<width>\d+)")
def parse_lnksta(vvv):
    m = LNKSTA.search(vvv)
    return {"speed": m["speed"], "width": int(m["width"])} if m else {}

# 3) A BDF: domain:bus:device.function (validate + capture)
BDF = re.compile(r"^(?P<dom>[0-9a-f]{4}):(?P<bus>[0-9a-f]{2}):(?P<dev>[0-9a-f]{2})\.(?P<fn>\d)$")

# 4) key=value pairs -> dict (SMART / sysfs style dumps)
kv = dict(re.findall(r"(\w+)=(\S+)", "temp=41 spare=100 used=2"))   # {'temp':'41', ...}

# 5) Verbose mode done RIGHT: comments allowed, literal spaces escaped as '\ '
AER = re.compile(r"""
    (?P<dev>\S+)\          # device id, then a literal space
    AER:\                  # the 'AER:' tag
    (?P<kind>Corrected|Uncorrected)\ error
    """, re.VERBOSE)
```

#### Quick token reference

| Token | Meaning | Token | Meaning |
|---|---|---|---|
| `\d \w \s` | digit / word / space | `* + ?` | 0+, 1+, 0-or-1 |
| `\D \W \S` | negated classes | `{m,n}` | m to n times |
| `.` | any char but newline | `*? +?` | lazy quantifiers |
| `^ $` | line start / end | `(...)` | capture group |
| `\b \B` | word boundary / not | `(?:...)` | non-capturing |
| `[...] [^...]` | set / negated set | `(?P<n>...)` | named group |
| `\A \Z` | string start / end | `(?=...) (?!...)` | look-ahead +/- |

Use named groups (`(?P<name>...)`) whenever you index by meaning — the parse becomes self-documenting and survives reordering.

---

### File I/O, CSV, JSON, YAML

#### Path Operations

```python
from pathlib import Path

results_dir = Path("/home/tester/results")
run_dir     = results_dir / "run_2024-01-15"
run_dir.mkdir(parents=True, exist_ok=True)

log_file = run_dir / "nvme_stress.log"
log_file.write_text("run started\n", encoding="utf-8")
content = log_file.read_text()

# glob and rglob
csv_files = list(results_dir.rglob("*.csv"))
latest    = max(csv_files, key=lambda p: p.stat().st_mtime)

# Atomic write: write temp file, then rename (os.replace is atomic on POSIX)
import os, tempfile

def atomic_write(path: Path, content: str):
    dir_ = path.parent
    with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False,
                                     suffix=".tmp") as f:
        f.write(content)
        tmp = Path(f.name)
    os.replace(tmp, path)
```

#### CSV

```python
import csv

# Write results
fieldnames = ["device", "iteration", "bandwidth_GBps", "latency_us", "result"]
with open(run_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for row in test_results:
        writer.writerow(row)

# `newline=""` is required on Windows; harmless on POSIX

# Read and process
with open(run_dir / "results.csv", newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = [row for row in reader]
    failures = [r for r in rows if r["result"] == "FAIL"]
```

#### JSON

```python
import json

# Serialize test record
record = {
    "run_id": "2024-01-15T08:43:00",
    "device": "nvme0",
    "metrics": {"bw_GBps": 6.8, "iops_k": 750},
    "passed": True,
}
with open(run_dir / "record.json", "w") as f:
    json.dump(record, f, indent=2)

# Deserialize
with open(run_dir / "record.json") as f:
    data = json.load(f)

# String variants
s = json.dumps(record)
data = json.loads(s)

# Custom serializer for non-serializable types
class TestEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Path):
            return str(obj)
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return super().default(obj)

json.dumps(record, cls=TestEncoder)
```

#### YAML (PyYAML)

YAML is the standard format for station configuration files at Zoox — more human-editable than JSON, supports comments.

```python
import yaml

# Station config (config/station.yaml):
# station_id: rack-3-slot-2
# devices:
#   - type: nvme
#     path: /dev/nvme0
#     expected_speed: "16 GT/s"
#   - type: pcie
#     bdf: "0000:03:00.0"
# timeouts:
#   connect: 5
#   read:    2

def load_station_config(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)   # ALWAYS safe_load; yaml.load() is unsafe

def save_station_config(config: dict, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

# Usage
cfg = load_station_config("/etc/zoox/station.yaml")
for dev in cfg["devices"]:
    print(dev["type"], dev.get("bdf", dev.get("path")))
```

`yaml.safe_load` only constructs basic Python objects (dict, list, str, int, float, bool, None). `yaml.load(f, Loader=yaml.FullLoader)` allows arbitrary Python objects — use only when you control the YAML source.

---

### subprocess — Wrapping CLI Tools

#### Core Patterns

```python
import subprocess
from pathlib import Path

def run_tool(
    args: list[str],
    timeout: float = 15,
    input_data: str | None = None,
) -> tuple[int, str, str]:
    """Run a CLI tool; return (returncode, stdout, stderr). Never raises on nonzero exit."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            input=input_data,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"command not found: {args[0]}"
    except subprocess.TimeoutExpired as exc:
        # subprocess.run() kills the child and re-raises; exc.stdout/stderr hold
        # any partial output captured before the timeout (bytes if text=False,
        # str if text=True -- may be None if nothing was flushed yet).
        partial_out = (exc.stdout or "").strip()
        return 124, partial_out, f"timed out after {timeout}s: {' '.join(str(a) for a in args)}"

# ALWAYS pass args as a list -- never use shell=True with untrusted input
rc, out, err = run_tool(["lspci", "-s", bdf, "-vvv"])
if rc != 0:
    raise RuntimeError(f"lspci failed: {err}")
```

Shell quoting bugs and injection are the main hazards of `shell=True`. Use a list; let subprocess handle escaping.

#### lspci Parsing

```python
LNKCAP_PAT = re.compile(
    r"LnkCap:.*?Speed\s+(?P<cap_speed>\S+),\s+Width\s+x(?P<cap_width>\d+)"
)
LNKSTA_PAT = re.compile(
    r"LnkSta:.*?Speed\s+(?P<cur_speed>\S+),\s+Width\s+x(?P<cur_width>\d+)"
)

def get_pcie_link_info(bdf: str) -> dict:
    rc, out, err = run_tool(["lspci", "-s", bdf, "-vvv"])
    if rc != 0:
        raise RuntimeError(f"lspci -s {bdf} -vvv: {err.strip()}")

    cap = LNKCAP_PAT.search(out)
    sta = LNKSTA_PAT.search(out)
    return {
        "cap_speed":  cap.group("cap_speed")  if cap else None,
        "cap_width":  int(cap.group("cap_width")) if cap else None,
        "cur_speed":  sta.group("cur_speed")  if sta else None,
        "cur_width":  int(sta.group("cur_width")) if sta else None,
    }
```

#### PCIe Link Status via sysfs (no lspci process overhead)

```python
def read_link_status(bdf: str) -> dict:
    base = Path(f"/sys/bus/pci/devices/{bdf}")
    if not base.exists():
        raise FileNotFoundError(f"No PCI device at {bdf}")
    return {
        "current_speed": (base / "current_link_speed").read_text().strip(),
        "max_speed":     (base / "max_link_speed").read_text().strip(),
        "current_width": (base / "current_link_width").read_text().strip(),
        "max_width":     (base / "max_link_width").read_text().strip(),
    }
```

#### NVMe — nvme-cli

```python
import json as _json

def nvme_list() -> list[dict]:
    rc, out, _ = run_tool(["nvme", "list", "-o", "json"])
    if rc != 0:
        return []
    data = _json.loads(out)
    # nvme-cli < 2.11: top-level "Devices" is a flat list of controller dicts.
    # nvme-cli >= 2.11: output restructured to {"Devices": [{"Subsystems": [...]}]}.
    # Probe for both layouts so the caller gets a consistent flat list.
    devices = data.get("Devices", [])
    if devices and "Subsystems" in devices[0]:
        flat = []
        for sub in devices:
            for ctrl in sub.get("Subsystems", []):
                flat.extend(ctrl.get("Controllers", []))
        return flat
    return devices

def nvme_smart_log(device: str) -> dict:
    rc, out, err = run_tool(["nvme", "smart-log", device, "-o", "json"])
    if rc != 0:
        raise RuntimeError(f"nvme smart-log {device}: {err.strip()}")
    return _json.loads(out)

def nvme_id_ctrl(device: str) -> dict:
    rc, out, _ = run_tool(["nvme", "id-ctrl", device, "-o", "json"])
    return _json.loads(out) if rc == 0 else {}
```

#### dmesg and ethtool

```python
def get_dmesg_errors(keyword: str = "nvme", since_boot: bool = True) -> list[str]:
    args = ["dmesg", "--level=err,warn"]
    if since_boot:
        args.append("-T")   # human-readable timestamps
    rc, out, _ = run_tool(args, timeout=5)
    return [l for l in out.splitlines() if keyword in l.lower()]

# Live-streaming dmesg (Popen for continuous output)
def stream_dmesg(callback, keyword=None):
    with subprocess.Popen(
        ["dmesg", "--follow", "-T"],
        stdout=subprocess.PIPE,
        text=True,
    ) as proc:
        for line in proc.stdout:
            if keyword is None or keyword in line:
                callback(line.rstrip())

def get_ethtool_stats(iface: str) -> dict[str, int]:
    rc, out, _ = run_tool(["ethtool", "-S", iface])
    stats = {}
    for line in out.splitlines():
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            try:
                stats[key.strip()] = int(val.strip())
            except ValueError:
                pass
    return stats
```

#### Privileged Commands (sudo without a TTY)

Many compute-platform tools (`nvme format`, `setpci`, `rescan`) require root. In a non-interactive test runner there is no TTY, so `sudo -S` reads the password from stdin. The better option for automated test stations is a targeted `sudoers` entry so no password is required at all.

```python
import subprocess, os

def run_privileged(args: list[str], timeout: float = 30) -> tuple[int, str, str]:
    """Run a command under sudo, no TTY required.

    On a test station, configure /etc/sudoers.d/test-runner with:
        tester ALL=(ALL) NOPASSWD: /usr/sbin/nvme, /usr/sbin/setpci
    Then sudo will not prompt for a password and this wrapper is straightforward.
    """
    cmd = ["sudo", "--non-interactive"] + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or ""), f"timed out: {' '.join(args)}"
    except FileNotFoundError:
        return 127, "", "sudo not found"
```

`--non-interactive` (equivalent to `-n`) makes sudo fail immediately with a clear error instead of hanging waiting for a TTY password prompt — critical when running inside a Continuous Integration (CI) job or a test daemon.

#### Environment Isolation for Subprocess Calls

`subprocess.run` inherits the parent's environment by default. Passing an explicit `env=` dict prevents ambient variables (proxy settings, `LD_PRELOAD`, debug flags) from poisoning tool output.

```python
import os

def clean_env(**extra: str) -> dict[str, str]:
    """Return a minimal environment for subprocess calls.

    Keeps PATH, HOME, USER, TERM; discards proxy, debug, and LD_* variables.
    Pass extra={"NVME_DEBUG": "1"} to inject variables for one call.
    """
    base_keys = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM",
                 "LANG", "LC_ALL", "DBUS_SESSION_BUS_ADDRESS"}
    env = {k: v for k, v in os.environ.items() if k in base_keys}
    env.update(extra)
    return env

rc, out, err = subprocess.run(
    ["nvme", "smart-log", "/dev/nvme0", "-o", "json"],
    capture_output=True, text=True,
    env=clean_env(),
).returncode, ..., ...   # unpack as normal
```

#### Popen for Long-Running Background Processes

`subprocess.run` blocks until the process exits. Use `Popen` when you need to start something, do other work concurrently, then wait — or when you need to send stdin incrementally.

```python
import subprocess, signal, time

class BackgroundProcess:
    """Start a long-running subprocess, stream its stdout, stop it cleanly."""

    def __init__(self, args: list[str]):
        self._args = args
        self._proc: subprocess.Popen | None = None

    def start(self):
        self._proc = subprocess.Popen(
            self._args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def stop(self, timeout: float = 5.0):
        if not self._proc:
            return
        self._proc.send_signal(signal.SIGINT)
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()

    def read_lines(self):
        """Yield lines from stdout as they arrive (blocks between lines)."""
        for line in self._proc.stdout:
            yield line.rstrip()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

# Capture a fio run while the test loop does other work
with BackgroundProcess(["fio", "--filename=/dev/nvme0", "--rw=read",
                        "--name=read_bw", "--output-format=json"]) as fio:
    time.sleep(10)   # let it run
# stdout/stderr are still accessible via fio._proc after __exit__
```

---

### pytest

pytest is the standard test runner. Key concepts: fixtures for setup/teardown, parametrize for data-driven tests, monkeypatch for dependency substitution, and `conftest.py` for shared fixtures.

#### Fixtures

```python
# conftest.py (shared across a test directory tree)
import pytest
from pathlib import Path

@pytest.fixture(scope="session")
def station_config():
    """Load station config once per session."""
    return load_station_config(Path("config/station.yaml"))

@pytest.fixture(scope="module")
def nvme_device(station_config):
    """Return path to the primary NVMe device under test."""
    devs = [d for d in station_config["devices"] if d["type"] == "nvme"]
    if not devs:
        pytest.skip("no NVMe device in station config")
    return devs[0]["path"]

@pytest.fixture
def fresh_results_dir(tmp_path):
    """Per-test temp directory pre-populated with a results subdir."""
    d = tmp_path / "results"
    d.mkdir()
    return d

# Yield fixture: teardown after yield
@pytest.fixture
def serial_port(station_config):
    import serial
    port_cfg = station_config.get("serial_port", {})
    ser = serial.Serial(
        port=port_cfg["path"],
        baudrate=port_cfg.get("baud", 115200),
        timeout=port_cfg.get("timeout", 1),
    )
    yield ser
    ser.close()
```

Fixture scopes: `function` (default), `class`, `module`, `session`. Higher scopes reduce setup overhead but require fixtures to be safe for reuse. Use `yield` for teardown.

#### parametrize

```python
import pytest

GEN_SPEEDS = ["GEN3", "GEN4", "GEN5"]
QUEUE_DEPTHS = [1, 4, 16, 32]

@pytest.mark.parametrize("speed", GEN_SPEEDS)
@pytest.mark.parametrize("qd", QUEUE_DEPTHS)
def test_nvme_throughput(nvme_device, speed, qd):
    # Cartesian: 3 speeds * 4 qd = 12 test cases
    bw = measure_bandwidth(nvme_device, speed=speed, queue_depth=qd)
    assert bw >= 3.0, f"BW {bw:.2f} GBps below limit at {speed} qd={qd}"

@pytest.mark.parametrize("cmd,expected", [
    (b"*IDN?\n", "Zoox"),
    (b"MEAS:VOLT?\n", None),   # None = don't check value
    (b"SYST:ERR?\n", "+0,"),
])
def test_instrument_commands(serial_port, cmd, expected):
    serial_port.write(cmd)
    resp = serial_port.readline().decode().strip()
    if expected is not None:
        assert expected in resp

# pytest.approx for floating-point
def test_voltage_within_spec(measured_voltage):
    assert measured_voltage == pytest.approx(3.3, abs=0.05)
```

#### Mocking Hardware

```python
from unittest.mock import MagicMock, patch, call

# patch WHERE IT IS USED, not where it is defined
@patch("mymodule.tests.subprocess.run")
def test_lspci_parse(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=FAKE_LSPCI_OUTPUT,
        stderr="",
    )
    info = get_pcie_link_info("0000:03:00.0")
    assert info["cur_speed"] == "16 GT/s"
    assert info["cur_width"] == 16

# side_effect: raise on N-th call, succeed on others
call_count = 0
def flaky_read(*args, **kwargs):
    global call_count
    call_count += 1
    if call_count < 3:
        raise IOError("transient error")
    return MagicMock(returncode=0, stdout="ok", stderr="")

@patch("mymodule.subprocess.run", side_effect=flaky_read)
def test_retry_behavior(mock_run):
    rc, out, _ = run_tool(["some_cmd"])
    assert mock_run.call_count == 3
    assert out == "ok"

# monkeypatch: cleaner for simple attribute/env substitution
def test_with_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOOX_RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr("mymodule.DEFAULT_TIMEOUT", 0.1)
    # monkeypatch reverts all changes after the test automatically
```

#### Fixture Parametrization

Parametrizing a fixture lets you run every test that depends on it against multiple values without touching the test itself — the right approach when the variation is about the resource (which device), not the test logic.

```python
# conftest.py
import pytest

@pytest.fixture(params=["nvme0", "nvme1"])
def nvme_dev(request):
    """Parametric fixture: each dependent test runs twice, once per device."""
    return f"/dev/{request.param}"

# test_nvme.py
def test_identify(nvme_dev):
    # runs as test_identify[nvme0] and test_identify[nvme1]
    rc, out, _ = run_tool(["nvme", "id-ctrl", nvme_dev, "-o", "json"])
    assert rc == 0
```

Use `pytest.fixture(params=...)` when the parameter is the *resource*. Use `@pytest.mark.parametrize` when the parameter is *test data* (inputs, thresholds, expected values). Mixing both is fine — you get the Cartesian product.

#### Accessing the `request` Fixture

The built-in `request` fixture exposes test context. The most common uses in hardware test code:

```python
@pytest.fixture
def result_log(request, tmp_path):
    """Named log file per test, in a shared tmp directory."""
    log_file = tmp_path / f"{request.node.name}.log"
    return log_file

@pytest.fixture(scope="session")
def shared_results_dir(request, tmp_path_factory):
    """Session-scoped temp dir (tmp_path is only function-scoped)."""
    return tmp_path_factory.mktemp("results")

@pytest.fixture
def device_under_test(request):
    """Read device path from command-line option, skip if not given."""
    dev = request.config.getoption("--device", default=None)
    if dev is None:
        pytest.skip("--device not specified")
    return dev
```

`tmp_path` is function-scoped (new dir per test). `tmp_path_factory` is session-scoped — use it inside session or module fixtures when you need a shared directory that persists across tests in the run.

#### Mocking subprocess at the Fixture Level

Mocking `subprocess.run` inline in every test is noisy. Promote it to a fixture so all tests in a module share the same mock setup cleanly:

```python
# conftest.py
import pytest
from unittest.mock import MagicMock, patch

FAKE_LSPCI = """\
0000:03:00.0 Non-Volatile memory controller: ...
        LnkCap: Port #0, Speed 32GT/s, Width x4
        LnkSta: Speed 32GT/s, Width x4
"""

@pytest.fixture
def mock_lspci(monkeypatch):
    """Replace subprocess.run with a canned lspci response."""
    fake = MagicMock(returncode=0, stdout=FAKE_LSPCI, stderr="")
    monkeypatch.setattr("mymodule.parsers.lspci.subprocess.run",
                        lambda *a, **kw: fake)
    return fake

def test_link_speed_parsed(mock_lspci):
    info = get_pcie_link_info("0000:03:00.0")
    assert info["cur_speed"] == "32GT/s"
    assert info["cur_width"] == 4
```

Prefer `monkeypatch.setattr` over `@patch` inside fixtures — `monkeypatch` is pytest-native, automatically reverts after the test, and doesn't require the `with patch(...)` boilerplate.

#### conftest.py Patterns

```python
# Markers for slow/hardware tests
# conftest.py at project root:
import pytest

def pytest_addoption(parser):
    parser.addoption("--run-hardware", action="store_true",
                     help="Run tests that require physical hardware")

def pytest_configure(config):
    config.addinivalue_line("markers",
        "hardware: requires physical hardware (--run-hardware)")
    config.addinivalue_line("markers",
        "slow: slow integration test")

def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-hardware"):
        skip = pytest.mark.skip(reason="needs --run-hardware")
        for item in items:
            if "hardware" in item.keywords:
                item.add_marker(skip)
```

```python
# pytest.ini / pyproject.toml
# [tool.pytest.ini_options]
# testpaths = ["tests"]
# markers = ["hardware", "slow", "regression"]
# addopts = "-ra -q --tb=short"
```

---

### Logging

```python
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def setup_logging(
    name: str,
    log_dir: Path,
    level: int = logging.DEBUG,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> logging.Logger:
    log = logging.getLogger(name)
    log.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    # Rotating file handler
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(
        log_dir / f"{name}.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    log.addHandler(ch)
    log.addHandler(fh)
    return log

log = setup_logging("nvme_test", Path("logs"))

# %-style args -- NOT f-strings -- so the string is only formatted when
# the message will actually be emitted (avoids overhead at low log levels)
log.debug("reading register addr=0x%X", addr)
log.info("test %s: %s in %.3fs", test_name, result, elapsed)
log.warning("retry %d/%d for %s", attempt, max_tries, device)
log.error("FAIL: %s = %.4f outside [%.4f, %.4f]", param, val, lo, hi)
log.exception("unexpected exception")   # includes traceback
```

---

### Concurrency

#### The GIL

CPython's Global Interpreter Lock (GIL) serializes bytecode execution to one thread at a time. Threads are appropriate for I/O-bound work (disk, network, serial port, SCPI) — one thread blocks in the kernel and releases the GIL while others run. They don't parallelize CPU-bound computation. Use `multiprocessing` for CPU-bound work (waveform FFT, large numpy reductions), or `concurrent.futures.ProcessPoolExecutor`.

#### Threads for Instrument I/O

```python
import threading
import time
from collections import deque

class DataCollector:
    """Thread-safe background sampler for any polling read function."""

    def __init__(
        self,
        read_fn,
        interval_s: float = 0.1,
        max_history: int = 1000,
    ):
        self._read_fn = read_fn
        self._interval = interval_s
        self._lock = threading.Lock()
        self._history = deque(maxlen=max_history)
        self._latest = None
        self._active = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self):
        self._active = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0):
        self._active = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _loop(self):
        while not self._stop_event.wait(timeout=self._interval):
            try:
                value = self._read_fn()
                with self._lock:
                    self._latest = value
                    self._history.append((time.monotonic(), value))
            except Exception as exc:
                # swallow transient read errors; log if you want
                _ = exc

    @property
    def latest(self):
        with self._lock:
            return self._latest

    def snapshot(self) -> list[tuple[float, object]]:
        with self._lock:
            return list(self._history)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

# Usage
with DataCollector(lambda: read_psu_voltage("CH1"), interval_s=0.05) as sampler:
    run_stress_test()
    data = sampler.snapshot()
```

**`Lock` vs `RLock`**: `Lock` is not reentrant — a thread that already holds it will deadlock if it tries to acquire it again. `RLock` allows the same thread to acquire it multiple times. Use `RLock` when a method that holds the lock may call other methods that also acquire it.

```python
# threading.Event: clean cancellable loop without sleep polling
stop_event = threading.Event()

def monitoring_thread(stop_event):
    while not stop_event.wait(timeout=1.0):
        check_health()

t = threading.Thread(target=monitoring_thread, args=(stop_event,), daemon=True)
t.start()
# later:
stop_event.set()
t.join()
```

#### ThreadPoolExecutor

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def test_all_devices(devices: list[str], timeout: float = 30) -> dict[str, str]:
    results = {}
    with ThreadPoolExecutor(max_workers=len(devices)) as ex:
        future_to_dev = {ex.submit(run_device_test, dev): dev for dev in devices}
        for fut in as_completed(future_to_dev, timeout=timeout):
            dev = future_to_dev[fut]
            try:
                results[dev] = fut.result()
            except Exception as exc:
                results[dev] = f"ERROR: {exc}"
    return results
```

#### asyncio for Async Instrument I/O

Use `asyncio` when managing many concurrent I/O operations in a single thread — e.g., polling dozens of SCPI instruments, streaming multiple serial ports simultaneously.

```python
import asyncio

async def query_instrument(addr: str, cmd: str, timeout: float = 2.0) -> str:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(addr, 5025), timeout=timeout
    )
    try:
        writer.write((cmd + "\n").encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return data.decode().strip()
    finally:
        writer.close()
        await writer.wait_closed()

async def poll_all(instruments: list[str], cmd: str) -> list[str]:
    tasks = [query_instrument(addr, cmd) for addr in instruments]
    return await asyncio.gather(*tasks, return_exceptions=True)

# Run from synchronous code
results = asyncio.run(poll_all(instrument_list, "MEAS:VOLT?"))
```

Asyncio is cooperative — `await` points are where the event loop can run other coroutines. A coroutine that never awaits blocks the entire event loop.

**`asyncio.timeout` (Python 3.11+)** replaces the nested `wait_for` pattern with a cleaner context manager that raises `TimeoutError` directly:

```python
import asyncio

async def query_instrument_311(addr: str, cmd: str, timeout: float = 2.0) -> str:
    async with asyncio.timeout(timeout):
        reader, writer = await asyncio.open_connection(addr, 5025)
        try:
            writer.write((cmd + "\n").encode())
            await writer.drain()
            data = await reader.readline()
            return data.decode().strip()
        finally:
            writer.close()
            await writer.wait_closed()
```

The timeout covers the entire block, not just one `await`. On Python 3.10 and earlier, keep using `wait_for`.

**Using asyncio from synchronous pytest tests** — `asyncio.run()` works but creates and destroys an event loop per call. For test suites, install `pytest-asyncio` and mark coroutines directly:

```python
# pip install pytest-asyncio
# pyproject.toml: [tool.pytest.ini_options] asyncio_mode = "auto"

import pytest

@pytest.mark.asyncio
async def test_all_instruments_respond(instrument_addrs):
    results = await poll_all(instrument_addrs, "*IDN?")
    for r in results:
        assert not isinstance(r, Exception), f"instrument error: {r}"
```

`asyncio_mode = "auto"` (pytest-asyncio 0.21+) marks all `async def` test functions automatically; you can drop the `@pytest.mark.asyncio` decorator.

---

### pyserial — Serial Port Instruments

```python
import serial
import serial.tools.list_ports
import time

def list_serial_ports() -> list[str]:
    return [p.device for p in serial.tools.list_ports.comports()]

class SerialInstrument:
    """Thin wrapper for RS-232/RS-485 instruments."""

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 1.0,
        terminator: str = "\n",
    ):
        self._ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
        )
        self._term = terminator.encode()

    def __enter__(self):
        if not self._ser.is_open:
            self._ser.open()
        return self

    def __exit__(self, *_):
        self._ser.close()

    def write_cmd(self, cmd: str) -> None:
        self._ser.write((cmd + "\n").encode("ascii"))

    def read_line(self) -> str:
        raw = self._ser.readline()
        return raw.decode("ascii", errors="replace").strip()

    def query(self, cmd: str, delay_s: float = 0.05) -> str:
        self._ser.reset_input_buffer()
        self.write_cmd(cmd)
        time.sleep(delay_s)
        return self.read_line()

    @property
    def in_waiting(self) -> int:
        return self._ser.in_waiting

with SerialInstrument("/dev/ttyUSB0", baudrate=9600) as inst:
    idn = inst.query("*IDN?")
    voltage = float(inst.query("MEAS:VOLT?"))
```

`serial.SerialTimeoutException` is raised when `write` or `readline` exceeds the configured timeout. Wrap in the custom `InstrumentError` hierarchy for consistent error handling across instrument types.

---

### pyvisa — SCPI Instruments (LAN, GPIB, USB)

```python
import pyvisa

class SCPIInstrument:
    """VISA resource wrapper with standard SCPI commands."""

    def __init__(self, resource_string: str, timeout_ms: int = 5000):
        self._addr = resource_string
        self._timeout_ms = timeout_ms
        self._rm: pyvisa.ResourceManager | None = None
        self._inst = None

    def __enter__(self):
        self._rm = pyvisa.ResourceManager()
        self._inst = self._rm.open_resource(self._addr)
        self._inst.timeout = self._timeout_ms
        return self

    def __exit__(self, *_):
        if self._inst:
            self._inst.close()
        if self._rm:
            self._rm.close()

    def write(self, cmd: str) -> None:
        self._inst.write(cmd)

    def query(self, cmd: str) -> str:
        return self._inst.query(cmd).strip()

    def reset(self) -> None:
        self.write("*RST")

    def measure_dc_voltage(self, channel: int = 1) -> float:
        return float(self.query(f"MEAS:VOLT:DC? (@{channel})"))

    def measure_current(self, channel: int = 1) -> float:
        return float(self.query(f"MEAS:CURR:DC? (@{channel})"))

# Supported VISA address strings:
#   LAN:  "TCPIP::192.168.1.10::INSTR"
#   LAN raw socket: "TCPIP::192.168.1.10::5025::SOCKET"
#   GPIB: "GPIB0::14::INSTR"
#   USB:  "USB0::0x0957::0x0607::MY47000419::INSTR"

with SCPIInstrument("TCPIP::10.0.1.50::INSTR") as psu:
    psu.reset()
    psu.write("VOLT 3.3; CURR 2.0; OUTP ON")
    v = psu.measure_dc_voltage()
    assert 3.25 <= v <= 3.35, f"PSU voltage {v:.3f} V out of range"
```

`pyvisa.errors.VisaIOError` covers most instrument communication errors (timeout, resource not found, etc.). Wrap it in `InstrumentError` when you want a unified exception hierarchy.

---

### Socket Programming — the equipment_rpc Pattern

A common manufacturing-test design is a small TCP RPC server that fronts an instrument: the GUI or test runner sends a JSON command, the server executes it against the hardware and returns JSON. This decouples the UI process from the hardware process and lets multiple clients share one instrument.

#### TCP server

```python
import socket
import json

def start_server(host="0.0.0.0", port=5000):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # AF_INET = IPv4; SOCK_STREAM = TCP (reliable, ordered byte stream). UDP would use SOCK_DGRAM.
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # SO_REUSEADDR lets you re-bind a port still in TIME_WAIT after a restart,
    # avoiding "Address already in use" for ~60 s.
    server.bind((host, port))
    server.listen(5)                  # backlog: max queued pending connections
    print(f"Listening on {host}:{port}")
    while True:
        conn, addr = server.accept()  # blocks until a client connects
        with conn:
            data = conn.recv(4096).decode()
            request = json.loads(data)
            response = handle_request(request)
            conn.sendall(json.dumps(response).encode())

def handle_request(req):
    cmd = req.get("command")
    if cmd == "read_voltage":
        return {"status": "ok", "value": 48.01}
    return {"status": "error", "message": f"unknown command: {cmd}"}
```

#### TCP client

```python
def send_command(host, port, command):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(5.0)          # ALWAYS set a timeout; a hung instrument must not block forever
        sock.connect((host, port))
        sock.sendall(json.dumps(command).encode())
        # TCP is a stream, not messages: read until the peer closes (or use a length prefix).
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:             # empty bytes -> peer closed the connection
                break
            chunks.append(chunk)
        return json.loads(b"".join(chunks).decode())
```

Two essentials: always `settimeout`, and remember that `recv` returns whatever bytes are available, so you must loop and reassemble. For request/response framing, prefer a newline-delimited protocol or a fixed-length header that states the body length.

---

### NumPy for Measurement Data

```python
import numpy as np

readings = np.array([3.298, 3.302, 3.299, 3.301, 3.300, 3.297, 3.303])

# Descriptive statistics
mean   = readings.mean()
std    = readings.std(ddof=1)      # ddof=1 for sample std dev
p95    = np.percentile(readings, 95)
rms    = np.sqrt(np.mean(readings**2))

# Spec check with boolean masking
# CRITICAL: hardware specs use & not 'and' with numpy arrays
LOW, HIGH = 3.25, 3.35
mask = (readings >= LOW) & (readings <= HIGH)   # element-wise AND
passing = readings[mask]
n_fail  = (~mask).sum()

# Threshold detection
hot_idx = np.where(readings > 3.305)[0]

# Rolling window — prefer sliding_window_view (NumPy 1.20+, memory-safe)
def rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    # sliding_window_view is zero-copy and bounds-checked; no manual stride math.
    windows = np.lib.stride_tricks.sliding_window_view(arr, window_shape=window)
    return windows.mean(axis=1)

# as_strided is the older alternative (still common in the wild) but dangerous:
# incorrect shape/strides can read out-of-bounds memory without raising an error.
# Only use it when sliding_window_view is unavailable or you need non-contiguous strides.
#   shape   = (arr.size - window + 1, window)
#   strides = (arr.strides[0], arr.strides[0])
#   windows = np.lib.stride_tricks.as_strided(arr, shape=shape, strides=strides)

# Histogram for distribution analysis
counts, edges = np.histogram(readings, bins=20)
bin_centers = (edges[:-1] + edges[1:]) / 2   # midpoint of each bin

# Vectorized register field extraction (batch decode)
raw_regs = np.array([0x1043, 0x2086, 0x30C9], dtype=np.uint32)
speeds   = (raw_regs >> 0) & 0xF    # bits [3:0]
widths   = (raw_regs >> 4) & 0x3F   # bits [9:4]

# Cumulative distribution (empirical CDF) -- useful for latency reporting
sorted_latencies = np.sort(readings)
cdf = np.arange(1, len(sorted_latencies) + 1) / len(sorted_latencies)
p50 = sorted_latencies[np.searchsorted(cdf, 0.50)]
p99 = sorted_latencies[np.searchsorted(cdf, 0.99)]

# np.diff: detect sudden jumps (link speed changes, power events)
deltas = np.diff(readings)
jump_idx = np.where(np.abs(deltas) > 0.005)[0]  # indices where delta > threshold

# np.polyfit: linear drift check over time
t = np.arange(len(readings), dtype=float)
slope, intercept = np.polyfit(t, readings, deg=1)
# if abs(slope) > threshold_per_sample: flag as drift
```

#### pandas for Test Records

```python
import pandas as pd

# Load a multi-run CSV
df = pd.read_csv("results.csv")

# Boolean indexing
failures    = df[df["result"] == "FAIL"]
slow_runs   = df[df["latency_us"] > 100]
nvme_fails  = df[(df["device"] == "nvme0") & (df["result"] == "FAIL")]

# Aggregation by device
summary = (
    df.groupby("device")
    .agg(
        runs=("iteration", "count"),
        mean_bw=("bandwidth_GBps", "mean"),
        p95_latency=("latency_us", lambda s: s.quantile(0.95)),
        fail_count=("result", lambda s: (s == "FAIL").sum()),
    )
    .reset_index()
)

# Pivot table for speed vs queue_depth heatmap
pivot = df.pivot_table(
    values="bandwidth_GBps",
    index="speed",
    columns="queue_depth",
    aggfunc="mean",
)

# Save
summary.to_csv("summary.csv", index=False)

# Merge baseline with current run
baseline = pd.read_csv("baseline.csv")
current  = pd.read_csv("current.csv")
merged   = baseline.merge(current, on=["device", "speed", "queue_depth"],
                           suffixes=("_base", "_cur"))
merged["bw_delta_pct"] = (
    (merged["bandwidth_GBps_cur"] - merged["bandwidth_GBps_base"])
    / merged["bandwidth_GBps_base"] * 100
)
regressions = merged[merged["bw_delta_pct"] < -5.0]
```

#### Practical pandas Patterns for Multi-Device Test Runs

```python
import pandas as pd

# --- Loading multiple per-device JSON result files into one DataFrame ---
import json
from pathlib import Path

def load_run(run_dir: Path) -> pd.DataFrame:
    frames = []
    for jf in sorted(run_dir.glob("*.json")):
        data = json.loads(jf.read_text())
        frames.append(pd.json_normalize(data))   # flattens nested dicts
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

df = load_run(Path("/results/run_2024-01-15"))

# --- Add pass/fail column based on spec limits ---
SPEC = {"bandwidth_GBps": (3.0, None), "latency_us": (None, 150)}

def apply_spec(df: pd.DataFrame, spec: dict) -> pd.DataFrame:
    df = df.copy()
    mask = pd.Series(True, index=df.index)
    for col, (lo, hi) in spec.items():
        if col not in df.columns:
            continue
        if lo is not None:
            mask &= df[col] >= lo
        if hi is not None:
            mask &= df[col] <= hi
    df["pass"] = mask
    return df

df = apply_spec(df, SPEC)

# --- Detect first failure per device ---
first_fail = (
    df[~df["pass"]]
    .sort_values("iteration")
    .groupby("device")
    .first()
    .reset_index()[["device", "iteration", "bandwidth_GBps", "latency_us"]]
)

# --- Pivot + percent-from-nominal heatmap ---
nominal_bw = 6.5   # GBps reference
heat = df.pivot_table(
    values="bandwidth_GBps",
    index="speed",
    columns="queue_depth",
    aggfunc="mean",
)
heat_pct = (heat / nominal_bw - 1) * 100   # percent deviation from nominal

# --- pd.cut: bin latency readings into spec tiers ---
bins   = [0, 50, 100, 150, float("inf")]
labels = ["excellent", "good", "marginal", "fail"]
df["latency_tier"] = pd.cut(df["latency_us"], bins=bins, labels=labels, right=False)
print(df["latency_tier"].value_counts())
```

`pd.json_normalize` flattens nested dicts (e.g., `{"metrics": {"bw": 6.8}}` becomes column `metrics.bw`) — eliminates the manual unpacking loop. `pd.cut` is cleaner than a chain of `np.where` for tiered spec checking.

---

### OOP Patterns for Test Infrastructure

#### Abstract Base Class for Instrument Hierarchy

```python
from abc import ABC, abstractmethod
from typing import Optional
import time, logging

log = logging.getLogger(__name__)

class Instrument(ABC):
    """Base class for all DUT-connected instruments."""

    def __init__(self, resource: str, timeout_s: float = 5.0):
        self.resource = resource
        self.timeout_s = timeout_s
        self._connected = False

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def reset(self) -> None: ...

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @classmethod
    def from_config(cls, cfg: dict) -> "Instrument":
        """Factory: instantiate from a YAML config dict."""
        return cls(cfg["resource"], timeout_s=cfg.get("timeout", 5.0))

    def measure_with_retry(
        self,
        measure_fn,
        retries: int = 3,
        delay_s: float = 0.5,
    ):
        last = None
        for attempt in range(retries):
            try:
                return measure_fn()
            except Exception as exc:
                last = exc
                log.warning("%s.measure retry %d/%d: %s",
                            self.__class__.__name__, attempt + 1, retries, exc)
                time.sleep(delay_s)
        raise last
```

#### @property and Validation

```python
class SpecWindow:
    def __init__(self, low: float, high: float):
        if low > high:
            raise ValueError(f"low={low} > high={high}")
        self._low  = low
        self._high = high

    @property
    def low(self) -> float:
        return self._low

    @low.setter
    def low(self, v: float):
        if v > self._high:
            raise ValueError(f"new low={v} > high={self._high}")
        self._low = v

    def contains(self, v: float) -> bool:
        return self._low <= v <= self._high

    def __repr__(self) -> str:
        return f"SpecWindow(low={self._low}, high={self._high})"
```

#### MRO and Mixins

Python uses C3 linearization for Method Resolution Order (MRO). When using multiple inheritance, keep base classes narrow (mixins), avoid diamond-of-death data attributes, and always use `super()`.

```python
class LogMixin:
    def log_info(self, msg, *args):
        logging.getLogger(type(self).__name__).info(msg, *args)

class RetryMixin:
    def with_retry(self, fn, times=3, delay=0.5):
        for i in range(times):
            try:
                return fn()
            except Exception:
                if i == times - 1:
                    raise
                time.sleep(delay)

class PowerSupply(LogMixin, RetryMixin, Instrument):
    def measure_voltage(self):
        return self.with_retry(lambda: float(self.query("MEAS:VOLT?")))
```

---

### SQLite for Test Results

SQLite is a serverless, file-based database: zero setup, the whole database is one file, and it survives restarts. Ideal for a single-server dashboard with one writer and fast reads. Reach for PostgreSQL/MySQL only when many clients must write concurrently.

```python
import sqlite3
import json, time

conn = sqlite3.connect("dashboard.db")    # opens or creates the file (no server process)

conn.execute("""
    CREATE TABLE IF NOT EXISTS stations (
        hostname TEXT PRIMARY KEY,
        data     TEXT,
        updated  REAL
    )
""")

# Upsert: insert, or overwrite the row if the primary key already exists
conn.execute(
    "INSERT OR REPLACE INTO stations (hostname, data, updated) VALUES (?, ?, ?)",
    (hostname, json.dumps(data), time.time()),
)
conn.commit()      # writes to disk; without it the change is lost
```

The `?` placeholders are **parameterized queries**: they prevent SQL injection and handle quoting. Never build SQL by string interpolation/f-strings.

```python
# Query one row
cur = conn.execute("SELECT data FROM stations WHERE hostname = ?", (hostname,))
row = cur.fetchone()           # a tuple, or None if not found
if row:
    station = json.loads(row[0])

# Query many rows + aggregate
cur = conn.execute("""
    SELECT keyword, AVG(duration_s) AS avg_s
    FROM keyword_timings
    WHERE model = ?
    GROUP BY keyword
""", (model,))
averages = {kw: avg for kw, avg in cur.fetchall()}   # fetchall returns a list of tuples

conn.close()
```

Set `conn.row_factory = sqlite3.Row` to access columns by name (`row["hostname"]`) instead of by index. Use a `with conn:` block to wrap a transaction that auto-commits on success and rolls back on exception.

---

### FastAPI — the Station Dashboard Pattern

FastAPI is a modern Python web framework. A typical use is a monitoring dashboard: each test station POSTs a heartbeat with its status, and the server stores it and serves a live view.

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any, Optional

app = FastAPI(title="Station Dashboard")
# FastAPI() is the app object; routes are registered on it. On each request, FastAPI
# finds the handler whose path/method match and calls it.

@app.get("/api/stations")
def list_stations():
    # GET = read, no side effects. Returning a dict/list -> FastAPI serializes it to JSON.
    return [{"hostname": "STATION-1", "state": "RUN"}]
```

#### Pydantic models: automatic request validation

You declare the expected shape as a class; FastAPI validates the incoming JSON against it and rejects bad input with a 422 automatically.

```python
class HeartbeatPayload(BaseModel):
    model_config = {"extra": "allow"}     # accept unknown fields (forward-compatible payloads)
    hostname: str                          # required; must be a string
    state: str = "IDLE"                    # optional, defaults to "IDLE"
    keyword_index: int = 0                 # optional int (bad type -> 422)
    failures: list[Any] = []               # optional list
    actuator_connected: Optional[bool] = None

@app.post("/heartbeat")
def receive_heartbeat(payload: HeartbeatPayload):
    # If validation fails, the function is never called; FastAPI returns 422.
    data = payload.model_dump()            # convert the model back to a plain dict
    db.upsert(payload.hostname, data)
    return {"status": "ok"}
```

#### Path and query parameters

```python
@app.get("/api/stations/{hostname}")
def get_station(hostname: str):            # {hostname} in the path -> path parameter
    station = db.get(hostname)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")
    return station

@app.get("/api/summary")
def summary(period: str = "24h"):          # not in the path -> query parameter (?period=week)
    if period not in {"24h", "week", "month"}:
        raise HTTPException(status_code=400, detail="Invalid period")
    return db.summary(period)
```

#### Serving static files and the dashboard page

```python
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app.mount("/static", StaticFiles(directory="static"), name="static")  # /static/app.js etc.

@app.get("/")
def dashboard():
    return FileResponse("static/index.html")   # the browser UI; correct Content-Type set for you
```

#### Lifespan (startup/shutdown)

```python
import asyncio
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(background_purge())   # before yield = startup
    yield
    task.cancel()                                     # after yield = shutdown
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)
```

#### Running it and key concepts

```bash
uvicorn server:app --reload --host 0.0.0.0 --port 8080    # dev: auto-reload
uvicorn server:app --host 0.0.0.0 --port 8080 --workers 1 # prod (single worker if state is in-memory)
```

Talking points if asked: sync route handlers (`def`, not `async def`) run in a thread pool, which is fine for fast handlers doing dict ops and a SQLite write. Pydantic removes all the manual `if "hostname" not in data` validation. FastAPI auto-generates interactive OpenAPI docs at `/docs`. Use a single worker when the server keeps in-memory caches, since multiple workers would each hold a separate copy.

---

### PySide6 — Desktop Instrument GUIs

PySide6 is the official Qt binding for Python (LGPL, free for commercial use). It is the standard choice for responsive desktop instrument-control GUIs with live graphing, where a web app would add unacceptable HTTP latency between operator and hardware.

#### Signals and slots: Qt's event system

A **signal** is an event an object can emit; a **slot** is a function that receives it. Objects connect signals to slots, decoupling emitter from receiver.

```python
from PySide6.QtCore import QObject, Signal

class ServoController(QObject):
    state_changed = Signal(str, str)     # declares a signal carrying (state, detail)
    error_occurred = Signal(str)
    stopped = Signal()                   # carries nothing

    def go(self):
        self.state_changed.emit("RAMP", "Ramping to 65 ft-lb")   # notify every connected slot

servo = ServoController()
servo.state_changed.connect(lambda s, d: print(f"{s}: {d}"))     # connect a slot
servo.error_occurred.connect(show_error_dialog)                  # many slots may connect
servo.go()                                                       # fires all connected slots in order
```

Emitting a signal with no connections is harmless (does nothing). Multiple slots can connect to one signal, and one slot can serve many signals.

#### QTimer: periodic work without blocking the UI

```python
from PySide6.QtCore import QTimer

self.tick_timer = QTimer()
self.tick_timer.timeout.connect(self.handle_tick)   # timeout fires every interval
self.tick_timer.setInterval(500)                    # milliseconds
self.tick_timer.start()
# ... later:
self.tick_timer.stop()
```

Use `QTimer` instead of `time.sleep()` in a loop: the timer fires on the main event loop, so the GUI stays responsive (buttons, repaints, dialogs work between ticks). `time.sleep()` freezes the entire UI. And use `QTimer` rather than a background thread when the callback touches widgets, because Qt widgets may only be modified from the main thread.

#### The loop-variable capture pattern in connections

```python
from PySide6.QtWidgets import QCheckBox
for iface in ["RS232", "RS485", "ETH", "CAN", "ANALOG"]:
    cb = QCheckBox(iface)
    cb.toggled.connect(lambda checked, i=iface: self.on_toggle(i, checked))
    # i=iface captures the CURRENT iface; without it every callback would see "ANALOG".
```

#### Threading rule of thumb

Do slow I/O on a worker thread, then deliver results to the GUI by emitting a signal (Qt marshals it to the main thread). Never update widgets directly from a worker thread.

---

### Modules, venv, and Packaging

#### Virtual Environments

```bash
python3 -m venv .venv
source .venv/bin/activate       # macOS/Linux
# .venv\Scripts\activate.bat   # Windows

pip install -r requirements.txt
pip install -e .                # editable install of local package
```

Always activate a venv before running test scripts. The `-e .` (editable) install makes `import mypackage` resolve to your source tree without reinstalling.

#### pyproject.toml

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "zoox-compute-tests"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "pytest>=7.4",
    "pyvisa>=1.14",
    "pyserial>=3.5",
    "numpy>=1.26",
    "pandas>=2.0",
    "PyYAML>=6.0",
]

[project.optional-dependencies]
dev = ["mypy", "ruff", "pytest-cov"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -q --tb=short"

[tool.mypy]
python_version = "3.11"
strict = true
```

#### Package Layout

```
zoox-compute-tests/
  src/
    zoox_tests/
      __init__.py
      instruments/
        __init__.py
        serial_inst.py
        visa_inst.py
      parsers/
        lspci.py
        nvme.py
        dmesg.py
      fixtures/
        conftest.py
  tests/
    test_nvme.py
    test_pcie.py
  pyproject.toml
  requirements.txt
```

`__init__.py` marks a directory as a package. Relative imports (`from .parsers import lspci`) work within the package; use absolute imports in test files.

---

### Worked Problems: Test-Domain Patterns

These are the recurring algorithm patterns that show up in Zoox platform test code. Each maps to a real problem in hardware test automation.

#### Sliding Window: Error Burst Detection

Detect windows where error rate exceeds a threshold — used in link stability monitoring.

```python
from collections import deque
from datetime import datetime

def max_errors_in_window(log_lines: list[str], window_s: float) -> int:
    """Return the maximum number of errors in any window_s-second window."""
    times = []
    for line in log_lines:
        if "[ERROR]" in line:
            ts = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
            times.append(ts.timestamp())

    if not times:
        return 0

    max_count = 0
    left = 0
    for right in range(len(times)):
        while times[right] - times[left] > window_s:
            left += 1
        max_count = max(max_count, right - left + 1)
    return max_count
```

#### Two-Pointer: Merge Sorted Measurement Runs

```python
def merge_sorted_runs(a: list[float], b: list[float]) -> list[float]:
    result = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            result.append(a[i]); i += 1
        else:
            result.append(b[j]); j += 1
    result.extend(a[i:])
    result.extend(b[j:])
    return result
```

#### Stack: Balanced Protocol Framing

Validate that protocol open/close tags are balanced — e.g., PCIe Transaction Layer Packet (TLP) framing in log analysis.

```python
def is_balanced(s: str) -> bool:
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for ch in s:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()
    return not stack
```

#### Hash Map: Two-Sum (Threshold Pairing in Readings)

Find all pairs of readings whose sum meets a target — used when looking for correlated events in two channels.

```python
def two_sum_indices(readings: list[float], target: float) -> list[tuple[int, int]]:
    seen = {}   # value -> index
    pairs = []
    for i, val in enumerate(readings):
        complement = target - val
        if complement in seen:
            pairs.append((seen[complement], i))
        seen[val] = i
    return pairs
```

#### Bit-Field Decoder: Register Status Report

Decode a 32-bit hardware status register into a structured dict — the everyday pattern for PCIe or Non-Volatile Memory Express (NVMe) register parsing.

```python
from dataclasses import dataclass
from typing import ClassVar

@dataclass
class PCIeStatusReg:
    raw: int

    FIELDS: ClassVar[dict[str, tuple[int, int]]] = {
        "correctable_errors": (0,  8),
        "uncorrectable_errs": (8,  8),
        "link_bandwidth_mgmt":(16, 1),
        "link_auto_bw":       (17, 1),
        "link_speed":         (18, 4),
        "link_width":         (22, 6),
    }

    def decode(self) -> dict[str, int]:
        return {
            name: (self.raw >> lsb) & ((1 << width) - 1)
            for name, (lsb, width) in self.FIELDS.items()
        }

reg = PCIeStatusReg(0x0043_0200)
print(reg.decode())
```

#### State Machine: Link Training State Parser

Parse dmesg output tracking PCIe link training state transitions.

```python
def parse_link_states(log_lines: list[str]) -> list[str]:
    """Extract ordered list of PCIe link training states from dmesg."""
    import re
    STATE_PAT = re.compile(r"PCIe.*?link.*?state[:\s]+(\w+)", re.IGNORECASE)
    states = []
    prev = None
    for line in log_lines:
        m = STATE_PAT.search(line)
        if m:
            state = m.group(1).upper()
            if state != prev:
                states.append(state)
                prev = state
    return states
```

#### Anagram Groups: Duplicate Config Detection

Group config file names that are anagrams (same characters, different order) — useful when auditing config sets for accidental duplicates.

```python
from collections import defaultdict

def group_anagrams(names: list[str]) -> list[list[str]]:
    groups = defaultdict(list)
    for name in names:
        key = "".join(sorted(name.lower()))
        groups[key].append(name)
    return [g for g in groups.values() if len(g) > 1]
```

#### OOP Composite: Test Suite with Mixed Test Types

```python
from abc import ABC, abstractmethod

class TestNode(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def run(self) -> dict: ...

    @abstractmethod
    def count(self) -> int: ...

class LeafTest(TestNode):
    def __init__(self, name: str, fn):
        super().__init__(name)
        self._fn = fn

    def run(self) -> dict:
        import traceback
        try:
            self._fn()
            return {"name": self.name, "result": "PASS"}
        except AssertionError as e:
            return {"name": self.name, "result": "FAIL", "reason": str(e)}
        except Exception:
            return {"name": self.name, "result": "ERROR",
                    "reason": traceback.format_exc()}

    def count(self) -> int:
        return 1

class TestSuite(TestNode):
    def __init__(self, name: str):
        super().__init__(name)
        self._children: list[TestNode] = []

    def add(self, node: TestNode) -> "TestSuite":
        self._children.append(node)
        return self

    def run(self) -> dict:
        results = [child.run() for child in self._children]
        passed  = sum(1 for r in results if r["result"] == "PASS")
        return {
            "name": self.name,
            "result": "PASS" if passed == len(results) else "FAIL",
            "children": results,
            "summary": f"{passed}/{len(results)}",
        }

    def count(self) -> int:
        return sum(c.count() for c in self._children)
```

---

### Quick Reference: Pitfalls and Idioms

| Pattern | Wrong | Right |
|---------|-------|-------|
| Float compare | `x == 3.3` | `math.isclose(x, 3.3, abs_tol=0.01)` |
| Mutable default | `def f(lst=[]):` | `def f(lst=None): lst = lst or []` |
| Late-bind lambda | `[lambda: i for i in r]` | `[lambda i=i: i for i in r]` |
| numpy boolean | `(a > 0) and (a < 10)` | `(a > 0) & (a < 10)` |
| groupby without sort | `groupby(data, key)` | `groupby(sorted(data, key=key), key)` |
| subprocess injection | `run(f"lspci -s {bdf}", shell=True)` | `run(["lspci", "-s", bdf])` |
| YAML unsafe load | `yaml.load(f)` | `yaml.safe_load(f)` |
| patch target | `patch("mod.original_module.func")` | `patch("mod.tests.func")` (where used) |
| `is` for equality | `x is "hello"` | `x == "hello"` |
| Bare except | `except:` | `except Exception:` |
| Suppress all errors | `except Exception: pass` | log + re-raise or specific handling |
| `str(Path(...))` | `str(Path("/a") + "/b")` | `str(Path("/a") / "b")` |
| pyproject build-backend | `"setuptools.backends.legacy:build"` | `"setuptools.build_meta"` |
| nvme list JSON (>=2.11) | `data["Devices"][0]["DevicePath"]` | check for `"Subsystems"` key first |
| rolling window (numpy) | `np.lib.stride_tricks.as_strided(...)` | `sliding_window_view(arr, window)` (1.20+) |
| sudo in CI/daemon | `subprocess.run(["sudo", ...])` hangs | add `--non-interactive`; configure NOPASSWD |
| TimeoutExpired output | discard `exc.stdout` | `exc.stdout or ""` has partial output |
| fixture vs parametrize | `@parametrize` for resource variants | `@fixture(params=...)` for resources, `@parametrize` for data |
| tmp_path in session fixture | `tmp_path` (function-scoped, fails) | `tmp_path_factory.mktemp(...)` |
