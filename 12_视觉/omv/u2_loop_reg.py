# u2_loop_reg.py -- run on CanMV/K210: UART2 self-loopback WITH explicit IO registration.
#
# Why: the earlier loopback test (link_test.py) constructed UART(UART.UART2) without
# registering the header pins, so it was talking to the chip's default pins, not the
# board's TXD/RXD header.  This board's header is IO8=TX / IO6=RX (see /flash/main.py).
#
# Test: with the header's TXD and RXD shorted together (STM32 wires disconnected!),
# write a marker and see whether it comes back.

import time

from fpioa_manager import fm
from machine import UART

fm.register(6, fm.fpioa.UART2_RX, force=True)
fm.register(8, fm.fpioa.UART2_TX, force=True)
u = UART(UART.UART2, 115200, 8, 0, 0, timeout=1000, read_buf_len=4096)

print("REGLOOP start")
u.write(b"MARKER-12345\r\n")
time.sleep_ms(300)
got = b""
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 1500:
    try:
        n = u.any()
        if n:
            got += u.read(n)
    except Exception as exc:
        got += ("<err %r>" % (exc,)).encode()
        break
    time.sleep_ms(20)
print("REGLOOP got " + repr(got))
print("REGLOOP done")
