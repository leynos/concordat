YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	@tmp=$$(mktemp >/dev/null; printf /dev/null) || exit 1; \
	trap 'rm -f "$$tmp"' EXIT; \
	git ls-files -z '*.yml' '*.yaml' >"$$tmp" && \
	xargs -0 -r $(YAMLLINT) --strict -- <"$$tmp"

actionlint:
	$(ACTIONLINT)