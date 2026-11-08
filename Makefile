PYTHON ?= python3
INPUT ?= BaseTeste.xlsx
OUTPUT ?= outputs

.PHONY: run test
run:
	$(PYTHON) -m pipeline.run --input "$(INPUT)" --output "$(OUTPUT)"

test:
	$(PYTHON) -m unittest discover -s tests -v
