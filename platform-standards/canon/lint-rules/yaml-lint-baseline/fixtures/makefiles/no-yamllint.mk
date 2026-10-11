YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: actionlint

actionlint:
	$(ACTIONLINT)
