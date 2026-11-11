"""Opt-in features, switched on by the FEATURES environment variable.

Anything that writes to the tuner's memory, rather than just tuning it, stays
off unless it is listed, e.g. ``FEATURES=store_preset`` in Home Assistant's
container environment. Names are separated by commas or spaces.
"""

from __future__ import annotations

import os
from typing import Final

FEATURES_ENV: Final = "FEATURES"

# Overwrites one of the tuner's 30 presets per band.
STORE_PRESET: Final = "store_preset"


def enabled(feature: str) -> bool:
    """Whether FEATURES lists this feature."""
    listed = os.environ.get(FEATURES_ENV, "").replace(",", " ").lower().split()
    return feature in listed
