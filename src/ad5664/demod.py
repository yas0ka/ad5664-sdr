"""Demodulate AD-5664 transmissions from complex I/Q samples (needs numpy).

One transmission ("burst", about 0.1 s) contains the same packet three times.
:func:`decode_burst` takes the I/Q samples of one burst and returns every repeat
whose checksum is valid. :class:`BurstDetector` finds bursts in a continuous stream.

Signal: 2-FSK, deviation about +/-11 kHz, 10 kbit/s NRZ, preamble ``AA AA ...``,
sync word ``2D D4``, then 17 bytes. The higher frequency is ``1``.
"""
from collections import Counter

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .packet import PACKET_LEN, verify

BIT_RATE = 10000.0
SYNC_BITS = '0010110111010100'  # 0x2DD4


def cu8_to_complex(buf):
    """Convert rtl_sdr output (interleaved unsigned 8-bit I/Q) to complex64."""
    d = np.frombuffer(buf, dtype=np.uint8).astype(np.float32) - 127.5
    d = d[:len(d) // 2 * 2]
    return (d[0::2] + 1j * d[1::2]).astype(np.complex64)


def _lowpass_taps(fs, cutoff, n):
    h = np.sinc(2 * cutoff / fs * (np.arange(n) - n // 2)) * np.hamming(n)
    return h / h.sum()


def fm_demod(x, fs):
    """Find the signal by FFT, shift it to 0 Hz, decimate and FM-demodulate.

    Returns ``(offset_hz, inst_freq, fs2, amplitude)``. The FFT search means the
    tuner does not have to be set exactly to the transmit frequency, which matters
    with cheap dongles whose frequency error can be hundreds of kHz.
    """
    n = len(x)
    s = np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(n)))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(n, 1 / fs))
    w = max(1, int(n * 2e3 / fs))
    ss = np.convolve(s, np.ones(w), 'same')
    m = ss > ss.max() * 0.1
    cf = (f[m].min() + f[m].max()) / 2
    y = x * np.exp(-2j * np.pi * cf * np.arange(n) / fs)
    dec = max(1, int(fs // 128000))
    y = np.convolve(y, _lowpass_taps(fs, 40e3, 8 * dec + 1), 'same')[::dec]
    fs2 = fs / dec
    fm = np.angle(y[1:] * np.conj(y[:-1])) * fs2 / 2 / np.pi
    fm = np.median(sliding_window_view(np.pad(fm, 3, mode='edge'), 7), axis=1)  # remove spikes
    return cf, fm, fs2, np.abs(y[1:])


def split_packets(fm, fs2, amp):
    """Cut a burst into its (usually three) packets using the amplitude.

    The repeats are separated by >= 10 ms of silence. The start is trimmed a little
    (unstable ramp-up) but the end is *extended* by 1 ms so the last byte (the
    checksum) is never cut off.
    """
    w = max(1, int(fs2 * 0.0003))
    a = np.convolve(amp, np.ones(w) / w, 'same')
    lo, hi = np.percentile(a, 10), np.percentile(a, 95)
    on = a > (lo + hi) / 2
    d = np.diff(np.r_[0, on.astype(int), 0])
    head, tail = int(fs2 * 0.0005), int(fs2 * 0.001)
    return [fm[s + head:e + tail]
            for s, e in zip(np.where(d == 1)[0], np.where(d == -1)[0]) if e - s > 0.003 * fs2]


def slice_bits(p, fs2):
    """Turn the instantaneous frequency of one packet into a '0'/'1' string.

    The decision threshold is the mean over the preamble. The bit period is measured
    from the preamble edges, and every edge is placed at round(time since first
    edge / bit period). Errors in edge position therefore do not accumulate, so long
    runs of 0s or 1s are counted correctly.
    """
    t0 = fs2 / BIT_RATE
    if len(p) < 60 * t0:
        return ''
    q = p - p[int(2 * t0):int(40 * t0)].mean()
    b = (q > 0).astype(int)
    e = np.where(np.diff(b) != 0)[0] + 1
    if len(e) < 20:
        return ''
    pe = e[:50]
    k = np.r_[0, np.cumsum(np.round(np.diff(pe) / t0))]
    t = np.polyfit(k, pe, 1)[0]
    if abs(t / t0 - 1) > 0.03:      # implausible measurement: use the nominal rate
        t = t0
    idx = np.r_[0, e, len(b)]
    pos = np.r_[0, np.round((e - e[0]) / t).astype(int) + 1, int(round((len(b) - e[0]) / t)) + 1]
    return ''.join(str(b[idx[i]]) * max(int(pos[i + 1] - pos[i]), 0) for i in range(len(idx) - 1))


def bits_to_packet(bits):
    """Find the sync word and return the following 17 bytes, or None."""
    i = bits.find(SYNC_BITS)
    if i < 0:
        return None
    d = bits[i + 16:i + 16 + 8 * PACKET_LEN]
    if len(d) < 8 * PACKET_LEN:
        return None
    return bytes(int(d[k:k + 8], 2) for k in range(0, len(d), 8))


def decode_burst(x, fs):
    """Decode one burst. Returns ``(offset_hz, [packet bytes, ...])`` with only
    checksum-valid packets (normally three identical ones)."""
    cf, fm, fs2, amp = fm_demod(x, fs)
    out = []
    for p in split_packets(fm, fs2, amp):
        pk = bits_to_packet(slice_bits(p, fs2))
        if pk is not None and verify(pk):
            out.append(pk)
    return cf, out


def majority(packets):
    """Return ``(packet, count)`` for the most common packet."""
    return Counter(packets).most_common(1)[0]


class BurstDetector:
    """Find bursts in a continuous stream of complex samples.

    Feed blocks with :meth:`feed`; it returns a list of ``(start_sample, iq)`` for
    every burst that has finished. Detection uses the mean amplitude per 2 ms
    against a slowly tracked noise floor.
    """

    def __init__(self, fs, threshold=1.6, step_s=0.002, gap_s=0.08, pad_s=0.02,
                 min_s=0.008, max_s=0.5):
        self.fs = fs
        self.threshold = threshold
        self.step = int(fs * step_s)
        self.gap = int(round(gap_s / step_s))       # end of burst after this many quiet steps
        self.pad = int(round(pad_s / step_s))       # margin before/after the burst
        self.min_steps = int(round(min_s / step_s))
        self.max_len = int(fs * max_s)              # longer than this = noise
        self.buf = np.zeros(0, np.complex64)
        self.buf_start = 0
        self.total = 0
        self.floor = None
        self.burst = None                           # [first step, last loud step]
        self._rest = np.zeros(0, np.complex64)      # samples not yet a whole step

    def feed(self, x):
        x = np.concatenate([self._rest, np.asarray(x, np.complex64)])
        n = len(x) // self.step * self.step
        self._rest = x[n:]
        x = x[:n]
        found = []
        if n == 0:
            return found
        env = np.abs(x).reshape(-1, self.step).mean(1)
        med = float(np.median(env))
        self.floor = med if self.floor is None else self.floor * 0.98 + min(med, self.floor * 1.5) * 0.02
        loud = np.where(env > self.floor * self.threshold)[0]
        blk0 = self.total // self.step
        self.buf = np.concatenate([self.buf, x])
        self.total += n
        for i in loud:
            b = blk0 + i
            if self.burst and b - self.burst[1] < self.gap:
                self.burst[1] = b
            elif self.burst is None:
                self.burst = [b, b]
            else:                                   # previous burst ended inside this block
                self._emit(found)
                self.burst = [b, b]
        if self.burst and self.total // self.step - self.burst[1] >= self.gap:
            self._emit(found)
        keep = (self.burst[0] - self.pad) * self.step if self.burst else self.total - self.pad * self.step
        keep = max(keep, self.total - self.max_len - n)
        if keep > self.buf_start:
            self.buf = self.buf[keep - self.buf_start:]
            self.buf_start = keep
        return found

    def _emit(self, found):
        s = max((self.burst[0] - self.pad) * self.step, self.buf_start)
        e = min((self.burst[1] + self.pad) * self.step, self.total)
        if self.burst[1] - self.burst[0] >= self.min_steps and e - s <= self.max_len:
            found.append((s, self.buf[s - self.buf_start:e - self.buf_start]))
        self.burst = None
