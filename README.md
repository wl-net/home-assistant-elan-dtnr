# ELAN DTNR for Home Assistant

A Home Assistant custom integration for the **ELAN DTNR Digital Dual Tuner**:
two independent AM/FM tuners in one box, controlled over RS-232. It talks to the
tuner through a local serial port, or through a TCP serial bridge
(`socket://host:port`), such as an stunnel client, ser2net or an ESP serial
server.

The unit is a device with its power switch, and each tuner a device of its own
under it:

| Entity | What it does |
| --- | --- |
| `switch.<name>_power` | The unit's power. The DTNR has no per-tuner power: on and off switch both tuners. |
| `media_player.<name>_tuner_N` | **source** = band (AM/FM); next/previous = preset up/down; `play_media` tunes (see below). Playing while the unit is on, off while it's off. Title shows the station (`106.3 FM`), channel shows the preset. |
| `number.…_fm_frequency` / `number.…_am_frequency` | Current frequency on that band (last-known while on the other band). Setting one switches band and tunes. |
| `select.…_preset` | Recall preset 1–30 on the current band. |
| `button.…_seek_up/down`, `button.…_step_up/down` | Seek to the next station, or step one channel (0.2 MHz FM). |
| `sensor.…_reception` | `no_signal`, `mono` or `stereo` (diagnostic). |

There's also an action, `elan_dtnr.store_preset`, which stores the station a
tuner is on into preset 1–30. It overwrites the tuner's preset memory, so it's
off unless switched on with `FEATURES` (see [Optional features](#optional-features)).

Any command sent while the unit is off switches it on first, which brings both
tuners on, and waits until it reports itself on. Storing a preset is the
exception: it's refused while the unit is off. The integration polls every
10 s and re-reads a tuner right after each command. A real DTNR doesn't report
front-panel changes by itself, so they show up at the next poll, and a seek
shows only where it stopped. (If a unit does send status lines on its own, the
integration uses them.)

## Connecting the tuner

The DTNR's DB9 is **female**, wired as DCE (pin 2 TX out, pin 3 RX in, pin 5
ground). Use a **straight-through** cable from a USB-serial adapter, not a
null-modem cable. It runs at **19200 baud, 8N1**, with no flow control.

### Over stunnel (the same setup as the Monoprice amp)

```
DTNR ──RS-232──▶ USB adapter on the AV Pi
                 socat OPENSSL-LISTEN:<port>,…  ⟵ TLS, pinned client cert
                       │
Home Assistant host:   stunnel client  (accept 127.0.0.1:2001)
                       │
                 Home Assistant → socket://127.0.0.1:2001
```

The DTNR needs a bridge of its own: a second socat service on the Pi for its
adapter (by `/dev/serial/by-id/…` name, `b19200`, its own TLS port), and a
second `[service]` in the HA-side stunnel client listening on another local
port. Then add the integration with `socket://127.0.0.1:<that port>`.

The client was written for this path, based on what the Monoprice amp's
patched pymonoprice needed:

- TCP keepalive on the socket, so a half-open link is noticed.
- One connection at a time; the old one is closed before reconnecting, since
  a forking socat would otherwise hand the serial port to two clients.
- A command that finds the link dead reconnects and is retried once.
- Two unanswered queries in a row recycle the connection. stunnel keeps the
  local socket open even when its TLS leg to the Pi is dead, so a timeout is
  the only symptom.
- If the bridge isn't up yet at startup, setup fails and Home Assistant
  retries it later.

### ser2net (if you don't need TLS)

```yaml
connection: &dtnr
  accepter: tcp,4001
  enable: on
  connector: serialdev,/dev/serial/by-id/usb-…,19200n81,local
```

## Installing

Copy `custom_components/elan_dtnr` into Home Assistant's config directory
(`/srv/home-assistant/config/custom_components/elan_dtnr` on the netops host)
and restart Home Assistant. Or add this repository to HACS as a custom
repository of type *Integration*.

Then go to **Settings → Devices & services → Add integration → ELAN DTNR**,
and pick a serial port or type the `socket://` URL. Setup sends `Q1` and
expects an answer.

It needs Home Assistant 2026.10 or newer. Local serial ports go through
`serialx`, which Home Assistant already ships.

### Optional features

Anything that writes to the tuner's memory, rather than just tuning it, is off
by default. Turn features on by listing them, separated by commas or spaces,
in the `FEATURES` environment variable of the Home Assistant container. Then
restart Home Assistant.

```yaml
services:
  homeassistant:
    environment:
      FEATURES: store_preset
```

| Feature | Enables |
| --- | --- |
| `store_preset` | the `elan_dtnr.store_preset` action |

## Tuning from automations

`media_player.play_media` accepts `content_type: channel` and an id like:

| id | means |
| --- | --- |
| `101.1`, `FM 101.1`, `101.1 MHz` | FM 101.1 (a bare number ≤ 108 is FM) |
| `1090`, `AM 1090`, `1090 am` | AM 1090 kHz (a bare number ≥ 520 is AM) |
| `preset 5`, `P5`, `AM preset 3` | recall a preset (current band unless given) |
| `AM`, `FM` | switch band, back to its last station |

```yaml
- alias: Morning news in the kitchen
  triggers:
    - trigger: time
      at: "06:30:00"
  actions:
    - action: media_player.play_media
      target:
        entity_id: media_player.elan_dtnr_tuner_1
      data:
        media_content_type: channel
        media_content_id: "FM 94.9"
```

With `FEATURES: store_preset` set:

```yaml
action: elan_dtnr.store_preset
target:
  entity_id: media_player.elan_dtnr_tuner_1
data:
  preset: 5
```

The DTNR only makes audio. Volume and zones are up to whatever its outputs
feed, such as the Monoprice amp's inputs.

## Checking the link: `tools/dtnr_probe.py`

A standalone script (pyserial only for local ports) that sends raw commands
and prints the replies, decoded where possible:

```sh
python3 tools/dtnr_probe.py socket://127.0.0.1:2001            # Q1, Q2, then a prompt
python3 tools/dtnr_probe.py socket://127.0.0.1:2001 X0 Q1 Q2   # the unit off, then ask both
```

Disable the integration first (the ⋮ menu on its entry, in Devices &
services): a forking bridge would hand the serial port to both, and they'd
take each other's replies.

## Protocol notes and open questions

The protocol is from the DTNR Installation & User Manual, "RS232 Protocol":
`T<tuner>B<band>…<CR>` commands, `Q1`/`Q2` queries, and replies like
`T1B2P00F1067S00` or `X0` when the tuner is off. FM is 4 digits in 100 kHz
units, AM is 4 digits in kHz.

Checked on a real unit (November 2026):

- **Power is the unit's.** A bare `X1` switches both tuners on and `X0` both
  off; `Q1` and `Q2` then both answer `X0`. `T1X1`, `T1X2`, `T1X0` and the
  manual's `X2` do nothing.
- **No unsolicited status.** Front-panel changes show up only when a tuner is
  asked.

Still open:

- **`L`.** The parameter table and the example `T1B2F0971L02` say `L`
  *stores* a preset. The shortcut table says `T1L15` *selects* preset 15. The
  integration uses `P` to recall and `F…L` to store, following the parameter
  table.

## Development

```sh
python3.14 -m venv .venv
.venv/bin/pip install pytest-homeassistant-custom-component serialx
.venv/bin/pytest
```

The tests run the integration end to end against `tests/fake_dtnr.py`, a TCP
emulator of the documented protocol, with power as a real unit does it. That
covers config flow, entities, actions, reconnects and stunnel-style silent
links. It doesn't stand in for the real tuner.
