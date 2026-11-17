"""Pure protocol helpers for the ELAN DTNR digital dual tuner.

No I/O and no Home Assistant imports, so this module can be loaded standalone
(the probe tool and the unit tests do exactly that).

Wire format (DTNR Installation Manual, "RS232 Protocol"):

* 19200 baud, 8 data bits, no parity, 1 stop bit.
* Commands are ASCII ``<letter><digits>`` tokens with no separators, terminated
  by a carriage return, e.g. ``T1B2F1063\r`` (tuner 1, FM, 106.3 MHz).
* ``Q1``/``Q2`` query a tuner. The reply looks like ``T1B2P00F1067S00``, or
  ``X0`` when the tuner is switched off.
* ``X1``/``X0`` switch the whole unit, both tuners, on and off; there's no
  per-tuner power. Found on a real unit: ``T1X1``, ``T1X2``, ``T1X0`` and the
  manual's ``X2`` do nothing.
* FM frequencies are 4 digits in 100 kHz units (``F0971`` = 97.1 MHz). AM
  frequencies are 4 digits in kHz (``F1550`` = 1550 kHz).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import re

BAUD_RATE = 19200
TERMINATOR = "\r"

FM_MIN_MHZ = 87.5
FM_MAX_MHZ = 108.0
FM_STEP_MHZ = 0.1
AM_MIN_KHZ = 520
AM_MAX_KHZ = 1710
AM_STEP_KHZ = 10
PRESET_MIN = 1
PRESET_MAX = 30


class Band(IntEnum):
    """Tuner band, using the DTNR's own ``B`` token values."""

    AM = 1
    FM = 2


class Reception:
    """String constants for signal/stereo status."""

    NO_SIGNAL = "no_signal"
    MONO = "mono"
    STEREO = "stereo"


@dataclass(frozen=True, slots=True)
class TunerState:
    """State of one of the two tuners, as reported by a query."""

    tuner: int
    power: bool
    band: Band | None = None
    frequency: float | None = None  # MHz for FM, kHz for AM
    preset: int | None = None  # None when no preset is selected (P00)
    signal_lock: bool | None = None
    stereo: bool | None = None

    @property
    def frequency_label(self) -> str | None:
        """Human-readable station, e.g. ``106.3 FM`` or ``1550 AM``."""
        if self.band is None or self.frequency is None:
            return None
        return format_frequency(self.band, self.frequency)

    @property
    def reception(self) -> str | None:
        """Combined lock/stereo status."""
        if not self.power or self.signal_lock is None:
            return None
        if not self.signal_lock:
            return Reception.NO_SIGNAL
        return Reception.STEREO if self.stereo else Reception.MONO


# --------------------------------------------------------------------------
# Frequencies
# --------------------------------------------------------------------------


def encode_frequency(band: Band, frequency: float) -> str:
    """Encode a frequency (MHz for FM, kHz for AM) as the 4-digit ``F`` value."""
    if band is Band.FM:
        code = round(frequency * 10)
        low, high = round(FM_MIN_MHZ * 10), round(FM_MAX_MHZ * 10)
        if not low <= code <= high:
            raise ValueError(
                f"FM frequency {frequency} MHz is outside {FM_MIN_MHZ}-{FM_MAX_MHZ} MHz"
            )
    else:
        code = round(frequency)
        if not AM_MIN_KHZ <= code <= AM_MAX_KHZ:
            raise ValueError(
                f"AM frequency {frequency} kHz is outside {AM_MIN_KHZ}-{AM_MAX_KHZ} kHz"
            )
    return f"{code:04d}"


def decode_frequency(band: Band, code: int) -> float:
    """Decode the numeric ``F`` value into MHz (FM) or kHz (AM)."""
    if band is Band.FM:
        return code / 10
    return float(code)


def format_frequency(band: Band, frequency: float) -> str:
    """Format a frequency for display."""
    if band is Band.FM:
        return f"{frequency:.1f} FM"
    return f"{round(frequency)} AM"


# --------------------------------------------------------------------------
# Commands (returned without the trailing carriage return)
# --------------------------------------------------------------------------


def _check_tuner(tuner: int) -> None:
    if tuner not in (1, 2):
        raise ValueError(f"Tuner must be 1 or 2, not {tuner}")


def _check_preset(preset: int) -> None:
    if not PRESET_MIN <= preset <= PRESET_MAX:
        raise ValueError(f"Preset must be {PRESET_MIN}-{PRESET_MAX}, not {preset}")


def cmd_query(tuner: int) -> str:
    """Query a tuner's state."""
    _check_tuner(tuner)
    return f"Q{tuner}"


def cmd_power(on: bool) -> str:
    """Switch the whole unit, both tuners, on (X1) or off (X0)."""
    return "X1" if on else "X0"


def cmd_band(tuner: int, band: Band) -> str:
    """Switch band; the tuner returns to the last station used on that band."""
    _check_tuner(tuner)
    return f"T{tuner}B{band.value}"


def cmd_tune(tuner: int, band: Band, frequency: float) -> str:
    """Tune directly to a frequency."""
    _check_tuner(tuner)
    return f"T{tuner}B{band.value}F{encode_frequency(band, frequency)}"


def cmd_recall_preset(tuner: int, band: Band, preset: int) -> str:
    """Recall a stored preset on the given band."""
    _check_tuner(tuner)
    _check_preset(preset)
    return f"T{tuner}B{band.value}P{preset:02d}"


def cmd_store_preset(tuner: int, band: Band, frequency: float, preset: int) -> str:
    """Store a frequency into a preset slot (manual example: ``T1B2F0971L02``)."""
    _check_tuner(tuner)
    _check_preset(preset)
    return f"T{tuner}B{band.value}F{encode_frequency(band, frequency)}L{preset:02d}"


def cmd_seek(tuner: int, up: bool) -> str:
    """Seek to the next receivable station."""
    _check_tuner(tuner)
    return f"T{tuner}A{1 if up else 0}"


def cmd_step(tuner: int, up: bool) -> str:
    """Step the frequency by one channel (0.2 MHz on FM)."""
    _check_tuner(tuner)
    return f"T{tuner}M{1 if up else 0}"


def cmd_preset_step(tuner: int, up: bool) -> str:
    """Move to the next/previous preset."""
    _check_tuner(tuner)
    return f"T{tuner}N{1 if up else 0}"


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"([A-Z])\s*(\d+)")


def parse_status(line: str, expected_tuner: int | None = None) -> list[TunerState]:
    """Parse a status line into zero or more tuner states.

    Handles ``T1B2P00F1067S00``, the spaced form shown in the manual's table,
    a bare ``X0`` (assigned to ``expected_tuner``), and the two-tuner reply to
    ``Q3``. Lines without a band and frequency, such as command echoes,
    produce no state.
    """
    states: list[TunerState] = []
    tuner: int | None = None
    fields: dict[str, str] = {}

    def emit() -> None:
        target = tuner if tuner is not None else expected_tuner
        if target not in (1, 2) or "B" not in fields or "F" not in fields:
            return
        try:
            band = Band(int(fields["B"]))
        except ValueError:
            return
        preset = int(fields.get("P", "0")) or None
        lock = stereo = None
        if (status := fields.get("S")) is not None:
            status = status.zfill(2)
            lock, stereo = status[0] == "1", status[1] == "1"
        states.append(
            TunerState(
                tuner=target,
                power=True,
                band=band,
                frequency=decode_frequency(band, int(fields["F"])),
                preset=preset,
                signal_lock=lock,
                stereo=stereo,
            )
        )

    for letter, digits in _TOKEN_RE.findall(line.upper()):
        if letter == "T":
            emit()
            tuner, fields = int(digits), {}
        elif letter == "X" and int(digits) == 0:
            if fields:
                # A complete block came first, so this X0 is for the next tuner.
                emit()
                tuner = tuner + 1 if tuner is not None else None
                fields = {}
            target = tuner if tuner is not None else expected_tuner
            if target in (1, 2):
                states.append(TunerState(tuner=target, power=False))
            tuner, fields = None, {}
            expected_tuner = None
        elif letter in "BFPS":
            fields[letter] = digits
    emit()
    return states


# --------------------------------------------------------------------------
# Free-form station strings (used for media_player.play_media)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MediaTarget:
    """What a ``play_media`` id asks for."""

    band: Band | None = None
    frequency: float | None = None
    preset: int | None = None


_BAND_ONLY_RE = re.compile(r"^(am|fm)$")
_PRESET_RE = re.compile(r"^(?:(am|fm)[\s:]*)?(?:preset|p)[\s:#]*(\d{1,2})$")
_STATION_RE = re.compile(
    r"^(?:(am|fm)[\s:]*)?(\d+(?:\.\d+)?)\s*(am|fm|mhz|khz)?$"
)


def parse_media_id(media_id: str) -> MediaTarget:
    """Interpret strings like ``101.1``, ``FM 101.1``, ``1010 AM``, ``preset 5``.

    A bare number up to 108 is treated as FM MHz; a bare number from 520 is
    treated as AM kHz.
    """
    text = media_id.strip().lower()

    if match := _BAND_ONLY_RE.match(text):
        return MediaTarget(band=Band[match[1].upper()])

    if match := _PRESET_RE.match(text):
        preset = int(match[2])
        _check_preset(preset)
        band = Band[match[1].upper()] if match[1] else None
        return MediaTarget(band=band, preset=preset)

    if match := _STATION_RE.match(text):
        prefix, number, suffix = match[1], float(match[2]), match[3]
        band: Band | None = None
        if prefix:
            band = Band[prefix.upper()]
        if suffix:
            suffix_band = Band.FM if suffix in ("fm", "mhz") else Band.AM
            if band is not None and band is not suffix_band:
                raise ValueError(f"Conflicting band in {media_id!r}")
            band = suffix_band
        if band is None:
            if number <= FM_MAX_MHZ:
                band = Band.FM
            elif number >= AM_MIN_KHZ:
                band = Band.AM
            else:
                raise ValueError(f"Cannot tell whether {media_id!r} is AM or FM")
        encode_frequency(band, number)  # range check
        return MediaTarget(band=band, frequency=number)

    raise ValueError(
        f"Unrecognised station {media_id!r}; use e.g. '101.1', 'AM 1010' or 'preset 5'"
    )
