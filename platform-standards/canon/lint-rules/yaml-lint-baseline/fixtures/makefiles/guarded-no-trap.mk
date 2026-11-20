YAMLLINT ?= yamllint
ACTIONLINT ?= actionlint
.PHONY: lint yamllint actionlint

lint: yamllint actionlint

yamllint:
	@yaml_list=$$(mktemp -t yaml.XXXXXX) || exit 2; \
	git ls-files -z '*.yml' '*.yaml' >"$$yaml_list" && \
	xargs -0 -r $(YAMLLINT) --strict -- <"$$yaml_list"

actionlint:
	$(ACTIONLINT)
