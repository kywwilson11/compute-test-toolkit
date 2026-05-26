import os
import sys

# Make `import computetest` work when running pytest from the repo without install.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
# Make sibling test helpers (e.g. sysfs_fixture) importable regardless of pytest import mode.
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("COMPUTETEST_BACKEND", "mock")
