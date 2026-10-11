YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	git ls-files '*.yml' '*.yaml' >/dev/null && $(YAMLLINT) --strict one.yml

actionlint:
	$(ACTIONLINT)
