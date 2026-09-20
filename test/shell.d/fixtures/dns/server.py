"""Tiny authoritative fixture; each upstream gives a distinguishable answer."""
import json
import selectors
import socket
import struct

sel = selectors.DefaultSelector()
addresses = [('192.0.2.1', '192.0.2.10'), ('1.1.1.1', '192.0.2.11'),
             ('1.0.0.1', '192.0.2.11'), ('8.8.8.8', '192.0.2.12'),
             ('8.8.4.4', '192.0.2.12'), ('192.0.2.53', '192.0.2.13'),
             ('2001:db8::53', '192.0.2.14'), ('198.51.100.1', '192.0.2.15')]
for address, answer in addresses:
  family = socket.AF_INET6 if ':' in address else socket.AF_INET
  sock = socket.socket(family, socket.SOCK_DGRAM)
  sock.bind((address, 53))
  sel.register(sock, selectors.EVENT_READ, (address, answer))
while True:
  for event, _ in sel.select():
    sock = event.fileobj
    data, client = sock.recvfrom(65535)
    pos, labels = 12, []
    while data[pos]:
      size = data[pos]
      labels.append(data[pos + 1:pos + 1 + size].decode('ascii'))
      pos += size + 1
    pos += 1
    kind, klass = struct.unpack('!HH', data[pos:pos + 4])
    end = pos + 4
    address, answer = event.data
    print(json.dumps({'server': address, 'name': '.'.join(labels), 'type': kind}), flush=True)
    rdata = socket.inet_aton(answer) if kind == 1 else socket.inet_pton(socket.AF_INET6, '2001:db8::99')
    count = int(kind in (1, 28))
    response = data[:2] + struct.pack('!HHHHH', 0x8180, 1, count, 0, 0) + data[12:end]
    if count:
      response += b'\xc0\x0c' + struct.pack('!HHIH', kind, klass, 0, len(rdata)) + rdata
    sock.sendto(response, client)
