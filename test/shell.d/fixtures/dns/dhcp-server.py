"""Serve short leases to the real NM DHCP client on the private veth pair."""
import socket
import struct

server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
server.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'server0\0')
server.bind(('', 67))
address = socket.inet_aton('192.0.2.100')
router = socket.inet_aton('192.0.2.1')


def option(code, data):
  return bytes([code, len(data)]) + data


while True:
  request, _ = server.recvfrom(4096)
  options, pos = {}, 240
  while pos < len(request) and request[pos] != 255:
    code = request[pos]
    pos += 1
    if code == 0:
      continue
    length = request[pos]
    pos += 1
    options[code] = request[pos:pos + length]
    pos += length
  kind = options.get(53)
  if kind not in (b'\x01', b'\x03'):
    continue
  print('DISCOVER' if kind == b'\x01' else 'REQUEST', flush=True)
  response = bytearray(request[:240])
  response[0] = 2
  response[16:20] = address
  response[20:24] = router
  response += option(53, b'\x02' if kind == b'\x01' else b'\x05')
  response += option(54, router) + option(1, socket.inet_aton('255.255.255.0'))
  response += option(3, router) + option(6, router)
  for code, seconds in ((51, 12), (58, 4), (59, 9)):
    response += option(code, struct.pack('!I', seconds))
  response += b'\xff'
  destination = socket.inet_ntoa(request[12:16]) if any(request[12:16]) else '255.255.255.255'
  server.sendto(response, (destination, 68))
