# v4_plan.py -- run on CanMV/K210: end-to-end test of the PLAN protocol over UART2.
#
# Sends a 3-item plan, ends it, then queries it back.  All lines carry the '@' prefix
# (see firmware feed(): the vision port only accepts '@'-prefixed lines).
# Pure ASCII on purpose.

import time

from machine import UART

u = UART(UART.UART2, baudrate=115200)
u.write(b"\r\n")            # warm-up write
time.sleep_ms(200)


def send(msg, wait_ms=400):
    u.write((msg + "\r\n").encode())
    got = b""
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < wait_ms:
        try:
            n = u.any()
            if n:
                got += u.read(n)
        except Exception as exc:
            print("rx-err", repr(exc))
            break
        time.sleep_ms(20)
    print("SENT", msg, "->", got)


send("@PCLEAR")
send("@PLAN 3")
send("@PI 1 100 150 3 7 yellow-cube")
send("@PI 2 200 150 3 7 yellow-cube")
send("@PI 3 300 200 1 5 red-cylinder")
send("@PLAN END")
send("@PLAN?")
send("@PLAND")
print("V4PLAN done")
