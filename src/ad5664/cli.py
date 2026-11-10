"""Command line tool.

    ad5664 listen                  receive live with rtl_sdr
    ad5664 decode capture.cu8      decode a recording made with rtl_sdr
    ad5664 parse "2A F7 00 32 ..." decode packet bytes received some other way
"""
import argparse
import csv
import json
import sys
import time

from .packet import parse_hex

CSV_FIELDS = ['time', 'channel', 'sensor_id', 'temperature', 'humidity', 'wbgt', 'vh',
              'next_tx_s', 'fast_mode', 'repeats', 'offset_hz', 'raw']


def _text(d, live):
    t = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(d['time'])) if live else '%9.3f s' % d['time']
    return ('%s  CH%d  %5.1f C  %3d %%  WBGT %4.1f  VH %4.1f  ID %s%s' %
            (t, d['channel'], d['temperature'], d['humidity'], d['wbgt'], d['vh'], d['sensor_id'],
             '  (fast mode)' if d['fast_mode'] else ''))


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


def main(argv=None):
    ap = argparse.ArgumentParser(prog='ad5664', description='A&D AD-5664 sensor decoder (unofficial)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    fmt = argparse.ArgumentParser(add_help=False)
    fmt.add_argument('--format', choices=['text', 'json', 'csv'], default='text')

    p = sub.add_parser('listen', parents=[fmt], help='receive live with rtl_sdr')
    p.add_argument('-f', '--freq', type=float, default=None, help='tuner frequency in Hz (default 429.9e6)')
    p.add_argument('-s', '--rate', type=float, default=None, help='sample rate (default 2.4e6)')
    p.add_argument('-g', '--gain', type=float, default=None, help='tuner gain in dB (default: auto)')
    p.add_argument('-d', '--device', type=int, default=None, help='rtl_sdr device index')
    p.add_argument('-p', '--ppm', type=int, default=None, help='frequency correction in ppm')

    p = sub.add_parser('decode', parents=[fmt], help='decode an rtl_sdr recording (.cu8)')
    p.add_argument('file')
    p.add_argument('-s', '--rate', type=float, default=None, help='sample rate of the file (default 2.4e6)')

    p = sub.add_parser('parse', help='decode 17 packet bytes given as hex')
    p.add_argument('hex', nargs='+')
    p.add_argument('--no-check', action='store_true', help='do not verify the checksum')

    a = ap.parse_args(argv)
    try:
        if a.cmd == 'parse':
            r = parse_hex(''.join(a.hex), check=not a.no_check)
            print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
            return 0
        from . import receiver
        rate = a.rate or receiver.DEFAULT_RATE
        if a.cmd == 'decode':
            _output(receiver.read_file(a.file, rate), a.format, live=False)
        else:
            src = receiver.listen(a.freq or receiver.DEFAULT_FREQ, rate, a.gain, a.device, a.ppm)
            _output(src, a.format, live=True)
    except KeyboardInterrupt:
        pass
    except (ValueError, FileNotFoundError) as e:
        print('error: %s' % e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
