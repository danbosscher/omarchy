#!/bin/bash

set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"

dns="$ROOT/bin/omarchy-dns"
hardware_network="$ROOT/install/hardware/network.sh"

! grep -F 'systemd-networkd' "$dns" >/dev/null || fail "omarchy-dns no longer restarts systemd-networkd"

test_tmp=$(mktemp -d)
trap 'rm -rf "$test_tmp"' EXIT
mkdir -p "$test_tmp/bin" "$test_tmp/etc/systemd"
export DNS_CALLS="$test_tmp/calls"

# Run the real provider dispatch with its config files redirected into the
# fixture. Elevation and the root-only PATH pin are covered by dns-sudoers-test;
# remove them here so even a root test run can only reach the stubs below.
sed -e "s|/etc/|$test_tmp/etc/|g" \
  -e '/^require_root "\$provider"$/d' \
  -e '/^if (( EUID == 0 )); then$/,/^fi$/d' \
  "$dns" >"$test_tmp/omarchy-dns"

cat >"$test_tmp/bin/nmcli" <<'SH'
#!/bin/bash
printf 'nmcli %s\n' "$*" >>"$DNS_CALLS"

# These saved profiles cover user-owned static DNS, a user-selected public
# provider indistinguishable from legacy Omarchy settings, and ordinary DHCP.
# Any attempt to rewrite them is recorded, never sent to NetworkManager.
case "$*" in
  '-t -f UUID,TYPE connection show')
    printf 'lab:802-3-ethernet\npublic:802-11-wireless\nauto:802-3-ethernet\nvpn:vpn\n'
    ;;
  '-g ipv4.ignore-auto-dns,ipv4.dns,ipv6.ignore-auto-dns,ipv6.dns connection show lab')
    printf 'yes\n192.168.50.2\nyes\nfd00::53\n'
    ;;
  '-g ipv4.ignore-auto-dns,ipv4.dns,ipv6.ignore-auto-dns,ipv6.dns connection show public')
    printf 'yes\n1.1.1.1,1.0.0.1\nyes\n2606:4700:4700::1111\n'
    ;;
  '-g ipv4.ignore-auto-dns,ipv4.dns,ipv6.ignore-auto-dns,ipv6.dns connection show auto')
    printf 'no\n\nno\n\n'
    ;;
  '-t -f DEVICE,TYPE,STATE device status')
    printf 'enp10s0:ethernet:connected\nlo:loopback:connected\n'
    ;;
esac
SH
chmod +x "$test_tmp/bin/nmcli"

cat >"$test_tmp/bin/systemctl" <<'SH'
#!/bin/bash
printf 'systemctl %s\n' "$*" >>"$DNS_CALLS"
SH
chmod +x "$test_tmp/bin/systemctl"

export PATH="$test_tmp/bin:$PATH"
nm_conf="$test_tmp/etc/NetworkManager/conf.d/20-omarchy-dns.conf"
resolved_conf="$test_tmp/etc/systemd/resolved.conf"
expected_calls=$(cat <<'EOF'
systemctl is-active --quiet NetworkManager.service
nmcli general reload conf
systemctl reload systemd-resolved.service
systemctl is-active --quiet NetworkManager.service
nmcli general reload dns-full
EOF
)

run_provider() {
  local provider="$1"
  local input="${2:-}"

  : >"$DNS_CALLS"
  if ! printf '%s\n' "$input" | bash "$test_tmp/omarchy-dns" "$provider" >"$test_tmp/output" 2>&1; then
    fail "DNS provider switch to $provider succeeds" "$(cat "$test_tmp/output")"
  fi

  # Only reload global config and republish DNS after resolved reloads. This
  # also forbids profile cleanup and device reapply for every provider.
  [[ $(cat "$DNS_CALLS") == "$expected_calls" ]] ||
    fail "$provider preserves saved connection DNS and avoids DHCP churn" "$(cat "$DNS_CALLS")"
  [[ $(bash "$test_tmp/omarchy-dns") == "$provider" ]] ||
    fail "$provider is reported after switching"
}

run_provider Cloudflare
grep -Fx '[global-dns-domain-*]' "$nm_conf" >/dev/null
grep -Fx 'servers=1.1.1.1,1.0.0.1,2606:4700:4700::1111,2606:4700:4700::1001' "$nm_conf" >/dev/null
grep -F 'DNS=1.1.1.1#cloudflare-dns.com' "$resolved_conf" >/dev/null
pass "Cloudflare applies a global override without rewriting or reapplying profiles"

run_provider Google
grep -Fx 'servers=8.8.8.8,8.8.4.4,2001:4860:4860::8888,2001:4860:4860::8844' "$nm_conf" >/dev/null
grep -F 'DNS=8.8.8.8#dns.google' "$resolved_conf" >/dev/null
pass "Google replaces the global override without rewriting or reapplying profiles"

run_provider Custom '192.168.50.2 fd00::53'
grep -Fx 'servers=192.168.50.2,fd00::53' "$nm_conf" >/dev/null
grep -Fx 'DNS=192.168.50.2 fd00::53' "$resolved_conf" >/dev/null
pass "Custom applies IPv4 and IPv6 DNS without rewriting or reapplying profiles"

run_provider DHCP
[[ ! -e $nm_conf ]] || fail "DHCP removes the global NetworkManager override"
[[ $(cat "$resolved_conf") == $'[Resolve]\nDNSOverTLS=no' ]] ||
  fail "DHCP clears resolved's global override"
pass "DHCP removes global overrides and preserves user and legacy profile DNS"

run_provider DHCP
[[ ! -e $nm_conf ]] || fail "repeated DHCP does not recreate the global override"
pass "repeated DHCP leaves saved profiles untouched"

grep -F 'systemd-networkd.service' "$hardware_network" >/dev/null
grep -F 'systemd-networkd.socket' "$hardware_network" >/dev/null
grep -F '20-wlan.network' "$hardware_network" >/dev/null
grep -F 'omarchy-networkd-retired' "$hardware_network" >/dev/null
pass "hardware setup retires archinstall networkd state"
