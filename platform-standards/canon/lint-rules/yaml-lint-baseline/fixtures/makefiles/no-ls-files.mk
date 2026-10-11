YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	$(YAMLLINT) --strict .

actionlint:
	$(ACTIONLINT)
