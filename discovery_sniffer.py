#!/usr/bin/env python3
"""
UDP Discovery Sniffer - diagnostic tool for phantom server investigation.

Binds to UDP port 5579 (same as peppy_remote discovery) and logs every
packet received with source IP, source port, and full payload.

Run on Windows while a Volumio server is broadcasting to see if the
Windows machine produces any packets of its own.

Usage:
    python discovery_sniffer.py
    python discovery_sniffer.py --port 5579 --duration 30

Press Ctrl+C to stop.
"""

import json
import socket
import sys
import time
from datetime import datetime


def get_local_ips():
    """Return list of local IP addresses on all interfaces."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    ips.add('127.0.0.1')
    return ips


def main():
    port = 5579
    duration = 60

    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == '--port' and i < len(sys.argv) - 1:
            port = int(sys.argv[i + 1])
        elif arg == '--duration' and i < len(sys.argv) - 1:
            duration = int(sys.argv[i + 1])

    local_hostname = socket.gethostname()
    local_ips = get_local_ips()

    print("=" * 60)
    print(" UDP Discovery Sniffer")
    print("=" * 60)
    print()
    print(f"  Local hostname:  {local_hostname}")
    print(f"  Local IPs:       {', '.join(sorted(local_ips))}")
    print(f"  Listening port:  {port}")
    print(f"  Duration:        {duration}s")
    print()
    print("  Packets from local IPs will be marked ** LOCAL **")
    print()
    print("-" * 60)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)

    try:
        sock.bind(('', port))
        print(f"  Bound to UDP port {port}")
    except OSError as e:
        print(f"  ERROR: Cannot bind to port {port}: {e}")
        print(f"  Is another instance of peppy_remote running?")
        sys.exit(1)

    print(f"  Listening... (Ctrl+C to stop)")
    print()

    count = 0
    local_count = 0
    start = time.time()

    try:
        while (time.time() - start) < duration:
            try:
                data, addr = sock.recvfrom(4096)
                count += 1
                src_ip = addr[0]
                src_port = addr[1]
                ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]

                is_local = src_ip in local_ips
                if is_local:
                    local_count += 1

                tag = "** LOCAL **" if is_local else ""

                print(f"[{ts}] Packet #{count} from {src_ip}:{src_port} ({len(data)} bytes) {tag}")

                # Try to decode as JSON
                try:
                    payload = json.loads(data.decode('utf-8', errors='replace'))
                    service = payload.get('service', '(none)')
                    hostname = payload.get('hostname', '(none)')
                    level_port = payload.get('level_port', '(none)')
                    version = payload.get('version', '(none)')
                    plugin_version = payload.get('plugin_version', '(none)')
                    config_version = payload.get('config_version', '(none)')
                    pkt_type = payload.get('type', '(none)')

                    print(f"         service={service}  hostname={hostname}")
                    print(f"         type={pkt_type}  level_port={level_port}")
                    print(f"         version={version}  plugin_version={plugin_version}")
                    print(f"         config_version={config_version}")

                    # Check for unexpected fields
                    known = {'service', 'hostname', 'level_port', 'spectrum_port',
                             'volumio_port', 'version', 'config_version',
                             'plugin_version', 'active_meter', 'type',
                             'client_version', 'client_port', 'spectrum_client_port'}
                    extra = set(payload.keys()) - known
                    if extra:
                        print(f"         EXTRA FIELDS: {extra}")

                except (json.JSONDecodeError, UnicodeDecodeError):
                    # Raw hex dump for non-JSON packets
                    hex_preview = data[:64].hex(' ')
                    print(f"         RAW (not JSON): {hex_preview}")

                print()

            except socket.timeout:
                continue

    except KeyboardInterrupt:
        print()
        print("-" * 60)

    elapsed = time.time() - start
    sock.close()

    print()
    print("=" * 60)
    print(f"  Total packets:   {count}")
    print(f"  From local IPs:  {local_count}")
    print(f"  Elapsed:         {elapsed:.1f}s")
    print("=" * 60)


if __name__ == '__main__':
    main()
