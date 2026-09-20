"""Run real daemons; assertions inspect packets, saved profiles and DHCP logs."""
import importlib.util
import base64
import json
import os
from pathlib import Path
import socket
import secrets
import subprocess
import time

ROOT = Path('/usr/share/omarchy')
assert os.environ.get('OMARCHY_DNS_TEST_NAMESPACE') == '1', 'Use dns-integration-test.sh to isolate the test'
HELPER = ROOT / 'default/dns/dns.py'
spec = importlib.util.spec_from_file_location('dns', HELPER)
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)


def run(*args, input=None):
  result = subprocess.run(args, input=input, text=True, capture_output=True, timeout=20)
  if result.returncode:
    raise AssertionError(f'{args}: {result.stderr}')
  return result.stdout


def wait_for(predicate):
  deadline = time.monotonic() + 10
  while time.monotonic() < deadline:
    try:
      if predicate():
        return
    except Exception:
      pass
    time.sleep(0.05)
  raise AssertionError('Timed out waiting for daemon state')


def start_resolved():
  proc = subprocess.Popen(['/usr/lib/systemd/systemd-resolved'], stdout=open('/tmp/lab/resolved.log', 'a'), stderr=subprocess.STDOUT)
  wait_for(lambda: run('resolvectl', 'status'))
  return proc


def select(provider, custom=None):
  run('/usr/bin/python3', '-I', str(HELPER), 'set', provider, input=custom)
  assert run('/usr/bin/python3', '-I', str(HELPER), 'status').strip() == provider


def link(interface):
  resolver = dns.Resolver()
  index = socket.if_nametoindex(interface)
  path = resolver.call(dns.RESOLVED, dns.RESOLVED_PATH, dns.RESOLVED + '.Manager', 'GetLink', '(i)', (index,))[0]
  return resolver.properties(dns.RESOLVED, path, dns.RESOLVED + '.Link')


def addresses(interface='uplink0'):
  return [socket.inet_ntop(entry[0], bytes(entry[1])) for entry in link(interface)['DNSEx']]


def query(name, expected):
  result = run('resolvectl', 'query', '-t', 'A', name)
  assert expected in result, result


def profiles():
  return {p.name: p.read_bytes() for p in Path('/etc/NetworkManager/system-connections').iterdir()}


def check(label):
  print('ok - ' + label, flush=True)


resolved = start_resolved()
wait_for(lambda: run('nmcli', 'general', 'status'))
run('nmcli', 'con', 'add', 'type', 'ethernet', 'ifname', 'uplink0', 'con-name', 'uplink',
    'ipv4.method', 'manual', 'ipv4.addresses', '192.0.2.2/24', 'ipv4.gateway', '192.0.2.1',
    'ipv4.dns', '192.0.2.1', 'ipv4.dns-search', 'home.test',
    'ipv6.method', 'manual', 'ipv6.addresses', '2001:db8::2/64', 'ipv6.may-fail', 'no')
run('nmcli', 'con', 'up', 'uplink')
run('ping', '-6', '-c', '1', '-W', '2', '2001:db8::53')
query('native.example.test', '192.0.2.10')
before = profiles()
routing = {key: link('uplink0')[key] for key in ('Domains', 'DefaultRoute', 'DNSOverTLS')}
for provider, answer, custom in [('Cloudflare', '192.0.2.11', None), ('Google', '192.0.2.12', None),
                                  ('Custom', '192.0.2.13', '192.0.2.53\n'), ('Custom', '192.0.2.14', '2001:db8::53\n')]:
  select(provider, custom)
  query(f'{provider.lower()}-{answer.rsplit(".", 1)[1]}.example.test', answer)
  assert profiles() == before
  assert {key: link('uplink0')[key] for key in routing} == routing
select('DHCP')
assert addresses() == ['192.0.2.1']
query('restore.example.test', '192.0.2.10')
assert profiles() == before
check('IPv4/IPv6 provider switches and DHCP restore leave saved profiles and routing unchanged')

select('Google')
run('nmcli', 'con', 'down', 'uplink')
run('nmcli', 'con', 'up', 'uplink')
# pre-up must apply before NetworkManager reports activation complete.
assert addresses() == dns.PRESETS['Google']
query('reconnect.example.test', '192.0.2.12')
run('nmcli', 'general', 'reload', 'dns-full')
wait_for(lambda: addresses() == dns.PRESETS['Google'])
check('pre-up and dns-change hooks restore the selected provider')

run('resolvectl', 'dns', 'vpn0', '198.51.100.1')
run('resolvectl', 'domain', 'vpn0', '~corp.test')
run('resolvectl', 'default-route', 'vpn0', 'no')
vpn_before = link('vpn0')
select('Cloudflare')
query('private.corp.test', '192.0.2.15')
query('public.example.test', '192.0.2.11')
for key in ('DNSEx', 'Domains', 'DefaultRoute', 'DNSOverTLS'):
  assert link('vpn0')[key] == vpn_before[key]
run('resolvectl', 'domain', 'vpn0', '~.')
select('Google')
query('privacy-vpn.example.test', '192.0.2.15')
requests = [json.loads(line) for line in Path('/tmp/lab/queries.log').read_text().splitlines()]
assert {r['server'] for r in requests if r['name'] == 'privacy-vpn.example.test'} == {'198.51.100.1'}
run('resolvectl', 'revert', 'vpn0')
check('split DNS and privacy VPN routes win without sending the same query to public DNS')

resolved.terminate()
resolved.wait(timeout=5)
resolved = start_resolved()
# Execute the exact command shipped in ExecStartPost, without a host systemd.
unit = (ROOT / 'etc/systemd/system/systemd-resolved.service.d/20-omarchy-dns.conf').read_text()
command = next(line.split('=', 1)[1].lstrip('-+') for line in unit.splitlines() if line.startswith('ExecStartPost='))
run(*command.split())
wait_for(lambda: addresses() == dns.PRESETS['Google'])
query('restart.example.test', '192.0.2.12')
check('resolved restart republishes native routing and reapplies the chosen DNS')

run('nmcli', 'con', 'add', 'type', 'wireguard', 'ifname', 'wg0', 'con-name', 'test-vpn',
    'wireguard.private-key', base64.b64encode(secrets.token_bytes(32)).decode(),
    'ipv4.method', 'manual', 'ipv4.addresses', '10.55.0.2/24', 'ipv4.dns', '198.51.100.1',
    'ipv4.dns-search', '~.', 'ipv4.dns-priority', '-50', 'ipv4.never-default', 'yes',
    'ipv6.method', 'disabled')
run('nmcli', 'con', 'up', 'test-vpn')
wait_for(lambda: not addresses())
select('Cloudflare')
assert not addresses(), 'Negative-priority VPN must keep uplink DNS suppressed'
assert addresses('wg0') == ['198.51.100.1']
assert ('.', True) in link('wg0')['Domains']
run('nmcli', 'con', 'down', 'test-vpn')
run('nmcli', 'con', 'delete', 'test-vpn')
wait_for(lambda: addresses() == dns.PRESETS['Cloudflare'])
check('NetworkManager WireGuard DNS and negative-priority exclusions remain intact')

select('DHCP')
run('nmcli', 'con', 'mod', 'uplink', 'ipv4.dns', '')
run('nmcli', 'con', 'down', 'uplink')
run('nmcli', 'con', 'up', 'uplink')
select('Google')
query('no-native-dns.example.test', '192.0.2.12')
check('a default uplink without native DNS can use the chosen provider')

select('DHCP')
run('nmcli', 'con', 'mod', 'uplink', 'ipv4.method', 'auto', 'ipv4.addresses', '', 'ipv4.gateway', '',
    'ipv4.dns-search', '', 'ipv4.may-fail', 'no', 'ipv6.method', 'disabled', 'ipv6.addresses', '')
run('nmcli', 'con', 'down', 'uplink')
run('nmcli', 'con', 'up', 'uplink')
wait_for(lambda: addresses() == ['192.0.2.1'])
wait_for(lambda: '192.0.2.100/24' in run('nmcli', '-g', 'IP4.ADDRESS', 'device', 'show', 'uplink0'))
before = profiles()
address_before = run('nmcli', '-g', 'IP4.ADDRESS', 'device', 'show', 'uplink0')
log = Path('/tmp/lab/nm.log')
starts_before = log.read_text().count('activation: beginning transaction')
assert starts_before > 0
for provider in ('Cloudflare', 'Google', 'DHCP', 'Google'):
  select(provider)
  assert run('nmcli', '-g', 'IP4.ADDRESS', 'device', 'show', 'uplink0') == address_before
  assert profiles() == before
assert log.read_text().count('activation: beginning transaction') == starts_before
# A 12-second lease renews naturally; the hook must survive the renewed DNS.
initial_leases = log.read_text().count('state changed new lease')
wait_for(lambda: log.read_text().count('state changed new lease') > initial_leases)
wait_for(lambda: addresses() == dns.PRESETS['Google'])
query('dhcp-renew.example.test', '192.0.2.12')
assert log.read_text().count('activation: beginning transaction') == starts_before
assert run('nmcli', '-g', 'IP4.ADDRESS', 'device', 'show', 'uplink0') == address_before
check('provider switches never restart DHCP; selection survives an actual lease renewal')
resolved.terminate()
resolved.wait(timeout=5)
