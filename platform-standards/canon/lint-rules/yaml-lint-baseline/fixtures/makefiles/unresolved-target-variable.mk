YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
TOOLS := ruff
TOOLS += ty
.PHONY: lint yamllint actionlint $(TOOLS)

lint: yamllint actionlint

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 -r $(YAMLLINT) --strict --

actionlint:
	$(ACTIONLINT)

$(TOOLS):
	@command -v $@ >/dev/null
