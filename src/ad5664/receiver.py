"""Receive AD-5664 readings from an RTL-SDR (via ``rtl_sdr``) or from a recorded file."""
import gzip
import logging
import subprocess
import time
from dataclasses import dataclass

import numpy as np

from .demod import BurstFinder, cu8_to_complex, decode_burst, majority
from .packet import Reading, parse

log = logging.getLogger('ad5664')

# Tested settings. The sensors transmit on 429.20 MHz, but with the FC0013 tuner used
# during the analysis the signal appeared up to ~1 MHz away depending on the settings;
# with -f 429.9M -s 2.4M it appears within +/-10 kHz of the tuned frequency.
# With other tuners, use `ad5664 scan` to see where it appears.
DEFAULT_FREQ = 429_900_000
DEFAULT_RATE = 2_400_000
# Gain candidates [dB] for automatic selection. The FC0013 gains form three groups
# (about -6, 6.5 and 19 dB); one from each. rtl_sdr picks the nearest supported gain,
# so the same values also work with other tuners. Automatic gain (AGC) is not used:
# with it the noise floor changed between 1 and 100 from one start to the next and
# the signal was sometimes clipped.
DEFAULT_GAINS = (6.5, -6.3, 19.7)
SCAN_SECONDS = 140      # try each gain this long (longer than the longest interval, 129 s)
LOST_SECONDS = 600      # re-select when a channel received with the chosen gain is lost this long
BLOCK_SAMPLES = 600_000            # 0.25 s at 2.4 MS/s
REPORT_SECONDS = 600


@dataclass(frozen=True)
class Reception:
    reading: Reading
    time: float          # UNIX time (live) or seconds from the start of the file
    repeats: int         # how many of the (usually 3) repeats agreed; 1 can mean "recovered by combining"
    offset_hz: float     # signal position relative to the tuned frequency
    snr_db: float = None  # burst peak / noise floor in the +/-50 kHz detection band
    gain: float = None   # tuner gain used (live reception)

    def to_dict(self):
        d = self.reading.to_dict()
        d.update(time=round(self.time, 3), repeats=self.repeats, offset_hz=round(self.offset_hz),
                 snr_db=None if self.snr_db is None else round(self.snr_db, 1), gain=self.gain)
        return d


class _Stats:
    """Counts for the periodic 'alive' log line."""

    def __init__(self):
        self.found = self.ok = 0
        self.decode_s = 0.0
        self.last = time.monotonic()

    def report(self, finder, gain, force=False):
        now = time.monotonic()
        if not force and now - self.last < REPORT_SECONDS:
            return
        # found = bursts passed to the decoder, ok = decoded. Many more found than ok
        # means noise is being mistaken for transmissions.
        log.info('alive gain=%s floor=%.2f spread=%.3f found=%d ok=%d decode=%.1fs',
                 gain, finder.floor or 0, finder.spread or 0, self.found, self.ok, self.decode_s)
        self.found = self.ok = 0
        self.decode_s = 0.0
        self.last = now


def _readings(chunks, fs, clock, offset_hz=0.0, gain=None, stats=None):
    finder = BurstFinder(fs, offset_hz)
    for buf in chunks:
        for start, iq, snr in finder.feed(cu8_to_complex(buf)):
            t = time.monotonic()
            cf, packets = decode_burst(iq, fs)
            if stats:
                stats.found += 1
                stats.ok += bool(packets)
                stats.decode_s += time.monotonic() - t
            if not packets:
                continue
            pk, n = majority(packets)
            rx = Reception(parse(pk), clock(start), n, float(cf), round(snr, 1), gain)
            log.debug('CH%d %.1fC %d%% repeats=%d snr=%.0fdB offset=%.1fkHz', rx.reading.channel,
                      rx.reading.temperature, rx.reading.humidity, n, snr, cf / 1e3)
            yield rx
        if stats:
            stats.report(finder, gain)


def _read_chunks(f, nbytes):
    while True:
        b = f.read(nbytes)
        while b and len(b) < nbytes:        # pipes return short reads (about 64 KB)
            more = f.read(nbytes - len(b))
            if not more:
                break
            b += more
        if not b:
            return
        yield b


def _open(path):
    return gzip.open(path, 'rb') if str(path).endswith('.gz') else open(path, 'rb')


def read_file(path, fs=DEFAULT_RATE, offset_hz=0.0):
    """Yield :class:`Reception` objects from an ``rtl_sdr`` recording (``.cu8``, or ``.cu8.gz``).

    ``offset_hz`` is where the signal appears relative to the centre of the recording
    (see :class:`~ad5664.demod.BurstFinder`)."""
    with _open(path) as f:
        yield from _readings(_read_chunks(f, BLOCK_SAMPLES * 2), fs, lambda s: s / fs, offset_hz)


class GainSelector:
    """Choose the tuner gain automatically.

    Scan: try each gain for ``scan_s`` seconds and record, per channel, the best number
    of agreeing repeats and the best SNR. After the last gain, fix the best one, compared by

    1. the number of channels received (more is better),
    2. the sum of agreeing repeats (more is better),
    3. the SNR of the weakest channel (higher is better: more margin).

    If nothing was received with any gain, scan again.
    Fixed: when a channel that was received with the chosen gain has not been received
    for ``lost_s`` seconds, scan again. A channel that is still missing after that scan
    is no longer watched, so a sensor with a dead battery does not cause endless rescans.
    """

    def __init__(self, gains=DEFAULT_GAINS, scan_s=SCAN_SECONDS, lost_s=LOST_SECONDS, clock=time.monotonic):
        self.gains = list(gains)
        self.scan_s = scan_s
        self.lost_s = lost_s
        self.clock = clock
        self.last_seen = {}
        self.known = set()
        self.start_scan('start')

    def start_scan(self, reason):
        self.mode = 'scan'
        self.step = 0
        self.results = {}           # gain -> {channel: (best repeats, best snr)}
        self.gain = self.gains[0]
        log.info('gain scan (%s): trying %s dB for %d s each', reason,
                 ' / '.join('%g' % g for g in self.gains), self.scan_s)

    def plan(self):
        """Return ``(gain, seconds)`` to run next; seconds is None when the gain is fixed."""
        return self.gain, (self.scan_s if self.mode == 'scan' else None)

    def on_reception(self, channel, repeats, snr):
        self.last_seen[channel] = self.clock()
        if self.mode == 'scan':
            r = self.results.setdefault(self.gain, {})
            a, s = r.get(channel, (0, -1e9))
            r[channel] = (max(a, repeats), max(s, snr if snr is not None else -1e9))
        else:
            self.known.add(channel)

    def score(self, gain):
        r = self.results.get(gain, {})
        if not r:
            return (0, 0, -1e9)
        return (len(r), sum(a for a, _ in r.values()), min(s for _, s in r.values()))

    def window_done(self):
        """Call when a gain has been tried for its whole window."""
        r = self.results.get(self.gain, {})
        log.info('gain %g: %s', self.gain, ', '.join('CH%d(repeats=%d, %.0fdB)' % (ch, a, s)
                                                    for ch, (a, s) in sorted(r.items())) or 'nothing')
        self.step += 1
        if self.step < len(self.gains):
            self.gain = self.gains[self.step]
            return
        best = max(self.gains, key=self.score)     # ties: the earlier gain in the list
        if self.score(best)[0] == 0:
            self.start_scan('nothing received')
            return
        self.mode, self.gain, self.since = 'fixed', best, self.clock()
        self.known = set(self.results[best])
        log.info('gain fixed at %g dB (CH%s)', best, ','.join(str(c) for c in sorted(self.known)))

    def should_rescan(self):
        """Call regularly while fixed. Starts a new scan and returns True when needed."""
        if self.mode != 'fixed':
            return False
        now = self.clock()
        if now - self.since < self.lost_s:
            return False
        lost = sorted(ch for ch in self.known if now - self.last_seen.get(ch, 0) > self.lost_s)
        if not lost:
            return False
        self.start_scan('CH%s not received for %d s' % (','.join(map(str, lost)), self.lost_s))
        return True


def _rtl_sdr_cmd(rtl_sdr, freq, fs, gain, device, ppm):
    cmd = [rtl_sdr, '-f', str(int(freq)), '-s', str(int(fs))]
    if gain is not None:
        cmd += ['-g', str(gain)]
    if device is not None:
        cmd += ['-d', str(device)]
    if ppm is not None:
        cmd += ['-p', str(ppm)]
    return cmd + ['-']


def _run(cmd, fs, offset_hz, gain, seconds=None, stop=None):
    """Run rtl_sdr once and yield receptions. Returns 'window' when ``seconds`` have
    passed, 'stop' when ``stop()`` returned True, or 'eof' when rtl_sdr exited."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    log.info('rtl_sdr started (gain %s%s)', 'auto' if gain is None else gain,
             ', %d s' % seconds if seconds else '')
    t0 = time.time()
    t_end = time.monotonic() + seconds if seconds else None
    state = {'result': 'eof', 'checked': time.monotonic()}

    def chunks():
        for b in _read_chunks(p.stdout, BLOCK_SAMPLES * 2):
            yield b
            now = time.monotonic()
            if t_end is not None and now >= t_end:
                state['result'] = 'window'
                return
            if stop and now - state['checked'] >= 10:
                state['checked'] = now
                if stop():
                    state['result'] = 'stop'
                    return

    try:
        yield from _readings(chunks(), fs, lambda s: t0 + s / fs, offset_hz, gain, _Stats())
    finally:
        p.kill()
        p.wait()
        log.info('rtl_sdr stopped (%s)', state['result'])
    return state['result']


def listen(freq=DEFAULT_FREQ, fs=DEFAULT_RATE, gain=None, device=None, ppm=None, rtl_sdr='rtl_sdr',
           offset_hz=0.0, gains=DEFAULT_GAINS, scan_s=SCAN_SECONDS, lost_s=LOST_SECONDS, restart=True):
    """Run ``rtl_sdr`` and yield :class:`Reception` objects until stopped.

    ``gain`` (dB) fixes the tuner gain. When it is ``None`` the gain is chosen
    automatically from ``gains`` (see :class:`GainSelector`); readings are yielded
    while choosing, too. ``offset_hz`` is where the signal appears relative to
    ``freq`` (find it with :func:`scan`). ``device`` is the rtl_sdr device index.
    When rtl_sdr exits (e.g. the dongle was unplugged), it is restarted after 5 s
    if ``restart`` is true.
    """
    sel = None if gain is not None else GainSelector(gains, scan_s, lost_s)
    while True:
        g, seconds = (gain, None) if sel is None else sel.plan()
        cmd = _rtl_sdr_cmd(rtl_sdr, freq, fs, g, device, ppm)
        run = _run(cmd, fs, offset_hz, g, seconds, sel.should_rescan if sel else None)
        try:
            while True:
                try:
                    rx = next(run)
                except StopIteration as e:
                    result = e.value
                    break
                if sel:
                    sel.on_reception(rx.reading.channel, rx.repeats, rx.snr_db)
                yield rx
        finally:
            run.close()             # stops rtl_sdr when the caller stops iterating
        if result == 'window':
            sel.window_done()
        elif result == 'eof':
            if not restart:
                return
            time.sleep(5)


def scan(freq=DEFAULT_FREQ, fs=DEFAULT_RATE, gain=19.7, seconds=150, device=None, ppm=None,
         rtl_sdr='rtl_sdr', path=None):
    """Find where the sensors' signal appears in the whole captured band.

    Unlike :func:`listen`, which only watches +/-50 kHz around ``offset_hz``, this
    looks for strong bursts anywhere in the band (per 2 ms, in 100 kHz slices) and
    yields a :class:`Reception` for every one that decodes. Its ``offset_hz`` is the
    value to pass to ``listen(offset_hz=...)`` with the same ``freq`` and ``fs``.
    It needs a reasonably strong signal: put the sensor near the antenna.
    Reads ``path`` (a recording) instead of running rtl_sdr when given.
    """
    if path is not None:
        f = _open(path)
        clock = lambda s: s / fs            # noqa: E731
        proc = None
    else:
        proc = subprocess.Popen(_rtl_sdr_cmd(rtl_sdr, freq, fs, gain, device, ppm),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        f = proc.stdout
        t0 = time.time()
        clock = lambda s: t0 + s / fs       # noqa: E731
    step = int(round(fs * 0.002))
    nband = max(1, int(fs // 100e3))
    buf = np.zeros(0, np.complex64)
    buf_start = 0
    pending = []            # sample indices of loud steps waiting for enough data after them
    last_hit = -10 ** 12
    t_end = time.monotonic() + seconds
    try:
        for raw in _read_chunks(f, BLOCK_SAMPLES * 2):
            x = cu8_to_complex(raw)
            n = len(x) // step * step
            if n:
                z = (x[:n] - x[:n].mean()).reshape(-1, step)
                p = np.abs(np.fft.fft(z, axis=1)) ** 2
                p[:, 0] = 0                                     # DC
                k = p.shape[1] // nband * nband
                bands = p[:, :k].reshape(len(z), nband, -1).mean(2)
                ratio = bands / (np.median(bands, axis=0) + 1e-12)
                start = buf_start + len(buf)
                for i in np.flatnonzero(ratio.max(1) > 10):    # 10 dB above that slice's median
                    s = start + int(i) * step
                    if s - last_hit > 0.3 * fs:
                        pending.append(s)
                        last_hit = s
            buf = np.concatenate([buf, x])
            end = buf_start + len(buf)
            while pending and pending[0] + 0.2 * fs <= end:
                s = pending.pop(0)
                a = max(int(s - 0.1 * fs), buf_start)
                iq = buf[a - buf_start:int(s + 0.2 * fs) - buf_start]
                cf, packets = decode_burst(iq, fs)
                if packets:
                    pk, cnt = majority(packets)
                    yield Reception(parse(pk), clock(s), cnt, cf, None, gain if path is None else None)
            keep = max(end - int(0.4 * fs), buf_start)
            buf = buf[keep - buf_start:]
            buf_start = keep
            if path is None and time.monotonic() >= t_end:
                break
    finally:
        if proc:
            proc.kill()
            proc.wait()
        else:
            f.close()
