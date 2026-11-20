YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	@tmp=$$(mktemp) || exit 1; \
	trap 'exit 0' EXIT; \
	git ls-files -z '*.yml' '*.yaml' >"$$tmp" && \
	xargs -0 -r $(YAMLLINT) --strict -- <"$$tmp"

actionlint:
	$(ACTIONLINT)
