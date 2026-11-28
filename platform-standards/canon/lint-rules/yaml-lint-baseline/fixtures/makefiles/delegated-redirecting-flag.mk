YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint:
	$(MAKE) -k yamllint && $(MAKE) actionlint

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 -r $(YAMLLINT) --strict --

actionlint:
	$(ACTIONLINT)
