import gzip
from pathlib import Path

from ad5664.receiver import GainSelector, scan

LOWGAIN = Path(__file__).parent / 'data' / 'ch5_lowgain.cu8.gz'


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_gain_selector_picks_most_channels_then_repeats():
    s = GainSelector([6.5, -6.3, 19.7], scan_s=140, lost_s=600, clock=Clock())
    assert s.plan() == (6.5, 140)
    s.on_reception(1, 3, 20)
    s.window_done()
    assert s.plan() == (-6.3, 140)
    s.on_reception(1, 3, 15)
    s.on_reception(2, 2, 12)
    s.window_done()
    s.on_reception(1, 3, 30)
    s.on_reception(2, 3, 25)
    s.window_done()
    assert s.plan() == (19.7, None)          # two channels and more agreeing repeats


def test_gain_selector_prefers_margin_on_tie():
    s = GainSelector([6.5, 19.7], clock=Clock())
    s.on_reception(1, 3, 12)
    s.window_done()
    s.on_reception(1, 3, 30)
    s.window_done()
    assert s.plan() == (19.7, None)


def test_gain_selector_rescans_when_channel_lost():
    c = Clock()
    s = GainSelector([6.5, 19.7], scan_s=140, lost_s=600, clock=c)
    s.on_reception(3, 3, 20)
    s.window_done()
    s.window_done()
    assert s.plan() == (6.5, None)
    c.t = 300
    s.on_reception(3, 3, 20)
    c.t = 800
    assert not s.should_rescan()
    c.t = 1000
    assert s.should_rescan()
    assert s.plan() == (6.5, 140)


def test_gain_selector_keeps_scanning_when_nothing_received():
    s = GainSelector([6.5, 19.7], clock=Clock())
    s.window_done()
    s.window_done()
    assert s.plan() == (6.5, 140)


def test_scan_reports_offset(tmp_path):
    f = tmp_path / 'x.cu8'
    f.write_bytes(gzip.open(LOWGAIN).read())
    rx = list(scan(path=str(f)))
    assert len(rx) == 1
    assert rx[0].reading.channel == 5
    assert abs(rx[0].offset_hz) < 20e3
