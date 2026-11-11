#!/usr/bin/env python3
"""Talk to an ELAN DTNR directly, to check the wiring and the protocol.

    dtnr_probe.py socket://127.0.0.1:2001            # query both tuners, then prompt
    dtnr_probe.py socket://127.0.0.1:2001 T1X1 Q1    # send commands, show replies
    dtnr_probe.py /dev/ttyUSB0                       # local port (needs pyserial)

Each command is sent with a carriage return; everything the tuner sends back
within a second is printed raw and, where it is a status line, decoded.

Things worth checking on a real unit (the manual leaves them open):
  * T1X2 / T1X1 switch tuner 1 off / on, and Q1 then answers X0 while off.
  * T1L05 *stores* preset 5 (main table) rather than recalling it
    (the manual's shortcut table says the latter).
  * Whether the tuner sends status on its own after a command or a
    front-panel change (the integration uses it if so).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlsplit

_PROTOCOL = Path(__file__).resolve().parent.parent / "custom_components/elan_dtnr/protocol.py"


def _load_parser():
    try:
        spec = importlib.util.spec_from_file_location("dtnr_protocol", _PROTOCOL)
        module = importlib.util.module_from_spec(spec)
        sys.modules["dtnr_protocol"] = module  # dataclasses need it registered
        spec.loader.exec_module(module)
        return module.parse_status
    except Exception:  # noqa: BLE001 - decoding is a nicety
        return None


class Link:
    def __init__(self, port: str) -> None:
        parts = urlsplit(port)
        if parts.scheme in ("socket", "tcp"):
            self._sock = socket.create_connection((parts.hostname, parts.port), timeout=5)
            self._sock.settimeout(0.1)
            self._serial = None
        else:
            import serial  # pyserial

            self._serial = serial.Serial(port, 19200, timeout=0.1)
            self._sock = None

    def write(self, data: bytes) -> None:
        if self._sock:
            self._sock.sendall(data)
        else:
            self._serial.write(data)

    def read(self) -> bytes:
        if self._serial:
            return self._serial.read(256)
        try:
            data = self._sock.recv(256)
        except socket.timeout:  # an alias of TimeoutError from Python 3.10
            return b""
        if not data:
            raise ConnectionError("bridge closed the connection")
        return data


def exchange(link: Link, command: str, parse) -> None:
    link.write(command.encode("ascii") + b"\r")
    received = b""
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if chunk := link.read():
            received += chunk
            deadline = time.monotonic() + 0.3  # wait a little after the last byte
    print(f"> {command}")
    if not received:
        print("  (no reply)")
        return
    for line in received.replace(b"\n", b"\r").split(b"\r"):
        if not line.strip():
            continue
        text = line.decode("ascii", errors="replace")
        decoded = ""
        if parse:
            tuner = int(command[1]) if command[:1] == "Q" and command[1:2] in "12" else None
            if states := parse(text, expected_tuner=tuner):
                decoded = "   = " + "; ".join(map(repr, states))
        print(f"< {text!r}{decoded}")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    link = Link(sys.argv[1])
    parse = _load_parser()
    commands = sys.argv[2:] or ["Q1", "Q2"]
    for command in commands:
        exchange(link, command.strip().upper(), parse)
    if len(sys.argv) > 2:
        return 0
    print("Type commands (e.g. T1B2F1011, T1X2, Q1); Ctrl-D to quit.")
    for line in sys.stdin:
        if command := line.strip().upper():
            exchange(link, command, parse)
    return 0


if __name__ == "__main__":
    sys.exit(main())
