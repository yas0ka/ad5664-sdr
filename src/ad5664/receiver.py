"""Receive AD-5664 readings from an RTL-SDR (via ``rtl_sdr``) or from a recorded file."""
import subprocess
import time
from dataclasses import dataclass

from .demod import BurstDetector, cu8_to_complex, decode_burst, majority
from .packet import Reading, parse

# Tested settings. The sensors transmit on 429.20 MHz, but with the FC0013 tuner used
# during the analysis the signal appeared up to ~1 MHz away depending on the settings.
# A wide 2.4 MS/s capture plus the FFT search in fm_demod() copes with that.
DEFAULT_FREQ = 429_900_000
DEFAULT_RATE = 2_400_000
BLOCK_SAMPLES = 600_000            # 0.25 s at 2.4 MS/s


@dataclass(frozen=True)
class Reception:
    reading: Reading
    time: float          # UNIX time (live) or seconds from the start of the file
    repeats: int         # how many of the (usually 3) repeats decoded to this packet
    offset_hz: float     # signal position relative to the tuned frequency

    def to_dict(self):
        d = self.reading.to_dict()
        d.update(time=round(self.time, 3), repeats=self.repeats, offset_hz=round(self.offset_hz))
        return d


def _readings(chunks, fs, clock):
    det = BurstDetector(fs)
    for buf in chunks:
        for start, iq in det.feed(cu8_to_complex(buf)):
            try:
                cf, packets = decode_burst(iq, fs)
            except (ValueError, IndexError):     # noise that does not demodulate
                continue
            if not packets:
                continue
            pk, n = majority(packets)
            yield Reception(parse(pk), clock(start), n, float(cf))


def _read_chunks(f, nbytes):
    while True:
        b = f.read(nbytes)
        while b and len(b) < nbytes:        # pipes may return short reads
            more = f.read(nbytes - len(b))
            if not more:
                break
            b += more
        if not b:
            return
        yield b


def read_file(path, fs=DEFAULT_RATE):
    """Yield :class:`Reception` objects from an ``rtl_sdr`` recording (``.cu8``)."""
    with open(path, 'rb') as f:
        yield from _readings(_read_chunks(f, BLOCK_SAMPLES * 2), fs, lambda s: s / fs)


def listen(freq=DEFAULT_FREQ, fs=DEFAULT_RATE, gain=None, device=None, ppm=None, rtl_sdr='rtl_sdr'):
    """Run ``rtl_sdr`` and yield :class:`Reception` objects forever (until ``rtl_sdr`` exits).

    ``gain`` is in dB (``None`` = automatic), ``device`` is the rtl_sdr device index.
    """
    cmd = [rtl_sdr, '-f', str(int(freq)), '-s', str(int(fs))]
    if gain is not None:
        cmd += ['-g', str(gain)]
    if device is not None:
        cmd += ['-d', str(device)]
    if ppm is not None:
        cmd += ['-p', str(ppm)]
    cmd.append('-')
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    t0 = time.time()
    try:
        yield from _readings(_read_chunks(p.stdout, BLOCK_SAMPLES * 2), fs, lambda s: t0 + s / fs)
    finally:
        p.kill()
        p.wait()
