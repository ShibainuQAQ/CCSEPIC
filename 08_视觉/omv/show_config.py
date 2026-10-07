# show_config.py -- run on CanMV/K210: dump the board's own pin/UART config.
#
# Goal: find out which IO numbers the TXD/RXD header uses, so we can remap those two IOs
# to a UART that the REPL does NOT own (the console is fighting our script over UART2).

import os

for p in ("/flash/config.json", "/flash/freq.conf", "/flash/main.py"):
    print("=== " + p + " ===")
    try:
        with open(p) as f:
            txt = f.read()
        print(txt[:1200])
    except Exception as exc:
        print("ERR " + repr(exc))

try:
    import fpioa_manager
    from fpioa_manager import fm
    print("=== FPIOA functions in use ===")
    for fn in ("UART0_TX", "UART0_RX", "UART1_TX", "UART1_RX", "UART2_TX", "UART2_RX",
               "UART3_TX", "UART3_RX"):
        try:
            print(fn, getattr(fm.fpioa, fn))
        except Exception as exc:
            print(fn, "ERR", repr(exc))
except Exception as exc:
    print("FPIOA-ERR " + repr(exc))

print("SHOWCONFIG done")
