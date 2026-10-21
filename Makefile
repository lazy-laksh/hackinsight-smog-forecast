# One-command entry points for the project.
# On Windows, run these from Git Bash, or call the Python command shown
# on each line directly (for example: python -m smog data).

PYTHON ?= python
export PYTHONPATH := src

.PHONY: data clean test help schedule model

help:
	@echo "make data   - fetch raw files, verify them, build cleaned + gold tables, run quality checks"
	@echo "make clean  - delete everything under data/ except manifest.csv"
	@echo "make test   - run the unit tests"
	@echo "make model - train the forecast model and write reports/ (model card, metrics)"
	@echo "make schedule - start the hourly Prefect schedule (keep it running)"

data:
	$(PYTHON) -m smog data

clean:
	$(PYTHON) -m smog clean

test:
	$(PYTHON) -m pytest -q

model:
	$(PYTHON) -m smog train

schedule:
	$(PYTHON) -m smog.flow
