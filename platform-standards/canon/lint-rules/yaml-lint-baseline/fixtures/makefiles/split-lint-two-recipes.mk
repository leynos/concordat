YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint
	@echo linted

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 -r $(YAMLLINT) --strict --

actionlint:
	$(ACTIONLINT)

lint:
	@echo replaces the first recipe
