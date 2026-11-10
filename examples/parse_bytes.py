"""Decode packet bytes obtained some other way (e.g. a CC1101 / RFM69 module or rtl_433).

No numpy or SDR needed: ad5664.packet is pure Python.

Radio settings for a packet radio: 2-FSK, 10 kbit/s, deviation ~11 kHz,
preamble AA AA ..., sync word 2D D4, fixed length 17 bytes. The last byte is a simple
sum checksum, not a CRC, so turn the radio's CRC check off and use ad5664.verify().
"""
import ad5664

packet = bytes.fromhex('2A F7 00 32 12 03 00 00 00 00 EC 00 8D 00 C7 03 AB')

if ad5664.verify(packet):
    r = ad5664.parse(packet)
    print(r)
    print('channel', r.channel, 'temperature', r.temperature, 'humidity', r.humidity)
else:
    print('bad checksum')
