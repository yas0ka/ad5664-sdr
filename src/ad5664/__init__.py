"""Decoder for the A&D AD-5664 wireless thermo-hygrometer sensor (429 MHz band, Japan).

Unofficial. Not affiliated with or endorsed by A&D Company, Limited.

Packet parsing (:mod:`ad5664.packet`) has no dependencies. Demodulating I/Q samples
(:mod:`ad5664.demod`, :mod:`ad5664.receiver`) needs numpy, and live reception needs
the ``rtl_sdr`` command from rtl-sdr.
"""
from .packet import PACKET_LEN, SYNC_WORD, Reading, checksum, parse, parse_hex, verify

__version__ = '0.1.0'

__all__ = ['PACKET_LEN', 'SYNC_WORD', 'Reading', 'checksum', 'parse', 'parse_hex', 'verify',
           'listen', 'read_file']


def __getattr__(name):
    # Import the numpy-based parts only when they are used.
    if name in ('listen', 'read_file', 'Reception'):
        from . import receiver
        return getattr(receiver, name)
    raise AttributeError(name)
