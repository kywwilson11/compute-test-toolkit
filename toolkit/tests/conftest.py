import os
import sys

# Make `import computetest` work when running pytest from the repo without install.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("COMPUTETEST_BACKEND", "mock")
