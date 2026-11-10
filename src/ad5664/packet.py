"""AD-5664 packet format (no external dependencies).

A packet is the 17 bytes that follow the sync word ``2D D4``::

    byte  0-1   sensor ID. Upper 3 bits of byte 0 = channel (1-5),
                lower 13 bits are re-randomised on every battery insertion
    byte  2     flags. 0x20 = fast (15 s) transmit mode after power-on
    byte  3     relative humidity [%]
    byte  4-5   temperature: ((b5 & 0x0F) << 8 | b4) - 500, in 0.1 degC
    byte  6-8   unknown (always 0 so far)
    byte  9-10  WBGT x 10, big endian
    byte 11-12  VH (volumetric humidity) x 10 [g/m3], big endian
    byte 13     unknown (always 0 so far)
    byte 14-15  time until next transmission x 8 - 1, little endian
    byte 16     checksum: sum of bytes 0-15, lower 8 bits

See docs/protocol.md for how each field was determined and how confident we are.
"""
from dataclasses import dataclass, asdict

SYNC_WORD = 0x2DD4
PACKET_LEN = 17


def checksum(data):
    """Return the checksum of the first 16 bytes (sum, lower 8 bits)."""
    return sum(data[:16]) & 0xFF


def verify(data):
    """True if ``data`` is at least 17 bytes long and the checksum matches."""
    return len(data) >= PACKET_LEN and checksum(data) == data[16]


@dataclass(frozen=True)
class Reading:
    channel: int          # 1-5 (switch on the sensor)
    sensor_id: int        # 16-bit ID, changes on battery insertion
    flags: int            # raw byte 2
    humidity: int         # %
    temperature: float    # degC
    wbgt: float           # WBGT (heat index) as computed by the sensor
    vh: float             # volumetric humidity [g/m3] as computed by the sensor
    next_tx_s: float      # seconds until the next transmission
    raw: bytes            # the 17 packet bytes

    @property
    def fast_mode(self):
        """True while the sensor sends every 15 s (first 2 minutes after power-on)."""
        return bool(self.flags & 0x20)

    def to_dict(self):
        d = asdict(self)
        d['sensor_id'] = '%04X' % self.sensor_id
        d['raw'] = self.raw.hex(' ').upper()
        d['fast_mode'] = self.fast_mode
        return d


def parse(data, check=True):
    """Decode 17 packet bytes into a :class:`Reading`.

    ``data`` may be ``bytes``, ``bytearray`` or a list of ints. Raises ``ValueError``
    if it is too short or (when ``check`` is true) the checksum does not match.
    """
    b = bytes(data[:PACKET_LEN])
    if len(b) < PACKET_LEN:
        raise ValueError('packet too short: %d bytes (need %d)' % (len(b), PACKET_LEN))
    if check and not verify(b):
        raise ValueError('checksum mismatch: expected %02X, got %02X' % (checksum(b), b[16]))
    return Reading(
        channel=b[0] >> 5,
        sensor_id=(b[0] << 8) | b[1],
        flags=b[2],
        humidity=b[3],
        temperature=((((b[5] & 0x0F) << 8) | b[4]) - 500) / 10,
        wbgt=((b[9] << 8) | b[10]) / 10,
        vh=((b[11] << 8) | b[12]) / 10,
        next_tx_s=(((b[15] << 8) | b[14]) + 1) / 8,
        raw=b,
    )


def parse_hex(text, check=True):
    """Like :func:`parse` but takes a hex string such as ``'2A F7 00 32 ...'``."""
    return parse(bytes.fromhex(text.replace(' ', '').replace(':', '')), check=check)
