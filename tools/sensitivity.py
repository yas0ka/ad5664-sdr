#!/usr/bin/env python3
"""Measure how weak a signal the receiver can decode.

Takes the real recording tests/data/ch1_burst.cu8 (one strong CH1 transmission),
adds white noise, and runs it through the same path as live reception
(BurstFinder -> decode_burst). Prints the share of tries that were detected,
decoded correctly, and decoded *wrongly* (should always be 0).

    python tools/sensitivity.py
    python tools/sensitivity.py --snr 0 12 --trials 50
    python tools/sensitivity.py --decoder hard        # the 0.1.0 decoder, for comparison

SNR is the signal power over the noise power in 40 kHz (the signal's bandwidth), in dB.
Use it to compare versions; the absolute value depends on this definition. The DC
offset, clipping and interference of a real dongle are not simulated.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
from ad5664 import parse_hex  # noqa: E402
from ad5664.demod import BurstFinder, decode_burst, decode_burst_hard  # noqa: E402

SAMPLE = ROOT / 'tests' / 'data' / 'ch1_burst.cu8'
FS = 2_400_000
BAND = 40e3
BLOCK = 600_000


def load():
    d = np.fromfile(SAMPLE, np.uint8).astype(np.float32) - 127.5
    x = (d[0::2] + 1j * d[1::2]).astype(np.complex64)
    x -= x.mean()
    e = np.abs(x).reshape(-1, 4800).mean(1)
    return x / float(np.median(e[e > e.max() * 0.5]))      # amplitude 1 while transmitting


def trial(sig, snr_db, rng, decoder):
    """Returns (detected, decoded correctly, decoded wrongly, agreeing repeats)."""
    sigma2 = 10 ** (-snr_db / 10) * FS / BAND
    pre, post = FS, int(FS * 0.6)
    x = np.zeros(pre + len(sig) + post, np.complex64)
    x[pre:pre + len(sig)] = sig * np.complex64(np.exp(1j * rng.uniform(0, 2 * np.pi)))
    n = len(x)
    x += ((rng.standard_normal(n) + 1j * rng.standard_normal(n)) * np.sqrt(sigma2 / 2)).astype(np.complex64)
    finder = BurstFinder(FS)
    found = ok = bad = agree = 0
    for i in range(0, n, BLOCK):
        for _start, iq, _snr in finder.feed(x[i:i + BLOCK]):
            found = 1
            for pk in decoder(iq, FS)[1]:
                if pk == EXPECT:
                    ok, agree = 1, agree + 1
                else:
                    bad = 1
    return found, ok, bad, agree


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--snr', type=float, nargs=2, default=(-1, 13), metavar=('LOW', 'HIGH'))
    ap.add_argument('--step', type=float, default=1.0)
    ap.add_argument('--trials', type=int, default=30)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--decoder', choices=['default', 'hard'], default='default')
    a = ap.parse_args()
    decoder = decode_burst if a.decoder == 'default' else decode_burst_hard
    sig = load()
    rng = np.random.default_rng(a.seed)
    print('SNR[dB]  detected  decoded  wrong  repeats')
    t0 = time.time()
    for snr in np.arange(a.snr[1], a.snr[0] - 1e-9, -a.step):
        r = np.array([trial(sig, snr, rng, decoder) for _ in range(a.trials)])
        print('%6.1f   %6.0f%%  %6.0f%%  %5d   %.2f'
              % (snr, 100 * r[:, 0].mean(), 100 * r[:, 1].mean(), r[:, 2].sum(), r[:, 3].mean()), flush=True)
    print('(%d tries each, %.0f s)' % (a.trials, time.time() - t0))


EXPECT = parse_hex(SAMPLE.with_suffix('.txt').read_text().split('\n')[0]).raw

if __name__ == '__main__':
    main()
