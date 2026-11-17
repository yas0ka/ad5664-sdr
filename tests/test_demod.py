from pathlib import Path

import numpy as np
import pytest

from ad5664 import parse_hex
from ad5664.demod import (BurstFinder, cu8_to_complex, decode_burst, decode_burst_hard,
                          decode_burst_soft)
from ad5664.receiver import read_file

DATA = Path(__file__).parent / 'data'
FS = 2_400_000
PACKET = bytes.fromhex('2A F7 00 32 12 03 00 00 00 00 EC 00 8D 00 C7 03 AB')
SAMPLE = DATA / 'ch1_burst.cu8'
LOWGAIN = DATA / 'ch5_lowgain.cu8.gz'


def sample_packet():
    return parse_hex(SAMPLE.with_suffix('.txt').read_text().split('\n')[0]).raw


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


def quiet(rng, n, level=0.05):
    return (level * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)


def feed_all(finder, stream, block=600_000):
    found = []
    for i in range(0, len(stream), block):
        found += finder.feed(stream[i:i + block])
    return found


@pytest.mark.parametrize('decoder', [decode_burst, decode_burst_soft, decode_burst_hard])
def test_decode_synthetic_burst(decoder):
    cf, packets = decoder(synth_burst(PACKET), FS)
    assert packets == [PACKET] * 3
    assert abs(cf - -300e3) < 5e3


def test_corrupted_packet_is_dropped():
    bad = bytearray(PACKET)
    bad[4] ^= 0x10          # checksum no longer matches
    _, packets = decode_burst(synth_burst(bytes(bad)), FS)
    assert packets == []


def test_burst_finder_at_offset():
    """The finder watches +/-50 kHz around offset_hz: a burst at -300 kHz is strong
    only when it is told where to look (outside, only a leak of it is seen)."""
    rng = np.random.default_rng(2)
    stream = np.concatenate([quiet(rng, FS), synth_burst(PACKET), quiet(rng, FS // 2)])
    found = feed_all(BurstFinder(FS, offset_hz=-300e3), stream)
    assert len(found) == 1
    start, iq, snr = found[0]
    assert abs(start / FS - 1.0) < 0.05
    assert snr > 30
    assert all(s < snr - 20 for _, _, s in feed_all(BurstFinder(FS), stream))
    _, packets = decode_burst(iq, FS)
    assert packets[0] == PACKET


def test_burst_finder_odd_block_sizes():
    rng = np.random.default_rng(3)
    stream = np.concatenate([quiet(rng, FS), synth_burst(PACKET, offset_hz=5e3), quiet(rng, FS // 2)])
    found = feed_all(BurstFinder(FS), stream, block=123_457)
    assert len(found) == 1
    assert decode_burst(found[0][1], FS)[1][0] == PACKET


def test_recorded_sample():
    """One real transmission recorded with rtl_sdr (strong signal)."""
    cf, packets = decode_burst(cu8_to_complex(SAMPLE.read_bytes()), FS)
    assert packets == [sample_packet()] * 3


def test_recorded_sample_in_stream(tmp_path):
    """The same burst with quiet samples around it, read through read_file()."""
    rng = np.random.default_rng(4)
    noise = lambda n: np.clip(np.round(127.5 + 2 * rng.standard_normal(2 * n)), 0, 255).astype(np.uint8).tobytes()  # noqa: E731
    f = tmp_path / 'capture.cu8'
    f.write_bytes(noise(FS) + SAMPLE.read_bytes() + noise(FS // 2))
    rx = list(read_file(f))
    assert len(rx) == 1
    assert rx[0].repeats == 3
    assert rx[0].reading.channel == 1
    assert rx[0].reading.temperature == 21.6


def test_low_gain_recording():
    """0.8 s recorded at gain 6.5 dB, with periodic noise from the board. The 0.1.0
    detector found nothing in it."""
    rx = list(read_file(LOWGAIN))
    assert len(rx) == 1
    r = rx[0]
    assert r.repeats == 3
    assert r.reading.raw == bytes.fromhex('AF 7D 00 31 E1 02 00 00 00 00 C0 00 68 00 07 04 73')
    assert r.reading.channel == 5 and r.reading.temperature == 23.7 and r.reading.humidity == 49
    assert r.snr_db > 30


def test_cu8_to_complex():
    x = cu8_to_complex(bytes([255, 0, 128, 128]))
    assert len(x) == 2
    assert x[0].real > 127 and x[0].imag < -127


def weak_sample(snr_db, rng):
    """The real CH1 burst scaled to amplitude 1, with white noise added so that the
    noise power in 40 kHz is ``snr_db`` below the signal, and 1 s of noise before it."""
    d = np.frombuffer(SAMPLE.read_bytes(), np.uint8).astype(np.float32) - 127.5
    x = (d[0::2] + 1j * d[1::2]).astype(np.complex64)
    x -= x.mean()
    e = np.abs(x).reshape(-1, 4800).mean(1)
    x /= float(np.median(e[e > e.max() * 0.5]))
    sig = np.concatenate([np.zeros(FS, np.complex64), x, np.zeros(FS // 2, np.complex64)])
    sig *= np.complex64(np.exp(1j * rng.uniform(0, 2 * np.pi)))
    sigma = np.sqrt(10 ** (-snr_db / 10) * FS / 40e3 / 2)
    n = len(sig)
    return sig + (sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)


def test_weak_signal():
    """At 6 dB SNR (in 40 kHz) the 0.1.0 detector and decoder read nothing (they
    needed about 11.5 dB). See tools/sensitivity.py for the full curve."""
    rng = np.random.default_rng(7)
    for _ in range(3):
        found = feed_all(BurstFinder(FS), weak_sample(6, rng))
        assert len(found) == 1
        _, packets = decode_burst(found[0][1], FS)
        assert packets and set(packets) == {sample_packet()}
