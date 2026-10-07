# stop_main.py -- run on CanMV/K210: find and disable the auto-run script.
#
# Why: the board had a stale /sd/main.py that auto-runs on every boot and floods
# UART2 with "未识别到色块" in a tight loop -- it hogged the console UART, so every
# pushed script and every @-command exchange was drowned out.
# Renaming it (instead of deleting) keeps a copy for later inspection.

import os

print("STOPMAIN start")
for d in ("/sd", "/flash"):
    try:
        print("LIST " + d + " " + repr(os.listdir(d)))
    except Exception as exc:
        print("LIST-ERR " + d + " " + repr(exc))

for d in ("/sd", "/flash"):
    p = d + "/main.py"
    try:
        os.rename(p, p + ".disabled")
        print("RENAMED " + p + " -> " + p + ".disabled")
    except Exception as exc:
        print("SKIP " + p + " " + repr(exc))

print("STOPMAIN done")
