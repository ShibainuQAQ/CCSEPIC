# u2_raw.py -- run on CanMV/K210: minimal "can I write to UART2 at all" test.
#
# Background (2026-10-03): the same script that worked when UART2 was the *second* UART
# opened sent nothing when UART2 was opened and written immediately -- and the ESP32-C6
# bridge had exactly the same symptom (first write after creating a UART is swallowed).
# So: do a warm-up write and wait before the real payload.

import time

from machine import UART

u = UART(UART.UART2, baudrate=115200)

# warm-up: the first write right after construction tends to vanish
u.write(b"\r\n")
time.sleep_ms(300)

for i in range(6):
    u.write(("RAW-TEST-%d\r\n" % i).encode())
    time.sleep_ms(300)

print("RAW sent 6")
