# ping_console.py -- run on CanMV/K210: talk to the STM32 through the *console* only.
#
# Why: this board's UART2 is the console UART (shared with the CH340 and the TXD/RXD
# header).  Constructing a second UART(UART.UART2) object fights the REPL over the same
# peripheral and is unreliable.  print() goes through the console -> out the header ->
# into the STM32, which is proven to work (the STM32 receives every boot banner).
#
# The STM32 only parses lines starting with '@' on that port, and prints
# "EVT U4_LINE <line>" to its debug UART -- that is our proof of arrival.

import time

for i in range(3):
    print("@PING")
    time.sleep_ms(600)
print("PINGCONSOLE done")
