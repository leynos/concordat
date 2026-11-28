YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint interrogate

lint: yamllint actionlint
	@echo linted

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 -r $(YAMLLINT) --strict --

actionlint:
	$(ACTIONLINT)

interrogate:
	@echo interrogate

lint: interrogate
