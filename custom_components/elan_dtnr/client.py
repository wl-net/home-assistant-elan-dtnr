"""Asyncio connection to an ELAN DTNR, over a local serial port or a TCP bridge.

Home Assistant-agnostic. Connection handling follows what the Monoprice
amplifier needed behind a socat/stunnel bridge:

* ``socket://host:port`` connects over TCP with keepalive, so a half-open
  link (bridge restarted, network blip) is noticed instead of writes vanishing.
* One connection at a time; the old one is closed before a new one opens (a
  forking socat bridge would otherwise give two connections the same port).
* A command that finds the link dead reconnects and is retried once.
* A single unanswered query is just a timeout. Consecutive ones on a TCP link
  recycle the connection: a local stunnel client keeps the local socket open
  even when its TLS leg to the bridge is dead.
* Failing to connect raises :class:`DTNRConnectionError`, which Home Assistant
  turns into a setup retry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import logging
import re
import socket
from typing import TypeVar
from urllib.parse import urlsplit

from .protocol import BAUD_RATE, TERMINATOR, TunerState, cmd_query, parse_status

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
QUERY_TIMEOUT = 2.0
COMMAND_GAP = 0.1  # pause after each command so the tuner isn't overrun
TIMEOUT_RECONNECT_STREAK = 2  # consecutive TCP timeouts before reconnecting
KEEPALIVE_IDLE = 30
KEEPALIVE_INTERVAL = 10
KEEPALIVE_COUNT = 3
MAX_LINE = 512

TCP_SCHEMES = ("socket", "tcp")

_T = TypeVar("_T")


class DTNRError(Exception):
    """Base error for the DTNR client."""


class DTNRConnectionError(DTNRError):
    """The port could not be opened, or the connection was lost."""


class DTNRTimeoutError(DTNRError):
    """The tuner did not answer in time."""


def is_tcp_url(port: str) -> bool:
    """Whether ``port`` names a TCP bridge rather than a local serial device."""
    return urlsplit(port).scheme.lower() in TCP_SCHEMES


def _split_tcp_url(port: str) -> tuple[str, int]:
    parts = urlsplit(port)
    if not parts.hostname or parts.port is None:
        raise ValueError(f"Expected socket://HOST:PORT, got {port!r}")
    return parts.hostname, parts.port


def _enable_keepalive(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for option, value in (
            ("TCP_KEEPIDLE", KEEPALIVE_IDLE),
            ("TCP_KEEPALIVE", KEEPALIVE_IDLE),  # macOS name for KEEPIDLE
            ("TCP_KEEPINTVL", KEEPALIVE_INTERVAL),
            ("TCP_KEEPCNT", KEEPALIVE_COUNT),
        ):
            if hasattr(socket, option):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, option), value)
    except OSError as err:
        _LOGGER.debug("Could not enable TCP keepalive: %s", err)


async def _open_stream(
    port: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if is_tcp_url(port):
        host, tcp_port = _split_tcp_url(port)
        reader, writer = await asyncio.open_connection(host, tcp_port)
        _enable_keepalive(writer.get_extra_info("socket"))
        return reader, writer

    import serialx  # noqa: PLC0415 - only needed for local serial ports

    try:
        return await serialx.open_serial_connection(url=port, baudrate=BAUD_RATE)
    except serialx.SerialException as err:
        raise OSError(str(err)) from err


class DTNRClient:
    """Talks to one DTNR (both of its tuners)."""

    def __init__(self, port: str) -> None:
        """Initialise; nothing is opened until the first command."""
        self.port = port
        self.on_state: Callable[[TunerState], None] | None = None
        self._is_tcp = is_tcp_url(port)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._pending: tuple[int, asyncio.Future[TunerState]] | None = None
        self._timeout_streak = 0

    @property
    def connected(self) -> bool:
        """Whether a connection is open and its reader is running."""
        return (
            self._writer is not None
            and self._read_task is not None
            and not self._read_task.done()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the connection if it isn't already."""
        async with self._lock:
            await self._connect()

    async def disconnect(self) -> None:
        """Close the connection."""
        async with self._lock:
            await self._close()

    async def send(self, command: str) -> None:
        """Send one command (without terminator)."""
        async with self._lock:
            await self._with_reconnect(lambda: self._write(command))
            await asyncio.sleep(COMMAND_GAP)

    async def query(self, tuner: int) -> TunerState:
        """Query one tuner and return its state."""
        async with self._lock:
            try:
                state = await self._with_reconnect(lambda: self._query(tuner))
            except DTNRTimeoutError:
                self._timeout_streak += 1
                if self._is_tcp and self._timeout_streak >= TIMEOUT_RECONNECT_STREAK:
                    _LOGGER.info(
                        "%d unanswered queries on %s; reconnecting and retrying once",
                        self._timeout_streak,
                        self.port,
                    )
                    self._timeout_streak = 0
                    await self._close()
                    await self._connect()
                    return await self._query(tuner)
                raise
            self._timeout_streak = 0
            return state

    # ------------------------------------------------------------------
    # Internals (call with the lock held)
    # ------------------------------------------------------------------

    async def _with_reconnect(self, operation: Callable[[], Awaitable[_T]]) -> _T:
        await self._connect()
        try:
            return await operation()
        except DTNRConnectionError as err:
            _LOGGER.info("Connection to %s lost (%s); reconnecting", self.port, err)
            await self._close()
            await self._connect()
            return await operation()

    async def _connect(self) -> None:
        if self.connected:
            return
        await self._close()
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                self._reader, self._writer = await _open_stream(self.port)
        except (OSError, TimeoutError, ValueError) as err:
            raise DTNRConnectionError(f"Could not open {self.port}: {err}") from err
        self._read_task = asyncio.create_task(
            self._read_loop(self._reader), name=f"elan_dtnr reader {self.port}"
        )
        _LOGGER.debug("Connected to %s", self.port)

    async def _close(self) -> None:
        task, self._read_task = self._read_task, None
        writer, self._writer = self._writer, None
        self._reader = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                async with asyncio.timeout(2):
                    await writer.wait_closed()
            _LOGGER.debug("Closed connection to %s", self.port)

    async def _write(self, command: str) -> None:
        if self._writer is None or not self.connected:
            raise DTNRConnectionError("Not connected")
        _LOGGER.debug("%s > %s", self.port, command)
        try:
            self._writer.write((command + TERMINATOR).encode("ascii"))
            await self._writer.drain()
        except OSError as err:
            raise DTNRConnectionError(f"Write failed: {err}") from err

    async def _query(self, tuner: int) -> TunerState:
        future: asyncio.Future[TunerState] = asyncio.get_running_loop().create_future()
        self._pending = (tuner, future)
        try:
            await self._write(cmd_query(tuner))
            async with asyncio.timeout(QUERY_TIMEOUT):
                return await future
        except TimeoutError as err:
            raise DTNRTimeoutError(f"No answer from tuner {tuner} on {self.port}") from err
        finally:
            self._pending = None

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        buffer = b""
        error: Exception = DTNRConnectionError("Connection closed by peer")
        try:
            while data := await reader.read(256):
                buffer += data
                *lines, buffer = re.split(rb"[\r\n]+", buffer)
                for raw in lines:
                    if line := raw.decode("ascii", errors="replace").strip():
                        self._handle_line(line)
                if len(buffer) > MAX_LINE:
                    _LOGGER.debug("Discarding unterminated input: %r", buffer)
                    buffer = b""
        except OSError as err:
            error = DTNRConnectionError(f"Read failed: {err}")
        finally:
            if self._pending is not None and not self._pending[1].done():
                self._pending[1].set_exception(error)
        _LOGGER.debug("Reader for %s stopped: %s", self.port, error)

    def _handle_line(self, line: str) -> None:
        _LOGGER.debug("%s < %s", self.port, line)
        pending_tuner = self._pending[0] if self._pending else None
        states = parse_status(line, expected_tuner=pending_tuner)
        for state in states:
            if (
                self._pending is not None
                and state.tuner == self._pending[0]
                and not self._pending[1].done()
            ):
                self._pending[1].set_result(state)
            elif self.on_state is not None:
                self.on_state(state)
