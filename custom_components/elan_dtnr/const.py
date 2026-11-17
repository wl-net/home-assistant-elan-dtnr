"""Constants for the ELAN DTNR integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "elan_dtnr"
DEFAULT_NAME: Final = "ELAN DTNR"
MANUFACTURER: Final = "ELAN"
MODEL: Final = "DTNR"

TUNERS: Final = (1, 2)

SCAN_INTERVAL: Final = timedelta(seconds=10)
# Seeking takes a while, so it gets a second refresh.
SEEK_REFRESH_DELAY: Final = 3.0
# How long to wait for the unit to report the new power state after X1/X0,
# and how often to ask meanwhile.
POWER_TIMEOUT: Final = 3.0
POWER_POLL_INTERVAL: Final = 0.5

SERVICE_STORE_PRESET: Final = "store_preset"
ATTR_PRESET: Final = "preset"
