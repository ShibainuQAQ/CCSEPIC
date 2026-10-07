# restore_flash_main.py -- run on CanMV/K210: put /flash/main.py back.
#
# Why: when disabling the auto-run scripts, BOTH were renamed.  But /flash/main.py is the
# board's OWN init (it loads config.json and sets up FPIOA pin mapping).  Without it,
# UART(UART.UART2) falls back to the chip's default pins (IO6/IO7) instead of the board's
# TXD/RXD header -- so script writes never appeared on the header, while print() (console)
# still did.  /sd/main.py stays disabled (that was the flooding user script).

import os

p = "/flash/main.py"
try:
    os.rename(p + ".disabled", p)
    print("RESTORED " + p)
except Exception as exc:
    print("RESTORE-ERR " + repr(exc))

for d in ("/flash", "/sd"):
    try:
        print("LIST " + d + " " + repr(os.listdir(d)))
    except Exception as exc:
        print("LIST-ERR " + d + " " + repr(exc))
print("RESTORE done")
