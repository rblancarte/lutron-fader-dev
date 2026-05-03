#!/usr/bin/env python3
"""
Listen for unsolicited zone updates from the Lutron hub.

Usage:
    python3 monitor_zones.py                  # monitor all zones
    python3 monitor_zones.py 25 28            # filter to specific zone IDs

Manipulate lights via app, keypad, or the Lutron app and watch what arrives.
Press Ctrl+C to stop.
"""
import socket
import sys
import time
from datetime import datetime

HOST = "10.0.1.14"
PORT = 23
USERNAME = "lutron"
PASSWORD = "integration"

LOCAL_IP = "10.0.1.227"


def parse_output(line: str) -> dict | None:
    try:
        parts = line.strip().split(',')
        if len(parts) >= 4 and parts[0] == '~OUTPUT':
            return {
                'zone': int(parts[1]),
                'action': int(parts[2]),
                'level': float(parts[3]),
            }
    except (ValueError, IndexError):
        pass
    return None


def read_until(sock: socket.socket, token: bytes, timeout: float = 5.0) -> bytes:
    buf = b""
    sock.settimeout(timeout)
    deadline = time.time() + timeout
    while token not in buf:
        if time.time() > deadline:
            raise TimeoutError(f"Timed out waiting for {token!r}, got: {buf!r}")
        try:
            chunk = sock.recv(256)
            if not chunk:
                raise ConnectionError(f"Connection closed waiting for {token!r}")
            buf += chunk
        except socket.timeout:
            raise TimeoutError(f"Timed out waiting for {token!r}")
    return buf


def monitor(zone_filter: list[int]):
    print(f"Connecting to {HOST}:{PORT}...")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((LOCAL_IP, 0))
    sock.connect((HOST, PORT))
    print("TCP connected.")

    try:
        read_until(sock, b"login:")
        sock.sendall(f"{USERNAME}\r\n".encode())

        read_until(sock, b"password:")
        sock.sendall(f"{PASSWORD}\r\n".encode())

        read_until(sock, b"GNET>")
        print("Authenticated.\n")

        print("Enabling zone level monitoring (#MONITORING,5,1)...")
        sock.sendall(b"#MONITORING,5,1\r\n")
        time.sleep(0.5)

        filter_msg = f"zones {zone_filter}" if zone_filter else "all zones"
        print(f"Listening for updates on {filter_msg}. Manipulate lights now.\n")
        print(f"{'Time':<12} {'Zone':<8} {'Action':<10} {'Level':<10}  Raw")
        print("-" * 60)

        sock.settimeout(30.0)
        buf = ""

        while True:
            try:
                data = sock.recv(1024)
            except socket.timeout:
                print("  (no activity for 30s, still listening...)")
                continue

            if not data:
                print("Connection closed by hub.")
                break

            buf += data.decode(errors='replace')

            while '\n' in buf:
                line, buf = buf.split('\n', 1)
                line = line.strip()
                if not line:
                    continue

                parsed = parse_output(line)

                if parsed:
                    if zone_filter and parsed['zone'] not in zone_filter:
                        continue
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    print(f"{ts:<12} {parsed['zone']:<8} {parsed['action']:<10} {parsed['level']:<10.2f}  {line}")
                else:
                    if line not in ('GNET>', ''):
                        print(f"             {'':8} {'':10} {'':10}  [{line}]")

    except KeyboardInterrupt:
        print("\n\nStopped.")
    finally:
        sock.close()


if __name__ == "__main__":
    zones = [int(z) for z in sys.argv[1:]] if len(sys.argv) > 1 else []
    monitor(zones)
