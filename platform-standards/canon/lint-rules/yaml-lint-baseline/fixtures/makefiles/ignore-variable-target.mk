YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
SPECIAL := .IGNORE
.PHONY: lint yamllint actionlint
$(SPECIAL): yamllint actionlint

lint: yamllint actionlint

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 -r $(YAMLLINT) --strict --

actionlint:
	$(ACTIONLINT)
