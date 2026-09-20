#!/bin/bash
set -euo pipefail

# Called only by dns-integration-test.sh's bwrap with private /etc, /run and netns.
[[ ${OMARCHY_DNS_TEST_NAMESPACE:-} == "1" ]] || exit 1
mkdir -p /run/dbus /run/systemd/resolve /run/NetworkManager /var/lib/NetworkManager /etc/NetworkManager/conf.d /etc/NetworkManager/system-connections /etc/systemd/resolved.conf.d
ln -s /run /var/run
printf 'root:x:0:0:root:/root:/bin/bash\nsystemd-resolve:x:0:0:resolver:/:/usr/bin/nologin\n' >/etc/passwd
printf 'root:x:0:\nsystemd-resolve:x:0:\n' >/etc/group
printf 'passwd: files\ngroup: files\nhosts: files dns\n' >/etc/nsswitch.conf
printf '127.0.0.1 localhost\n' >/etc/hosts
printf 'test-machine\n' >/etc/hostname
printf '0123456789abcdef0123456789abcdef\n' >/etc/machine-id
printf '[Resolve]\nDNSSEC=no\nDNSOverTLS=no\nLLMNR=no\nMulticastDNS=no\n' >/etc/systemd/resolved.conf
ln -s /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
cat >/etc/NetworkManager/NetworkManager.conf <<'CONF'
[main]
plugins=keyfile
dns=systemd-resolved
rc-manager=unmanaged
auth-polkit=false
[connectivity]
interval=0
[device-servers]
match-device=interface-name:server*;interface-name:vpn*;interface-name:lo
managed=0
[logging]
level=DEBUG
domains=DHCP4,DHCP6,DNS
CONF
export DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket
export XDG_RUNTIME_DIR=/run
unset DBUS_SESSION_BUS_ADDRESS
dbus-daemon --session --address="$DBUS_SYSTEM_BUS_ADDRESS" --fork
cp -a /usr/share/omarchy/etc/NetworkManager/dispatcher.d /etc/NetworkManager/
/usr/lib/nm-dispatcher --persist --debug >/tmp/lab/dispatcher.log 2>&1 &
/usr/bin/NetworkManager --debug >/tmp/lab/nm.log 2>&1 &

ip link set lo up
ip netns add dns-server
ip -n dns-server link set lo up
ip link add uplink0 type veth peer name server0
ip link set server0 netns dns-server
ip -n dns-server addr add 192.0.2.1/24 dev server0
ip -n dns-server link set server0 up
for address in 1.1.1.1 1.0.0.1 8.8.8.8 8.8.4.4 192.0.2.53; do
  ip -n dns-server address add "$address/32" dev server0
done
ip -n dns-server -6 address add 2001:db8::53/64 dev server0 nodad
ip link add vpn0 type veth peer name vpnserver0
ip link set vpnserver0 netns dns-server
ip addr add 198.51.100.2/24 dev vpn0
ip -n dns-server addr add 198.51.100.1/24 dev vpnserver0
ip link set vpn0 up
ip -n dns-server link set vpnserver0 up
ip netns exec dns-server python3 -B /usr/share/omarchy/test/shell.d/fixtures/dns/server.py >/tmp/lab/queries.log 2>&1 &

ip netns exec dns-server python3 -B /usr/share/omarchy/test/shell.d/fixtures/dns/dhcp-server.py >/tmp/lab/dhcp.log 2>&1 &
python3 -B /usr/share/omarchy/test/shell.d/fixtures/dns/integration.py
