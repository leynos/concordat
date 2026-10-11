.PHONY: yamllint actionlint

yamllint:
	git ls-files -z '*.yml' '*.yaml' | xargs -0 -r yamllint --strict --

actionlint:
	actionlint
