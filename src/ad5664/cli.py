"""Command line tool.

    ad5664 listen                  receive live with rtl_sdr (gain chosen automatically)
    ad5664 scan                    find where the signal appears (for tuners other than FC0013)
    ad5664 decode capture.cu8      decode a recording made with rtl_sdr
    ad5664 parse "2A F7 00 32 ..." decode packet bytes received some other way
"""
import argparse
import csv
import json
import logging
import signal
import statistics
import sys
import time

from .packet import parse_hex, plausible

CSV_FIELDS = ['time', 'channel', 'sensor_id', 'temperature', 'humidity', 'wbgt', 'vh',
              'next_tx_s', 'fast_mode', 'repeats', 'snr_db', 'gain', 'offset_hz', 'raw']


def _text(d, live):
    t = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(d['time'])) if live else '%9.3f s' % d['time']
    extra = ''
    if d.get('snr_db') is not None:
        extra += '  %3.0f dB' % d['snr_db']
    if d['fast_mode']:
        extra += '  (fast mode)'
    return ('%s  CH%d  %5.1f C  %3d %%  WBGT %4.1f  VH %4.1f  ID %s  x%d%s' %
            (t, d['channel'], d['temperature'], d['humidity'], d['wbgt'], d['vh'], d['sensor_id'],
             d['repeats'], extra))


def _output(receptions, fmt, live):
    w = None
    if fmt == 'csv':
        w = csv.DictWriter(sys.stdout, CSV_FIELDS, extrasaction='ignore')
        w.writeheader()
    for r in receptions:
        d = r.to_dict()
        if fmt == 'json':
            print(json.dumps(d, ensure_ascii=False))
        elif fmt == 'csv':
            w.writerow(d)
        else:
            print(_text(d, live))
        sys.stdout.flush()


def _scan(receptions, freq):
    found = {}
    for r in receptions:
        print('CH%d  offset %+8.1f kHz  (%.4f MHz)  %5.1f C  %3d %%' % (
            r.reading.channel, r.offset_hz / 1e3, (freq + r.offset_hz) / 1e6,
            r.reading.temperature, r.reading.humidity))
        sys.stdout.flush()
        found.setdefault(r.reading.channel, []).append(r.offset_hz)
    if not found:
        print('No transmission found. Put a sensor close to the antenna, or re-insert its '
              'battery (it then transmits every 15 s for 2 minutes), and try again.')
        return 1
    off = statistics.median(o for v in found.values() for o in v)
    chs = ','.join(str(c) for c in sorted(found))
    print()
    if abs(off) <= 20e3:
        print('Found CH%s near the centre (%+.1f kHz). No --offset is needed:' % (chs, off / 1e3))
        print('  ad5664 listen -f %.0f' % freq)
    else:
        print('Found CH%s at %+.1f kHz. Use the same -f and -s with --offset, for example:' % (chs, off / 1e3))
        print('  ad5664 listen -f %.0f --offset %.0f' % (freq, round(off, -3)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ad5664', description='A&D AD-5664 sensor decoder (unofficial)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    fmt = argparse.ArgumentParser(add_help=False)
    fmt.add_argument('--format', choices=['text', 'json', 'csv'], default='text')
    fmt.add_argument('-v', '--verbose', action='count', default=0,
                     help='log to stderr (-v: gain selection and status every 10 min, -vv: every burst)')

    rf = argparse.ArgumentParser(add_help=False)
    rf.add_argument('-f', '--freq', type=float, default=None, help='tuner frequency in Hz (default 429.9e6)')
    rf.add_argument('-s', '--rate', type=float, default=None, help='sample rate (default 2.4e6)')
    rf.add_argument('-d', '--device', type=int, default=None, help='rtl_sdr device index')
    rf.add_argument('-p', '--ppm', type=int, default=None, help='frequency correction in ppm')

    p = sub.add_parser('listen', parents=[fmt, rf], help='receive live with rtl_sdr')
    p.add_argument('-g', '--gain', type=float, default=None,
                   help='fix the tuner gain in dB (default: choose automatically from --gains)')
    p.add_argument('--gains', default='6.5,-6.3,19.7',
                   help='gains to try when choosing automatically, comma separated (default 6.5,-6.3,19.7)')
    p.add_argument('--scan-sec', type=float, default=None, help='seconds to try each gain (default 140)')
    p.add_argument('--offset', type=float, default=0.0,
                   help='where the signal appears relative to -f, in Hz (see "ad5664 scan"; default 0)')

    p = sub.add_parser('scan', parents=[rf], help='find where the signal appears in the received band')
    p.add_argument('-g', '--gain', type=float, default=19.7, help='tuner gain in dB (default 19.7)')
    p.add_argument('-t', '--seconds', type=float, default=150, help='how long to listen (default 150)')
    p.add_argument('--file', default=None, help='scan a recording instead of running rtl_sdr')

    p = sub.add_parser('decode', parents=[fmt], help='decode an rtl_sdr recording (.cu8 or .cu8.gz)')
    p.add_argument('file')
    p.add_argument('-s', '--rate', type=float, default=None, help='sample rate of the file (default 2.4e6)')
    p.add_argument('--offset', type=float, default=0.0, help='signal offset from the centre in Hz (default 0)')

    p = sub.add_parser('parse', help='decode 17 packet bytes given as hex')
    p.add_argument('hex', nargs='+')
    p.add_argument('--no-check', action='store_true', help='do not verify the checksum')

    a = ap.parse_args(argv)
    if hasattr(signal, 'SIGTERM'):
        # exit normally on SIGTERM (systemd, timeout) so that rtl_sdr is stopped too
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        if a.cmd == 'parse':
            r = parse_hex(''.join(a.hex), check=not a.no_check)
            d = r.to_dict()
            d['plausible'] = plausible(r.raw)
            print(json.dumps(d, ensure_ascii=False, indent=2))
            return 0
        from . import receiver
        level = {0: logging.WARNING, 1: logging.INFO}.get(getattr(a, 'verbose', 1), logging.DEBUG)
        logging.basicConfig(level=level, format='%(asctime)s %(message)s', datefmt='%H:%M:%S',
                            stream=sys.stderr)
        rate = a.rate or receiver.DEFAULT_RATE
        if a.cmd == 'decode':
            _output(receiver.read_file(a.file, rate, a.offset), a.format, live=False)
        elif a.cmd == 'scan':
            freq = a.freq or receiver.DEFAULT_FREQ
            if not a.file:
                print('Listening for %.0f s at %.4f MHz, %.2f MS/s, gain %g dB ...'
                      % (a.seconds, freq / 1e6, rate / 1e6, a.gain), file=sys.stderr)
            src = receiver.scan(freq, rate, a.gain, a.seconds, a.device, a.ppm, path=a.file)
            return _scan(src, freq)
        else:
            gains = [float(v) for v in a.gains.split(',')]
            src = receiver.listen(a.freq or receiver.DEFAULT_FREQ, rate, a.gain, a.device, a.ppm,
                                  offset_hz=a.offset, gains=gains,
                                  scan_s=a.scan_sec or receiver.SCAN_SECONDS)
            _output(src, a.format, live=True)
    except KeyboardInterrupt:
        pass
    except (ValueError, FileNotFoundError) as e:
        print('error: %s' % e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
