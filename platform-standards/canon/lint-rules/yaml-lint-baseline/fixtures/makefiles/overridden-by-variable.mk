YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
REPLACED := yamllint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 -r $(YAMLLINT) --strict --

actionlint:
	$(ACTIONLINT)

$(REPLACED):
	@echo not linting
