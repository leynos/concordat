YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
IGNORED := yamllint actionlint
.PHONY: lint yamllint actionlint
.IGNORE: $(IGNORED)

lint: yamllint actionlint

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 -r $(YAMLLINT) --strict --

actionlint:
	$(ACTIONLINT)
