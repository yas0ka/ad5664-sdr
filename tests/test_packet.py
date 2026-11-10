from pathlib import Path

import pytest

import ad5664

DATA = Path(__file__).parent / 'data'

# Checked against the sensor's own display (2026-11-09 21:32:40): 28.6 C, 50 %
EXAMPLE = '2A F7 00 32 12 03 00 00 00 00 EC 00 8D 00 C7 03 AB'


def load_packets():
    lines = (DATA / 'packets.txt').read_text().splitlines()
    return [bytes.fromhex(l) for l in lines if l and not l.startswith('#')]


def test_example_matches_display():
    r = ad5664.parse_hex(EXAMPLE)
    assert r.channel == 1
    assert r.sensor_id == 0x2AF7
    assert r.temperature == 28.6
    assert r.humidity == 50
    assert r.wbgt == 23.6
    assert r.vh == 14.1
    assert r.next_tx_s == 121.0
    assert not r.fast_mode


def test_all_recorded_packets_have_valid_checksum():
    packets = load_packets()
    assert len(packets) >= 40
    for p in packets:
        assert ad5664.verify(p), p.hex(' ')


def test_recorded_packets_are_plausible():
    for p in load_packets():
        r = ad5664.parse(p)
        assert 1 <= r.channel <= 5
        assert 0 <= r.humidity <= 100
        assert -10 < r.temperature < 40
        # every channel transmits at its own interval: CH1 121 s ... CH5 129 s
        assert abs(r.next_tx_s - (119 + 2 * r.channel)) <= 0.25


def test_bad_checksum_rejected():
    p = bytearray.fromhex(EXAMPLE)
    p[3] ^= 0x01
    assert not ad5664.verify(p)
    with pytest.raises(ValueError):
        ad5664.parse(p)
    assert ad5664.parse(p, check=False).humidity == 51


def test_short_packet_rejected():
    with pytest.raises(ValueError):
        ad5664.parse(bytes(16))


def test_to_dict():
    d = ad5664.parse_hex(EXAMPLE).to_dict()
    assert d['sensor_id'] == '2AF7'
    assert d['raw'].startswith('2A F7')
