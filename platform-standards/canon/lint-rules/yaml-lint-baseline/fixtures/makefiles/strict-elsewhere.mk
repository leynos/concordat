YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 $(YAMLLINT) ; $(YAMLLINT) --strict good.yml

actionlint:
	$(ACTIONLINT)