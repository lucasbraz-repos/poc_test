PYTHON ?= python3
INPUT ?= BaseTeste.xlsx
OUTPUT ?= outputs

.PHONY: run test package-aws
run:
	$(PYTHON) -m pipeline.run --input "$(INPUT)" --output "$(OUTPUT)"

test:
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) -m unittest discover -s tests_aws -v

package-aws:
	$(PYTHON) tools/package_aws.py
