YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	@tmp=$$(mktemp) || exit 1; \
	trap 'rm -f "$$tmp"' EXIT; \
	git ls-files '*.yml' '*.yaml' >"$$tmp" && \
	xargs -r $(YAMLLINT) --strict -- <"$$tmp"

actionlint:
	$(ACTIONLINT)
