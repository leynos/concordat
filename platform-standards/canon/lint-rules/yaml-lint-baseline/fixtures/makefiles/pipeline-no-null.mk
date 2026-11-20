YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -r $(YAMLLINT) --strict --

actionlint:
	$(ACTIONLINT)