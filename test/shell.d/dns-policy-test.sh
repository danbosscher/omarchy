#!/bin/bash

set -euo pipefail
source "$(dirname "$0")/base-test.sh"

python3 -B "$SHELL_TEST_DIR/fixtures/dns/policy-test.py"
pass "DNS policies validate input, preserve VPN routing and encryption, and roll back failures"
