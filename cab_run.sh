#!/usr/bin/env bash
PYTHONPATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd):${PYTHONPATH:-}" exec python3 -m cab.cli "$@"
