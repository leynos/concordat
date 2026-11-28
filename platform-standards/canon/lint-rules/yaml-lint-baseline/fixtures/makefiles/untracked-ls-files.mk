YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	git ls-files -z --others '*.yml' '*.yaml' | xargs -0 -r $(YAMLLINT) --strict --

actionlint:
	$(ACTIONLINT)