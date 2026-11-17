"""Minimal example: print each reading as it arrives (needs an RTL-SDR and rtl_sdr).

    python examples/print_readings.py
"""
import logging

import ad5664

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')   # show gain selection

for rx in ad5664.listen():
    r = rx.reading
    print(f'CH{r.channel}: {r.temperature:.1f} C, {r.humidity} %, WBGT {r.wbgt:.1f}'
          f'  (next in {r.next_tx_s:.0f} s, signal {rx.snr_db:.0f} dB, gain {rx.gain} dB)')
