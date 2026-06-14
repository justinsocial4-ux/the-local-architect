#!/usr/bin/env bash
# Run the Fable Method test suite.
#
# basetemp is auto-handled by conftest.py, so this "just works" — no need to pass
# --basetemp. Any extra args are forwarded to pytest (e.g. -k pattern, -x, -vv).
#
#   ./run_tests.sh            # full suite
#   ./run_tests.sh -k bypass  # just the bypass probes
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m pytest fable_method/tests/test_engine.py -q -p no:cacheprovider "$@"
