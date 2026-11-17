"""Demodulate AD-5664 transmissions from complex I/Q samples (needs numpy).

One transmission ("burst", about 0.1 s) contains the same packet three times.
:func:`decode_burst` takes the I/Q samples of one burst and returns the packet
(once per repeat that agreed). :class:`BurstFinder` finds bursts in a continuous stream.

Signal: 2-FSK, deviation about +/-10.7 kHz, 10 kbit/s NRZ, preamble ``AA AA ...``,
sync word ``2D D4``, then 17 bytes. The higher frequency is ``1``.

Two decoders are included:

* :func:`decode_burst_soft` (default since 0.2.0) compares the energy of the two FSK
  tones over each bit, searches the bit timing over the whole packet, tolerates
  errors in the sync word and adds the three repeats together. It reads signals
  about 10 dB weaker than the old decoder.
* :func:`decode_burst_hard` is the 0.1.0 decoder (FM demodulation and edge timing).
  :func:`decode_burst` falls back to it when the soft decoder finds nothing.

Both only return packets that pass :func:`ad5664.packet.plausible` (checksum plus
consistency checks), because the 8-bit sum checksum alone lets about 1 % of
corrupted packets through when the signal is weak.
"""
import math
from collections import Counter

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .packet import PACKET_LEN, plausible

BIT_RATE = 10000.0
DEVIATION = 10.7e3                       # measured 10.69-10.74 kHz on strong recordings
SYNC_BITS = '0010110111010100'           # 0x2DD4
SYNC_TEMPLATE = '10101010' + SYNC_BITS   # end of preamble + sync word
CHANNEL_CUTOFF = 22e3                    # one-sided bandwidth used by the soft decoder


def cu8_to_complex(buf):
    """Convert rtl_sdr output (interleaved unsigned 8-bit I/Q) to complex64."""
    d = np.frombuffer(buf, dtype=np.uint8).astype(np.float32) - 127.5
    d = d[:len(d) // 2 * 2]
    return (d[0::2] + 1j * d[1::2]).astype(np.complex64)


def _lowpass_taps(fs, cutoff, n):
    h = np.sinc(2 * cutoff / fs * (np.arange(n) - n // 2)) * np.hamming(n)
    return h / h.sum()


# ------------------------------------------------------------------ soft decoder (0.2.0)

def find_center(x, fs):
    """Return the centre frequency [Hz] of the signal in ``x`` relative to 0 Hz.

    The spectrum is smoothed over 2 kHz, the noise floor (median) is subtracted, and
    the centre of the region around the strongest peak (within 2.5 x the deviation)
    that rises above 15 % of the peak is returned. Searching the whole capture means
    the tuner does not have to be set exactly to the transmit frequency.
    """
    n = len(x)
    s = np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(n)))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(n, 1 / fs))
    w = max(1, int(n * 2e3 / fs))
    c = np.r_[0, np.cumsum(s)]                         # moving average via cumulative sum
    i = np.clip(np.arange(n) - w // 2, 0, n - w)
    ss = (c[i + w] - c[i]) / w
    floor = np.median(ss)
    pk = int(np.argmax(ss))
    near = np.abs(f - f[pk]) < 2.5 * DEVIATION       # the two FSK tones are at +/- deviation
    m = near & (ss - floor > 0.15 * (ss[pk] - floor))
    return float((f[m].min() + f[m].max()) / 2)


def channelize(x, fs, cf):
    """Shift ``cf`` to 0 Hz, keep +/-22 kHz and decimate to about 133 kS/s.
    Returns ``(y, fs2)``."""
    y = x * np.exp(-2j * np.pi * cf * np.arange(len(x)) / fs).astype(np.complex64)
    d1 = max(1, int(round(fs / 400e3)))               # box average down to ~400 kS/s first
    n = len(y) // d1 * d1
    y = y[:n].reshape(-1, d1).mean(1)
    fs1 = fs / d1
    y = np.convolve(y, _lowpass_taps(fs1, CHANNEL_CUTOFF, 129).astype(np.float32), 'same')
    d2 = max(1, int(round(fs1 / 133e3)))
    return y[::d2], fs1 / d2


def find_packets(y, fs2):
    """Split a channelized burst into its repeats by amplitude.
    Returns ``[(start, end), ...]`` as sample indices of ``y``."""
    w = max(1, int(fs2 * 0.001))
    a = np.convolve(np.abs(y), np.ones(w) / w, 'same')
    lo, hi = np.percentile(a, 10), np.percentile(a, 95)
    on = a > (lo + hi) / 2
    d = np.diff(np.r_[0, on.astype(int), 0])
    segs = []
    for s, e in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
        if segs and s - segs[-1][1] < 0.003 * fs2:    # gaps < 3 ms are noise dips: join them
            segs[-1][1] = e
        else:
            segs.append([s, e])
    m = int(fs2 * 0.001)
    return [(max(0, s - m), min(len(y), e + m)) for s, e in segs if e - s > 0.010 * fs2]


def soft_bits(y, fs2):
    """Return, for each bit of one packet, (energy of the high tone - energy of the low
    tone), normalised to a mean magnitude of 1. Positive means ``1``.

    The bit period (nominal +/-1 %) and the start position are the ones that maximise
    the mean magnitude over the whole packet. Returns ``None`` if the packet is too short.
    """
    # Remove the remaining frequency offset using the preamble (alternating 0/1, so its
    # mean FM value is exactly the centre). The data part has more 0s than 1s, so the
    # mean or median of the whole packet would be biased. Clip noise spikes at +/-30 kHz.
    fm = np.angle(y[1:] * np.conj(y[:-1])) * fs2 / 2 / np.pi
    off = float(np.clip(fm[int(0.001 * fs2):int(0.004 * fs2)], -30e3, 30e3).mean())
    t = np.arange(len(y)) / fs2
    y = y * np.exp(-2j * np.pi * off * t)
    hi = np.r_[0, np.cumsum(y * np.exp(-2j * np.pi * DEVIATION * t))]
    lo = np.r_[0, np.cumsum(y * np.exp(2j * np.pi * DEVIATION * t))]
    t0 = fs2 / BIT_RATE
    k = int((len(y) - t0 * 1.011) // (t0 * 1.01))      # number of bits that fit for every candidate
    if k < 60:
        return None
    best = None
    for period in t0 * (1 + np.arange(-0.010, 0.0101, 0.0005)):
        p = np.arange(0, period, 0.5)[:, None]          # all start positions at once
        e = np.round(p + period * np.arange(k + 1)).astype(int)
        s = np.abs(hi[e[:, 1:]] - hi[e[:, :-1]]) ** 2 - np.abs(lo[e[:, 1:]] - lo[e[:, :-1]]) ** 2
        score = np.abs(s).mean(1)
        j = int(np.argmax(score))
        if best is None or score[j] > best[0]:
            best = (score[j], s[j])
    s = best[1]
    return s / (np.abs(s).mean() + 1e-12)


def align(s, max_errors=3):
    """Find the best match of end-of-preamble + sync word in the soft bits ``s``
    (allowing up to ``max_errors`` wrong bits) and return the 136 soft bits after it."""
    tpl = np.array([1.0 if c == '1' else -1.0 for c in SYNC_TEMPLATE])
    n = len(s) - len(tpl) - 8 * PACKET_LEN
    if n <= 0:
        return None
    c = np.correlate(np.sign(s[:n + len(tpl) - 1]), tpl, 'valid')
    i = int(np.argmax(c))
    if c[i] < len(tpl) - 2 * max_errors:
        return None
    return s[i + len(tpl):i + len(tpl) + 8 * PACKET_LEN]


def soft_to_bytes(d):
    """Hard-decide soft bits and pack them into bytes."""
    bits = ''.join('1' if v > 0 else '0' for v in d)
    return bytes(int(bits[k:k + 8], 2) for k in range(0, len(bits) - 7, 8))


def decode_burst_soft(x, fs):
    """Weak-signal decoder. Returns ``(offset_hz, [packet, ...])`` where the list holds
    the decoded packet once per repeat that agreed (empty if nothing was decoded)."""
    cf = find_center(x, fs)
    y, fs2 = channelize(x, fs, cf)
    singles, datas = [], []
    for a, b in find_packets(y, fs2):
        s = soft_bits(y[a:b], fs2)
        d = align(s) if s is not None else None
        if d is None:
            continue
        datas.append(d)
        pk = soft_to_bytes(d)
        if plausible(pk):
            singles.append(pk)
    # 1. two or more repeats agree: done
    counts = Counter(singles)
    for pk, n in counts.most_common():
        if n >= 2:
            return cf, [pk] * n
    # 2. the repeats added together decode (more reliable than any single one)
    if len(datas) >= 2:
        comb = soft_to_bytes(np.sum(datas, axis=0))
        if plausible(comb):
            return cf, [comb] * max(1, counts[comb])
    # 3. only one repeat was found: accept it only if every bit is clear
    if len(datas) == 1 and len(singles) == 1 and np.abs(datas[0]).min() > 0.25:
        return cf, singles
    return cf, []


# ------------------------------------------------------------------ hard decoder (0.1.0)

def fm_demod(x, fs):
    """Find the signal by FFT, shift it to 0 Hz, decimate and FM-demodulate.
    Returns ``(offset_hz, inst_freq, fs2, amplitude)``."""
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
    return float(cf), fm, fs2, np.abs(y[1:])


def split_packets(fm, fs2, amp):
    """Cut a burst into its (usually three) packets using the amplitude.
    The end is extended by 1 ms so the last byte (the checksum) is never cut off."""
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
    Every edge is placed at round(time since first edge / bit period), so errors in
    edge position do not accumulate over long runs of 0s or 1s."""
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
    """Find the exact sync word and return the following 17 bytes, or None."""
    i = bits.find(SYNC_BITS)
    if i < 0:
        return None
    d = bits[i + 16:i + 16 + 8 * PACKET_LEN]
    if len(d) < 8 * PACKET_LEN:
        return None
    return bytes(int(d[k:k + 8], 2) for k in range(0, len(d), 8))


def decode_burst_hard(x, fs):
    """The 0.1.0 decoder. Returns ``(offset_hz, [packet, ...])`` with every repeat
    that passed :func:`~ad5664.packet.plausible`."""
    cf, fm, fs2, amp = fm_demod(x, fs)
    out = []
    for p in split_packets(fm, fs2, amp):
        pk = bits_to_packet(slice_bits(p, fs2))
        if pk is not None and plausible(pk):
            out.append(pk)
    return cf, out


# ------------------------------------------------------------------ public entry points

def decode_burst(x, fs):
    """Decode one burst. Returns ``(offset_hz, [packet bytes, ...])``.

    The list holds the packet once for each repeat that agreed (normally 3; 1 means
    the packet was only recovered by adding the repeats together or from a single
    clean repeat). It is empty when nothing could be decoded.
    """
    try:
        cf, packets = decode_burst_soft(x, fs)
    except (ValueError, IndexError):
        cf, packets = 0.0, []
    if not packets:
        try:
            cf2, packets = decode_burst_hard(x, fs)
        except (ValueError, IndexError, TypeError, np.linalg.LinAlgError):
            packets = []
        if packets:
            cf = cf2
    return cf, packets


def majority(packets):
    """Return ``(packet, count)`` for the most common packet."""
    return Counter(packets).most_common(1)[0]


class BurstFinder:
    """Find bursts in a continuous stream of complex samples.

    Feed blocks of any length with :meth:`feed`; it returns a list of
    ``(start_sample, iq, snr_db)`` for every burst found. ``snr_db`` is the peak
    amplitude relative to the noise floor in the detection band.

    Detection looks only at a +/-50 kHz band around ``offset_hz`` (relative to the
    tuned frequency), using the mean amplitude per 2 ms:

    1. A step is "loud" when it exceeds floor + 6 x spread (at least 1.2 x floor).
       The floor and spread (median and MAD) are tracked over the last ~10 s, so the
       threshold rises automatically where the noise is rough.
    2. From the first loud step, the peak within 0.2 s is found and only steps above
       35 % of it belong to the burst (FSK has a constant envelope, so weak noise
       around it is excluded). A burst is cut after 0.16 s at most.

    The 0.1.0 detector looked at the whole 2.4 MHz band and closed a burst only after
    80 ms of silence; with automatic gain or periodic noise it could stay open forever
    and never pass anything to the decoder. The final decision is made by the decoder
    (checksum and plausibility), so this class favours not missing bursts.
    """

    K_SPREAD = 6.0
    K_MIN = 1.2
    PEAK_FRAC = 0.35
    MIN_DENSE = 0.4         # at least 40 % of the burst must be above half the peak

    def __init__(self, fs, offset_hz=0.0, band_hz=50e3):
        self.fs = fs
        self.offset_hz = float(offset_hz)
        self.step = int(round(fs * 0.002))
        target = max(1, int(fs / (2 * band_hz)))           # box average this many samples
        self.dec = max(d for d in range(1, target + 1) if self.step % d == 0)
        self.win = 100              # steps (0.2 s) to look for the peak
        self.max_span = 80          # a burst is at most 0.16 s (really ~0.09 s)
        self.min_span = 4
        self.gap_end = 40           # 80 ms without signal ends a burst
        self.pad = 10               # 20 ms margin on both sides
        self.lookahead = self.win + self.max_span + self.pad
        self.buf = np.zeros(0, np.complex64)
        self.buf_start = 0          # sample index of buf[0]
        self.env = np.zeros(0, np.float32)
        self.env_start = 0          # step index of env[0]
        self.now = 0                # steps read so far
        self.scan = 0               # steps before this have been examined
        self.meds = []
        self.mads = []
        self.floor = None
        self.spread = None
        self._rest = np.zeros(0, np.complex64)
        self._total = 0             # samples consumed (for the mixer phase)
        self._dc = None

    def feed(self, x):
        x = np.concatenate([self._rest, np.asarray(x, np.complex64)])
        n = len(x) // self.step * self.step
        self._rest = x[n:]
        x = x[:n]
        if n == 0:
            return []
        # Remove the dongle's DC offset. It is tracked slowly (time constant ~2.5 s): the
        # mean of a block that contains part of a burst is pulled by the burst, and
        # subtracting it would leave a step that looks like another burst.
        m = complex(x.mean())
        a = min(1.0, n / self.fs / 2.5)
        self._dc = m if self._dc is None else self._dc + a * (m - self._dc)
        z = x - np.complex64(self._dc)
        if self.offset_hz:
            k = np.arange(self._total, self._total + n)
            z = z * np.exp(-2j * np.pi * self.offset_hz / self.fs * k).astype(np.complex64)
        self._total += n
        y = z.reshape(-1, self.dec).mean(1)
        e = np.abs(y).reshape(-1, self.step // self.dec).mean(1).astype(np.float32)
        med = float(np.median(e))
        self.meds = (self.meds + [med])[-40:]
        self.mads = (self.mads + [1.4826 * float(np.median(np.abs(e - med)))])[-40:]
        self.floor = float(np.median(self.meds))
        self.spread = float(np.median(self.mads))
        self.buf = np.concatenate([self.buf, x])
        self.env = np.concatenate([self.env, e])
        self.now += len(e)
        out = []
        thr = max(self.floor + self.K_SPREAD * self.spread, self.floor * self.K_MIN)
        while True:
            seg = self.env[self.scan - self.env_start:]
            idx = np.flatnonzero(seg > thr)
            if len(idx) == 0:
                self.scan = self.now
                break
            c0 = self.scan + int(idx[0])
            if self.now < c0 + self.lookahead:           # decide after more data arrives
                self.scan = c0
                break
            w = self.env[c0 - self.env_start:c0 - self.env_start + self.win + self.max_span]
            peak = float(w[:self.win].max())
            sel = np.flatnonzero(w > max(peak * self.PEAK_FRAC, thr))
            s0 = s1 = c0 + int(sel[0])
            for j in sel[1:]:
                b = c0 + int(j)
                if b - s1 >= self.gap_end or b - s0 >= self.max_span:
                    break
                s1 = b
            g = self.env[s0 - self.env_start:s1 + 1 - self.env_start]
            if s1 - s0 >= self.min_span and float((g > peak * 0.5).mean()) >= self.MIN_DENSE:
                a = max((s0 - self.pad) * self.step, self.buf_start)
                z = min((s1 + 1 + self.pad) * self.step, self.now * self.step)
                snr = 20 * math.log10(peak / self.floor) if self.floor > 0 else 0.0
                out.append((a, self.buf[a - self.buf_start:z - self.buf_start], snr))
            self.scan = s1 + 1
        keep = max(self.scan - self.pad, 0)                # drop what has been examined
        if keep > self.env_start:
            self.env = self.env[keep - self.env_start:]
            self.env_start = keep
            self.buf = self.buf[keep * self.step - self.buf_start:]
            self.buf_start = keep * self.step
        return out
