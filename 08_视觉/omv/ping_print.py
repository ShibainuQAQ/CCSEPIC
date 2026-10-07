# ping_print.py -- run on CanMV/K210: send @JING/@VER to the STM32 through BOTH paths
#                  and see which one actually reaches it.
#
# Background (2026-10-04): on this board UART2 is the *console* UART (shared with the
# CH340/USB).  A newly constructed UART(UART.UART2) object appeared to write nowhere,
# while print() (which goes through the console) clearly reached the STM32 -- the STM32's
# v4_rx counter jumped by exactly one boot banner whenever the board reset.
# So: send with print() AND with the object, then read back and report.

import time

from machine import UART

try:
    u = UART(UART.UART2, baudrate=115200)
    print("OBJ-OK")
except Exception as exc:
    u = None
    print("OBJ-ERR " + repr(exc))

for i in range(5):
    print("@PING")                      # path A: console (UART2) via print()
    if u is not None:
        try:
            u.write(b"@VER\r\n")        # path B: explicit UART object
        except Exception as exc:
            print("W-ERR " + repr(exc))
    time.sleep_ms(250)

got = b""
if u is not None:
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
print("GOT " + repr(got))
print("PINGPRINT done")
