# Compute-test toolkit — build, test, lint, mutate, demo.
PY ?= python3
export PYTHONPATH := src

.PHONY: all c ctest test lint typecheck coverage check mutmut demo plan dashboard clean

all: c ctest check         ## build C engine + run all lint/type/coverage gates

c:                         ## build the C BERT engine
	$(MAKE) -C c

ctest:                     ## run the C unit tests (Unity)
	$(MAKE) -C c ctest

test:                      ## run the pytest suite (uses the mock backend)
	COMPUTETEST_BACKEND=mock $(PY) -m pytest

lint:                      ## ruff (style + import-sort + bugbear + pyupgrade)
	$(PY) -m ruff check src tests

typecheck:                 ## mypy on src
	$(PY) -m mypy src

coverage:                  ## pytest under coverage; reports gaps + enforces fail_under from pyproject
	COMPUTETEST_BACKEND=mock $(PY) -m coverage run -m pytest
	$(PY) -m coverage report -m

check: lint typecheck coverage  ## what CI gates on every push/PR

mutmut:                    ## mutation testing on the 6 logic modules (setup.cfg [mutmut])
	COMPUTETEST_BACKEND=mock $(PY) -m mutmut run
	$(PY) -m mutmut results || true

demo:                      ## run the PCIe BERT demo (no hardware)
	$(PY) demo_bert.py

plan:                      ## run the full example test plan (no hardware)
	COMPUTETEST_BACKEND=mock $(PY) -m computetest.cli plan configs/example_plan.json

dashboard:                 ## serve the results dashboard (needs: pip install fastapi uvicorn)
	COMPUTETEST_DB=results.db uvicorn dashboard.app:app --reload

clean:
	$(MAKE) -C c clean
	rm -rf **/__pycache__ .pytest_cache .coverage .ruff_cache .mypy_cache .mutmut-cache \
	       .hypothesis results.db
