# link_test.py -- run on CanMV/K210: identify which UART the board's TXD/RXD header is,
#                and test the link to the STM32 (UART4 on PC10/PC11).
#
# Pure ASCII on purpose: the board REPL mangles multi-byte chars.
#
# Two things it answers:
#   1) If you SHORT the board's TXD<->RXD pins with a jumper, exactly one UART will read
#      back its own label -> that is the UART behind that header.  ("LOOPBACK UARTn got ...")
#   2) With the header wired to the STM32, the STM32's debug port prints
#      "EVT U4_LINE I-AM-UARTn 12345" -> same answer, without the jumper.
#
# PC side:
#   D:\python\python.exe 12_视觉\shot.py --port COM10 --script 12_视觉\omv\link_test.py
#   (shot.py will complain "no B64" at the end -- ignore it, we only want the printed log)

import time

from machine import UART

print("LINKTEST start")

cands = []
try:
    cands.append(("UART1", UART.UART1))
except Exception:
    pass
try:
    cands.append(("UART2", UART.UART2))
except Exception:
    pass
try:
    cands.append(("UART3", UART.UART3))
except Exception:
    pass

opened = []
for name, uid in cands:
    try:
        u = UART(uid, baudrate=115200)
        opened.append((name, u))
        print("opened", name)
    except Exception as exc:
        print("open-fail", name, repr(exc))

for name, u in opened:
    msg = ("I-AM-" + name + " 12345\r\n").encode()
    for _ in range(3):
        try:
            u.write(msg)
        except Exception as exc:
            print("write-fail", name, repr(exc))
        time.sleep_ms(150)
    time.sleep_ms(300)
    got = b""
    try:
        n = u.any()
        if n:
            got = u.read(n)
    except Exception as exc:
        print("read-fail", name, repr(exc))
    if got:
        print("LOOPBACK", name, "got", got)
    else:
        print("no-loopback", name)

# Leave all candidate UARTs open and echo anything the STM32 sends back for a few seconds.
print("echo-window 6s (send something from the STM32 now)")
t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 6000:
    for name, u in opened:
        try:
            n = u.any()
            if n:
                print("RX", name, u.read(n))
        except Exception:
            pass
    time.sleep_ms(50)

print("LINKTEST done")
