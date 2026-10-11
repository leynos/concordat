YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 -n 20 -r $(YAMLLINT) -s --

actionlint:
	$(ACTIONLINT) -config-file .github/actionlint.yaml
