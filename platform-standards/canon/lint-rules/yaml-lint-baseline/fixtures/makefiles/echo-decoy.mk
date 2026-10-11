YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	@echo "$(YAMLLINT) --strict $$(git ls-files '*.yml' '*.yaml')"

actionlint:
	@echo "$(ACTIONLINT)"
