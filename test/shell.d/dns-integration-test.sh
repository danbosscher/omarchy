#!/bin/bash

set -euo pipefail
source "$(dirname "$0")/base-test.sh"

# Real daemons and DNS packets, but only inside private network/mount namespaces.
# Opt in because hardened builders may forbid user namespaces or lack daemons.
if [[ ${OMARCHY_TEST_DNS_INTEGRATION:-0} != "1" ]]; then
  pass "set OMARCHY_TEST_DNS_INTEGRATION=1 to run the isolated DNS integration test"
  exit 0
fi

for command in bwrap ip nmcli resolvectl dbus-daemon python3; do
  require_command "$command"
done
test_tmp=$(mktemp -d)
trap 'rm -rf "$test_tmp"' EXIT
if ! bwrap --unshare-all --uid 0 --gid 0 --cap-add ALL \
  --setenv OMARCHY_DNS_TEST_NAMESPACE 1 \
  --ro-bind / / --dev /dev --proc /proc --tmpfs /run --tmpfs /tmp \
  --tmpfs /etc --tmpfs /var --ro-bind "$ROOT" /usr/share/omarchy \
  --bind "$test_tmp" /tmp/lab --die-with-parent \
  /bin/bash /usr/share/omarchy/test/shell.d/fixtures/dns/lab.sh; then
  for log in "$test_tmp"/*.log; do
    [[ -f $log ]] && tail -n 35 "$log" >&2
  done
  fail "isolated NetworkManager/resolved DNS integration"
fi
pass "real DNS selection, VPN routing, reconnects, resolver restarts, and DHCP lease stability"
