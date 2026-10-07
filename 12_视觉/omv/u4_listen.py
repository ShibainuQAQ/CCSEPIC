# u4_listen.py -- run on CanMV/K210: listen on UART2 (the board's TXD/RXD header) and print
#                 whatever the STM32 sends us.  Used to prove the STM32 -> K210 direction.
# Pure ASCII on purpose.

import time

from machine import UART

WAIT_MS = 15000

u = UART(UART.UART2, baudrate=115200)
print("LISTEN start (UART2, 15s)")

t0 = time.ticks_ms()
n_rx = 0
while time.ticks_diff(time.ticks_ms(), t0) < WAIT_MS:
    try:
        n = u.any()
        if n:
            data = u.read(n)
            n_rx += len(data)
            print("RX", data)
    except Exception as exc:
        print("RX-ERR", repr(exc))
    time.sleep_ms(50)

print("LISTEN done bytes=%d" % n_rx)
