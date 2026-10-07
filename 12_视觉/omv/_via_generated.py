# -*- coding: utf-8 -*-
# 自动生成：PC -> K210(UART2) -> STM32 转发脚本
import time

from fpioa_manager import fm
from machine import UART

# ⚠️⚠️ 关键：这块板（CanMV_Yahboom K210）的 **TXD/RXD 排针 = IO8(TX) / IO6(RX)**，
#    必须**显式注册**给 UART2 —— 只写 UART(UART.UART2) 会落到芯片默认引脚上 ✗，
#    于是数据根本不在排针上，STM32 一个字节都收不到 ✓（这就是 2026-10-04 排查很久的坑）。
#    这一段是照抄板子自己的 /flash/main.py ✓。
fm.register(6, fm.fpioa.UART2_RX, force=True)
fm.register(8, fm.fpioa.UART2_TX, force=True)

u = UART(UART.UART2, 115200, 8, 0, 0, timeout=1000, read_buf_len=4096)
u.write(b"\r\n")
time.sleep_ms(250)

CMDS = ['@PING']

for m in CMDS:
    u.write((m + "\r\n").encode())
    got = b""
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 600:
        try:
            n = u.any()
            if n:
                got += u.read(n)
        except Exception as exc:
            got += ("<err %r>" % (exc,)).encode()
            break
        time.sleep_ms(15)
    print("SENT " + m + " -> " + repr(got))
print("VIADONE")
