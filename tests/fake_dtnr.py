"""A TCP emulator of the DTNR's RS-232 protocol, as the manual describes it.

It stands in for the tuner behind a socket:// bridge. It checks the
integration against the documented protocol, not against real hardware,
except for power, which follows a real unit: a bare X1/X0 switches both
tuners, and T1X1, T1X2, T1X0 and X2 do nothing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import re
import time

FM, AM = 2, 1
FM_STEP, AM_STEP = 2, 10  # 0.2 MHz / 10 kHz, in F-code units


@dataclass
class FakeTuner:
    power: bool = True
    band: int = FM
    freq: dict[int, int] = field(default_factory=lambda: {FM: 1063, AM: 1550})
    preset: int = 0
    presets: dict[int, dict[int, int]] = field(default_factory=lambda: {FM: {}, AM: {}})
    lock: bool = True
    stereo: bool = True
    ready_at: float = 0.0  # still warming up (reports X0) until this time

    @property
    def on(self) -> bool:
        return self.power and time.monotonic() >= self.ready_at

    def status(self, n: int) -> str:
        if not self.on:
            return "X0"
        s = f"{int(self.lock)}{int(self.stereo and self.band == FM)}"
        return f"T{n}B{self.band}P{self.preset:02d}F{self.freq[self.band]:04d}S{s}"


class FakeDTNR:
    """Serve the protocol on 127.0.0.1."""

    def __init__(self, *, push: bool = False) -> None:
        self.tuners = {1: FakeTuner(), 2: FakeTuner(band=AM)}
        self.received: list[str] = []
        self.push = push  # also send status after each command
        self.mute = False  # accept commands but never answer
        self.power_on_delay = 0.0  # seconds after X1 before a tuner is on
        self.connections = 0
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.sockets[0].getsockname()[:2]
        return f"socket://{host}:{port}"

    async def start(self) -> FakeDTNR:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self

    async def stop(self) -> None:
        await self.drop_clients()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def drop_clients(self) -> None:
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()
        await asyncio.sleep(0.05)

    def set_power(self, on: bool) -> None:
        """Switch the unit, both tuners, as X1/X0 do."""
        for tuner in self.tuners.values():
            if on and not tuner.power:
                tuner.ready_at = time.monotonic() + self.power_on_delay
            tuner.power = on

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.add(writer)
        try:
            while line := await reader.readuntil(b"\r"):
                command = line.decode().strip()
                self.received.append(command)
                for reply in self.handle(command):
                    if not self.mute:
                        writer.write(reply.encode() + b"\r")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    def handle(self, command: str) -> list[str]:
        replies: list[str] = []
        ctx: int | None = None
        for letter, digits in re.findall(r"([A-Z])(\d+)", command):
            value = int(digits)
            if letter == "Q":
                replies += [self.tuners[n].status(n) for n in ((1, 2) if value == 3 else (value,))]
                continue
            if letter == "T":
                ctx = value
                continue
            if letter == "X":
                if ctx is None and value in (0, 1):
                    self.set_power(value == 1)
                continue
            if ctx is None:
                continue
            t = self.tuners[ctx]
            if not t.on:
                continue
            if letter == "B":
                if value != t.band:
                    t.band, t.preset = value, 0
            elif letter == "F":
                t.freq[t.band], t.preset = value, 0
            elif letter == "L":
                t.presets[t.band][value] = t.freq[t.band]
            elif letter == "P" and value in t.presets[t.band]:
                t.freq[t.band], t.preset = t.presets[t.band][value], value
            elif letter in "AM":
                step = FM_STEP if t.band == FM else AM_STEP
                step *= 4 if letter == "A" else 1
                t.freq[t.band] += step if value else -step
                t.preset = 0
            elif letter == "N":
                stored = sorted(t.presets[t.band])
                if stored:
                    i = stored.index(t.preset) if t.preset in stored else -1
                    t.preset = stored[(i + (1 if value else -1)) % len(stored)]
                    t.freq[t.band] = t.presets[t.band][t.preset]
        if self.push and ctx is not None:
            replies.append(self.tuners[ctx].status(ctx))
        return replies
