YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 -r --arg-file=other.list $(YAMLLINT) --strict --

actionlint:
	$(ACTIONLINT)