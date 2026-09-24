#!/usr/bin/env bash
# Fetches an unrelated tool; the lints themselves come from install-whitaker.
set -euo pipefail
curl -fsSL "https://github.com/leynos/mdtablefix/releases/download/v0.6.0/mdtablefix.tgz" -o mdtablefix.tgz
whitaker --all
