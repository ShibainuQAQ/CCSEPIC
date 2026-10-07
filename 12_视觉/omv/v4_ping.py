# v4_ping.py -- run on CanMV/K210: talk to the STM32 over UART2 with the '@' prefix protocol.
#
# Protocol (agreed 2026-10-03):
#   K210 -> STM32 : lines starting with '@'  (e.g. "@PING", "@VIS 12 x y box img")
#                   anything WITHOUT '@' is silently ignored by the STM32 -- that is
#                   deliberate, because this UART is also the board console/REPL.
#   STM32 -> K210 : plain text lines ("OK pong", "ERR ...", "EVT ...")
#
# Pure ASCII on purpose.

import time

from machine import UART

u = UART(UART.UART2, baudrate=115200)

print("V4PING start")
probes = ["@PING", "@VER", "@POS", "@VIS 1 100 200 3 7 yellow-cube", "@NOPE 123"]
for msg in probes:
    u.write((msg + "\r\n").encode())
    time.sleep_ms(250)
    got = b""
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 400:
        try:
            n = u.any()
            if n:
                got += u.read(n)
        except Exception as exc:
            print("rx-err", repr(exc))
            break
        time.sleep_ms(20)
    print("SENT", msg, "-> GOT", got)

print("V4PING done")
