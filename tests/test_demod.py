from pathlib import Path

import numpy as np

from ad5664 import parse_hex
from ad5664.demod import BurstDetector, cu8_to_complex, decode_burst
from ad5664.receiver import read_file

DATA = Path(__file__).parent / 'data'
FS = 2_400_000
PACKET = bytes.fromhex('2A F7 00 32 12 03 00 00 00 00 EC 00 8D 00 C7 03 AB')


def synth_burst(packet, fs=FS, offset_hz=-300e3, dev=11e3, rate=10000.0, noise=0.05, seed=1):
    """Make a burst like the real sensor: 3 packets, ~11-16 ms apart, 2-FSK."""
    rng = np.random.default_rng(seed)
    bits = '10' * 32 + '0010110111010100' + ''.join('{:08b}'.format(b) for b in packet) + '0' * 4
    spb = fs / rate
    sym = np.array([int(bits[int(i / spb)]) for i in range(int(len(bits) * spb))])
    gap = np.zeros(int(fs * 0.013))
    freq = np.concatenate([gap, sym * 2 - 1, gap, sym * 2 - 1, gap, sym * 2 - 1, gap]) * dev
    on = np.concatenate([gap, np.ones(len(sym)), gap, np.ones(len(sym)), gap, np.ones(len(sym)), gap])
    t = np.arange(len(freq)) / fs
    phase = 2 * np.pi * np.cumsum(freq + offset_hz) / fs
    x = on * np.exp(1j * phase) + noise * (rng.standard_normal(len(t)) + 1j * rng.standard_normal(len(t)))
    return x.astype(np.complex64)


def test_decode_synthetic_burst():
    cf, packets = decode_burst(synth_burst(PACKET), FS)
    assert packets == [PACKET] * 3
    assert abs(cf - -300e3) < 5e3


def test_corrupted_packet_is_dropped():
    bad = bytearray(PACKET)
    bad[4] ^= 0x10          # checksum no longer matches
    _, packets = decode_burst(synth_burst(bytes(bad)), FS)
    assert packets == []


def test_burst_detector_finds_burst_in_stream():
    rng = np.random.default_rng(2)
    quiet = lambda n: (0.05 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)
    stream = np.concatenate([quiet(FS // 2), synth_burst(PACKET), quiet(FS // 2)])
    det = BurstDetector(FS)
    found = []
    for i in range(0, len(stream), 600_000):
        found += det.feed(stream[i:i + 600_000])
    assert len(found) == 1
    _, packets = decode_burst(found[0][1], FS)
    assert packets[0] == PACKET


SAMPLE = DATA / 'ch1_burst.cu8'


def test_recorded_sample():
    """One real transmission recorded with rtl_sdr (the file holds only the burst,
    so it is passed to decode_burst directly rather than through BurstDetector)."""
    expected = parse_hex(SAMPLE.with_suffix('.txt').read_text().split('\n')[0]).raw
    cf, packets = decode_burst(cu8_to_complex(SAMPLE.read_bytes()), FS)
    assert packets == [expected] * 3


def test_recorded_sample_in_stream(tmp_path):
    """The same burst with quiet samples around it, read through read_file()."""
    quiet = bytes([127, 128]) * (FS // 2)
    f = tmp_path / 'capture.cu8'
    f.write_bytes(quiet + SAMPLE.read_bytes() + quiet)
    rx = list(read_file(f))
    assert len(rx) == 1
    assert rx[0].repeats == 3
    assert rx[0].reading.channel == 1
    assert rx[0].reading.temperature == 21.6


def test_cu8_to_complex():
    x = cu8_to_complex(bytes([255, 0, 128, 128]))
    assert len(x) == 2
    assert x[0].real > 127 and x[0].imag < -127
