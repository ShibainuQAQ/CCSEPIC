# find_shorted_pins.py -- run on CanMV/K210: find which two IOs are wired together.
#
# Situation: the board's TXD and RXD header pins are physically shorted (user did it for a
# loopback test).  Whatever two IOs that is, driving one LOW makes the other read LOW.
# Scan the plausible free IOs and report the pair -- that gives us the header's real IO
# numbers, independent of any config file that might be stale.
#
# Only scans IO6..IO15 (typical free GPIOs on CanMV K210); avoids the flash/PSRAM pins
# (IO0..IO5, IO16/17) which can hang the board if driven.

import time

from fpioa_manager import fm
from machine import GPIO

CAND = [6, 7, 8, 9, 10, 11, 12, 13, 14, 15]

found = []
for drv in CAND:
    try:
        fm.register(drv, fm.fpioa.GPIOHS0, force=True)
        gdrv = GPIO(GPIO.GPIOHS0, GPIO.OUT)
        readers = []
        for i, io in enumerate(CAND):
            if io == drv:
                continue
            try:
                fm.register(io, getattr(fm.fpioa, "GPIOHS%d" % (i + 1)), force=True)
                readers.append((io, GPIO(getattr(GPIO, "GPIOHS%d" % (i + 1)),
                                         GPIO.IN, GPIO.PULL_UP)))
            except Exception as exc:
                print("reg-err %d %r" % (io, exc))
        gdrv.value(0)
        time.sleep_ms(40)
        low = [io for io, g in readers if g.value() == 0]
        gdrv.value(1)
        time.sleep_ms(40)
        if low:
            found.append((drv, low))
            print("DRIVE %d -> LOW %r" % (drv, low))
    except Exception as exc:
        print("drive-err %d %r" % (drv, exc))

print("SHORTPAIRS " + repr(found))
print("FINDSHORT done")
