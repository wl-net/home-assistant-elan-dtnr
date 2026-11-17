"""Protocol tests, checked against the manual's examples."""

import pytest

from custom_components.elan_dtnr.protocol import (
    Band,
    MediaTarget,
    TunerState,
    cmd_band,
    cmd_power,
    cmd_preset_step,
    cmd_query,
    cmd_recall_preset,
    cmd_seek,
    cmd_step,
    cmd_store_preset,
    cmd_tune,
    encode_frequency,
    parse_media_id,
    parse_status,
)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # "Command Structure 1 Examples"
        (cmd_tune(1, Band.FM, 106.3), "T1B2F1063"),
        (cmd_tune(2, Band.AM, 1550), "T2B1F1550"),
        (cmd_recall_preset(2, Band.FM, 2), "T2B2P02"),
        (cmd_store_preset(1, Band.FM, 97.1, 2), "T1B2F0971L02"),
        (cmd_query(1), "Q1"),
        # "Shortcut Commands"
        (cmd_seek(1, False), "T1A0"),
        (cmd_step(2, True), "T2M1"),
        (cmd_preset_step(1, True), "T1N1"),
        (cmd_preset_step(2, False), "T2N0"),
        (cmd_band(1, Band.AM), "T1B1"),
        # Found on a real unit: the manual's X1/X2 with a tuner prefix do
        # nothing; a bare X1/X0 switches the whole unit.
        (cmd_power(True), "X1"),
        (cmd_power(False), "X0"),
    ],
)
def test_commands_match_manual(command: str, expected: str) -> None:
    assert command == expected


def test_frequency_encoding_edges() -> None:
    assert encode_frequency(Band.FM, 87.9) == "0879"
    assert encode_frequency(Band.FM, 107.9) == "1079"
    assert encode_frequency(Band.FM, 101.1) == "1011"  # no float drift
    assert encode_frequency(Band.AM, 530) == "0530"
    with pytest.raises(ValueError):
        encode_frequency(Band.FM, 120)
    with pytest.raises(ValueError):
        encode_frequency(Band.AM, 100)
    with pytest.raises(ValueError):
        cmd_recall_preset(1, Band.FM, 31)


def test_parse_manual_example() -> None:
    # "T1B2P00F1067S00: Tuner 1 is in FM mode, no preset selected. The
    # frequency is 106.7 with no signal and no stereo"
    assert parse_status("T1B2P00F1067S00") == [
        TunerState(
            tuner=1,
            power=True,
            band=Band.FM,
            frequency=106.7,
            preset=None,
            signal_lock=False,
            stereo=False,
        )
    ]


def test_parse_spaced_and_am() -> None:
    (state,) = parse_status("T2 B1 P05 F1550 S10")
    assert state.band is Band.AM
    assert state.frequency == 1550
    assert state.preset == 5
    assert state.frequency_label == "1550 AM"
    assert state.reception == "mono"


def test_parse_off() -> None:
    assert parse_status("X0", expected_tuner=2) == [TunerState(tuner=2, power=False)]
    assert parse_status("X0") == []  # nobody asked, so whose is it?


def test_parse_q3_both_and_with_one_off() -> None:
    both = parse_status("T1B2P01F1011S11T2B1P00F0680S10")
    assert [(s.tuner, s.frequency) for s in both] == [(1, 101.1), (2, 680)]
    assert both[0].reception == "stereo"

    second_off = parse_status("T1B2P01F1011S11X0")
    assert [(s.tuner, s.power) for s in second_off] == [(1, True), (2, False)]

    first_off = parse_status("X0T2B1P00F0680S10", expected_tuner=1)
    assert [(s.tuner, s.power) for s in first_off] == [(1, False), (2, True)]


@pytest.mark.parametrize("line", ["", "OK", "T1A1", "T1X1", "T3B2F1011", "T1B7F1011"])
def test_parse_ignores_non_status(line: str) -> None:
    assert parse_status(line, expected_tuner=1) == []


@pytest.mark.parametrize(
    ("media_id", "expected"),
    [
        ("101.1", MediaTarget(Band.FM, 101.1)),
        ("FM 101.1", MediaTarget(Band.FM, 101.1)),
        ("fm:97.3", MediaTarget(Band.FM, 97.3)),
        ("101.1 MHz", MediaTarget(Band.FM, 101.1)),
        ("1010", MediaTarget(Band.AM, 1010)),
        ("AM 680", MediaTarget(Band.AM, 680)),
        ("680 am", MediaTarget(Band.AM, 680)),
        ("preset 5", MediaTarget(preset=5)),
        ("P12", MediaTarget(preset=12)),
        ("AM preset 3", MediaTarget(Band.AM, preset=3)),
        ("fm", MediaTarget(Band.FM)),
    ],
)
def test_parse_media_id(media_id: str, expected: MediaTarget) -> None:
    assert parse_media_id(media_id) == expected


@pytest.mark.parametrize("media_id", ["300", "FM 1010", "AM 101.1", "preset 31", "kexp", "101.1 AM"])
def test_parse_media_id_rejects(media_id: str) -> None:
    with pytest.raises(ValueError):
        parse_media_id(media_id)
