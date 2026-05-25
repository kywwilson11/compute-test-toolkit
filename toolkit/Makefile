# Compute-test toolkit — build, test, demo.
PY ?= python3
export PYTHONPATH := src

.PHONY: all c test demo plan dashboard clean

all: c test

c:                       ## build the C BERT engine
	$(MAKE) -C c

test:                    ## run the pytest suite (uses the mock backend)
	COMPUTETEST_BACKEND=mock $(PY) -m pytest

demo:                    ## run the PCIe BERT demo (no hardware)
	$(PY) demo_bert.py

plan:                    ## run the full example test plan (no hardware)
	COMPUTETEST_BACKEND=mock $(PY) -m computetest.cli plan configs/example_plan.json

dashboard:               ## serve the results dashboard (needs: pip install fastapi uvicorn)
	COMPUTETEST_DB=results.db uvicorn dashboard.app:app --reload

clean:
	$(MAKE) -C c clean
	rm -rf **/__pycache__ .pytest_cache results.db
