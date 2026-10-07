# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""DTR/RTS pin control and custom reset-sequence handling.

Provides the low-level DTR/RTS primitives used by Espressif tools. Tools
layer their own reset *strategies* (classic, USB-JTAG, hard, …) on top of
these primitives.

The chip-side circuit is active-low (DTR/RTS asserted drives the pin LOW),
so the boolean values used here mirror pyserial's convention rather than
the physical pin level. Use `PIN_LOW` / `PIN_HIGH` for
self-documenting call sites next to the schematic.
"""

from __future__ import annotations

import os
import struct
import time
from typing import TYPE_CHECKING
from typing import Any

from esp_pylib.constants import HARDWARE_FLOW_CONTROL_VID_PIDS

# DTR/RTS pin levels follow the active-low convention used across Espressif
# tools: setting the pin "active" (asserted) drives the line LOW, hence
# ``PIN_LOW = True``. The naming reflects the *logical* level applied via
# pyserial's ``port.dtr = True``/``port.rts = True``.
PIN_LOW = True
"""Logical state corresponding to a LOW (asserted, active) DTR/RTS line."""

PIN_HIGH = False
"""Logical state corresponding to a HIGH (deasserted, inactive) DTR/RTS line."""

DEFAULT_RESET_DELAY = 0.05
"""Default wait (seconds) before releasing the boot pin after a reset."""

MINIMAL_EN_LOW_DELAY = 0.005
"""Minimum time (seconds) the EN pin must be held low to trigger a reset."""

# Unix-only IOCTL constants for simultaneous DTR+RTS setting
# (needed because pyserial can't set both pins atomically).
# Set to ``None`` if not available to avoid import errors (e.g. Windows).
TIOCMSET = None
TIOCMGET = None
TIOCM_DTR = None
TIOCM_RTS = None
HUPCL = None
if os.name != 'nt':
    try:
        import termios

        TIOCMSET = termios.TIOCMSET
        TIOCMGET = termios.TIOCMGET
        TIOCM_DTR = termios.TIOCM_DTR
        TIOCM_RTS = termios.TIOCM_RTS
        HUPCL = getattr(termios, 'HUPCL', None)
    except (ImportError, AttributeError):
        pass

    # ``fcntl`` is Unix-only; importing it on Windows raises ImportError. Guard
    # the import so ``import esp_pylib.serial_reset`` works on Windows.
    import fcntl

if TYPE_CHECKING:
    import serial

__all__ = [
    'DEFAULT_RESET_DELAY',
    'MINIMAL_EN_LOW_DELAY',
    'PIN_HIGH',
    'PIN_LOW',
    'classic_bootloader_reset',
    'execute_custom_reset',
    'hard_reset',
    'parse_custom_reset_sequence',
    'set_dtr',
    'set_dtr_rts',
    'set_rts',
    'unix_tight_bootloader_reset',
    'usb_jtag_bootloader_reset',
    'uses_hardware_flow_control',
]


def _parse_bool_digit(arg: str) -> bool:
    """Parse a strict ``"0"`` / ``"1"`` digit into a bool.

    Used by `parse_custom_reset_sequence`. Rejects anything else
    (``"2"``, ``"-1"``, ``"true"``, …) so an obvious typo like ``D10``
    cannot silently parse as a single ``True`` write instead of two
    separate ``D1`` + ``D0`` steps.
    """
    if arg == '0':
        return False
    if arg == '1':
        return True
    raise ValueError(f"Expected '0' or '1', got {arg!r}")


def set_dtr(port: serial.Serial, state: bool) -> None:
    """Set the DTR pin state.

    ``state`` follows pyserial's convention: ``True`` asserts DTR (active),
    ``False`` deasserts. Use `PIN_LOW` / `PIN_HIGH` for
    self-documenting call sites.
    """
    port.setDTR(state)


def set_rts(port: serial.Serial, state: bool) -> None:
    """Set the RTS pin state with the Windows ``usbser.sys`` workaround.

    Some Windows USB-CDC adapters using ``usbser.sys`` only emit the
    ``SET_CONTROL_LINE_STATE`` request when both DTR and RTS are written
    in the same operation. Re-applying the *current* DTR value after
    every RTS change forces the second write to flow, keeping behavior
    consistent across platforms. The extra write is harmless on Unix,
    so we run it unconditionally rather than branching on ``sys.platform``
    (matching the proven esptool/esp-idf-monitor implementations).
    """
    port.setRTS(state)
    port.setDTR(port.dtr)


def set_dtr_rts(port: serial.Serial, dtr: bool = False, rts: bool = False) -> None:
    """Set DTR and RTS atomically via ``ioctl(TIOCMSET)``.

    pyserial cannot set both pins in a single transaction, which causes
    glitches in tight reset sequences (notably ESP32-C3/S3 USB-JTAG modes).
    On Unix we read the current modem-control bits with ``TIOCMGET``, mask
    in the desired DTR/RTS values, and write them back with ``TIOCMSET``.

    :raises NotImplementedError: when called on Windows (no ioctl).
    """
    if os.name == 'nt':
        raise NotImplementedError('Atomic DTR+RTS set is only supported on POSIX systems (requires fcntl/termios).')
    # ``TIOCM*`` are ``None`` on Windows (no ``termios``) or if ``termios`` lacks
    # these symbols on an unusual Unix build.
    if TIOCMGET is None or TIOCMSET is None or TIOCM_DTR is None or TIOCM_RTS is None:
        raise NotImplementedError('Atomic DTR+RTS set requires termios TIOCM* constants, which are not available here.')
    fd = port.fileno()
    status = struct.unpack('I', fcntl.ioctl(fd, TIOCMGET, struct.pack('I', 0)))[0]
    if dtr:
        status |= TIOCM_DTR
    else:
        status &= ~TIOCM_DTR
    if rts:
        status |= TIOCM_RTS
    else:
        status &= ~TIOCM_RTS
    fcntl.ioctl(fd, TIOCMSET, struct.pack('I', status))


def _set_hupcl(port: serial.Serial, enabled: bool) -> bool:
    """Set or clear the termios ``HUPCL`` ("hang up on close") flag.

    Hardware flow-control adapters such as the SiLabs CP2102C re-assert RTS
    when the serial port is closed. Because the CP2102C ties its CTS line to
    the chip's RTS pin, that final RTS twitch can drag ``EN`` low and reset
    the ESP32 during shutdown. Clearing ``HUPCL`` keeps the kernel from
    pulsing the modem-control lines on close, so the chip survives the close
    cleanly. The flag has no effect on adapters without that coupling, so
    leave it untouched in normal operation.

    :returns: ``True`` if ``HUPCL`` was successfully written, ``False`` on
        Windows (no ``termios``) or on POSIX builds that omit
        `termios.HUPCL`. Callers use the return value to decide
        whether a platform fallback is needed — typically deasserting DTR
        manually before close, which is how the CP2102C hard reset path on
        Windows compensates for the missing ``HUPCL`` knob.
    """
    if os.name == 'nt' or HUPCL is None:
        return False
    fd = port.fileno()
    attrs = termios.tcgetattr(fd)
    if enabled:
        attrs[2] |= HUPCL
    else:
        attrs[2] &= ~HUPCL
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return True


def parse_custom_reset_sequence(seq_str: str) -> list[dict[str, Any]]:
    """Parse a custom reset sequence string into a list of step dicts.

    Sequence format:

    * ``D<0|1>`` — call `set_dtr` with ``False``/``True``
    * ``R<0|1>`` — call `set_rts` with ``False``/``True``
    * ``U<dtr>,<rts>`` — call `set_dtr_rts` (Unix only)
    * ``W<seconds>`` — sleep for the given number of seconds (float)

    Steps are ``|``-separated. Whitespace around steps is ignored; empty
    steps are dropped (so trailing ``|`` is tolerated). The 0/1 digits
    correspond to the boolean argument passed to pyserial — they are *not*
    physical pin levels (the chip-side circuit is active-low; see module
    docstring).

    Example::

        >>> parse_custom_reset_sequence('D0|R1|W0.1|D1|R0')
        [{'type': 'dtr', 'state': False},
         {'type': 'rts', 'state': True},
         {'type': 'wait', 'duration': 0.1},
         {'type': 'dtr', 'state': True},
         {'type': 'rts', 'state': False}]

    :raises ValueError: on unknown commands or malformed arguments.
    """
    steps: list[dict[str, Any]] = []
    for raw_token in seq_str.split('|'):
        token = raw_token.strip()
        if not token:
            continue
        cmd = token[0].upper()
        arg = token[1:]
        try:
            if cmd == 'D':
                steps.append({'type': 'dtr', 'state': _parse_bool_digit(arg)})
            elif cmd == 'R':
                steps.append({'type': 'rts', 'state': _parse_bool_digit(arg)})
            elif cmd == 'U':
                # ``U<dtr>,<rts>`` takes two comma-separated 0/1 values,
                # e.g. ``U1,0`` for DTR=on, RTS=off. Strip each part so
                # human-friendly spacing like ``U1, 0`` is tolerated, matching
                # the whitespace handling already applied to the outer token.
                parts = [p.strip() for p in arg.split(',')]
                if len(parts) != 2:
                    raise ValueError(f'Expected two comma-separated values for U command, got {arg!r}.')
                steps.append(
                    {
                        'type': 'dtr_rts',
                        'dtr': _parse_bool_digit(parts[0]),
                        'rts': _parse_bool_digit(parts[1]),
                    }
                )
            elif cmd == 'W':
                steps.append({'type': 'wait', 'duration': float(arg)})
            else:
                raise ValueError(f'Unknown reset sequence command: {token!r}.')
        except ValueError as exc:
            # Re-raise with the original token in the message so users can
            # spot the bad fragment in long sequences.
            raise ValueError(f'Invalid custom reset sequence step {token!r}: {exc}') from None
    return steps


def execute_custom_reset(port: serial.Serial, seq_str: str) -> None:
    """Parse ``seq_str`` and execute it against ``port``.

    Convenience wrapper around `parse_custom_reset_sequence` plus the
    DTR/RTS primitives. Tools that need to integrate the steps with their
    own retry/error handling can call `parse_custom_reset_sequence`
    directly and dispatch the steps themselves.
    """
    for step in parse_custom_reset_sequence(seq_str):
        kind = step['type']
        if kind == 'dtr':
            set_dtr(port, step['state'])
        elif kind == 'rts':
            set_rts(port, step['state'])
        elif kind == 'dtr_rts':
            set_dtr_rts(port, step['dtr'], step['rts'])
        elif kind == 'wait':
            time.sleep(step['duration'])
        else:  # pragma: no cover - parse_custom_reset_sequence guards this
            raise ValueError(f'Unknown step kind in parsed sequence: {kind!r}.')


def uses_hardware_flow_control(vid_pid: tuple[int | None, int | None]) -> bool:
    """Return ``True`` if ``vid_pid`` belongs to a hardware-flow-control adapter.

    Decides whether the reset sequences should run in their ``flow_control=True``
    variant — i.e. whether the adapter behaves like a SiLabs CP2102C and
    couples CTS to the chip's RTS.
    """
    return vid_pid in HARDWARE_FLOW_CONTROL_VID_PIDS


# ---------------------------------------------------------------------------
# Named reset sequences
#
# These are the actual DTR/RTS pulse trains used to drive Espressif chips into
# the bootloader or to issue a hard reset. They were duplicated (with subtle
# drift) between ``esptool/reset.py`` and ``esp_idf_monitor/base/reset.py``;
# moving them here gives both tools a single source of truth for the *what*
# while letting each tool keep its own *how* (retry policy, strategy
# selection, config plumbing). Timings are accepted as parameters so callers
# can pass per-chip values (esp-idf-monitor's ``chip_config``) or fall back
# to esptool's historical constants via the defaults.
# ---------------------------------------------------------------------------


def classic_bootloader_reset(
    port: serial.Serial,
    enter_boot_delay: float = 0.1,
    reset_delay: float = DEFAULT_RESET_DELAY,
    flow_control: bool = False,
) -> None:
    """Sequential DTR/RTS bootloader reset (the historical, portable sequence).

    Drives ``IO0`` low while pulsing ``EN``, then releases both. ``DTR`` and
    ``RTS`` are written one at a time (no atomic transition) which works on
    every platform but can briefly glitch through invalid (DTR, RTS) pairs.
    Use `unix_tight_bootloader_reset` instead when atomic transitions
    matter (notably for ESP32-C3/S3 in USB-JTAG mode).

    :param enter_boot_delay: Time to hold the chip in reset before releasing
        EN. Default ``0.1`` matches esptool. esp-idf-monitor passes
        ``chip_config['enter_boot_set']``.
    :param reset_delay: Time after releasing EN before deasserting IO0.
        Default `DEFAULT_RESET_DELAY` (``0.05``) matches esptool.
        esp-idf-monitor passes ``chip_config['enter_boot_unset']``.
    :param flow_control: Set ``True`` for adapters with always-on hardware
        flow control (e.g. the SiLabs CP2102C). Those adapters wire CTS to
        the chip's RTS, so toggling DTR after the chip has booted would
        re-trigger RTS on incoming UART data and glitch ``EN`` low. The
        trailing ``IO0=HIGH`` write is therefore skipped in that mode.
    """
    set_dtr(port, PIN_HIGH)  # IO0=HIGH
    set_rts(port, PIN_LOW)  # EN=LOW, chip in reset
    time.sleep(enter_boot_delay)
    set_dtr(port, PIN_LOW)  # IO0=LOW
    set_rts(port, PIN_HIGH)  # EN=HIGH, chip out of reset
    time.sleep(reset_delay)
    if not flow_control:
        set_dtr(port, PIN_HIGH)  # IO0=HIGH, done


def unix_tight_bootloader_reset(
    port: serial.Serial,
    enter_boot_delay: float = 0.1,
    reset_delay: float = DEFAULT_RESET_DELAY,
    flow_control: bool = False,
) -> None:
    """Atomic-DTR/RTS bootloader reset for POSIX systems.

    Uses `set_dtr_rts` (``ioctl(TIOCMSET)``) so each transition writes
    both modem-control bits in a single syscall, avoiding the brief invalid
    pin pairs that the sequential `classic_bootloader_reset` walks
    through. This is the recommended entry path on Linux/macOS.

    :param flow_control: Set ``True`` for adapters with always-on hardware
        flow control (e.g. the SiLabs CP2102C). See
        `classic_bootloader_reset` for why the trailing ``IO0=HIGH``
        writes are skipped in that mode.

    :raises NotImplementedError: when called on Windows (no ``ioctl``).
        Tools should fall back to `classic_bootloader_reset` there.
    """
    set_dtr_rts(port, PIN_HIGH, PIN_HIGH)
    set_dtr_rts(port, PIN_LOW, PIN_LOW)
    set_dtr_rts(port, PIN_HIGH, PIN_LOW)  # IO0=HIGH & EN=LOW, chip in reset
    time.sleep(enter_boot_delay)
    set_dtr_rts(port, PIN_LOW, PIN_HIGH)  # IO0=LOW & EN=HIGH, chip out of reset
    time.sleep(reset_delay)
    if not flow_control:
        set_dtr_rts(port, PIN_HIGH, PIN_HIGH)  # IO0=HIGH, done
        # Some hubs/adapters drop the final IO0 transition; the trailing
        # single-pin write makes the line state observable and matches the
        # proven esptool path.
        set_dtr(port, PIN_HIGH)


def usb_jtag_bootloader_reset(port: serial.Serial, settle_delay: float = 0.1) -> None:
    """Bootloader reset sequence for the USB-Serial-JTAG peripheral.

    The internal USB-Serial-JTAG bridge (PID 0x1001 on chips like ESP32-C3,
    S3, C6, P4) reacts to a different pulse train than an external UART
    bridge. The trailing ``setRTS`` writes are intentionally ordered to walk
    through ``(1, 1)`` instead of ``(0, 0)`` because Windows ``usbser.sys``
    only flushes ``SET_CONTROL_LINE_STATE`` on an RTS change.

    :param settle_delay: Time held between phase transitions. Both esptool
        and esp-idf-monitor use ``0.1``; exposed for symmetry with the other
        sequences and for tests that want to skip waiting.
    """
    set_rts(port, PIN_HIGH)
    set_dtr(port, PIN_HIGH)  # Idle
    time.sleep(settle_delay)
    set_dtr(port, PIN_LOW)  # Set IO0
    set_rts(port, PIN_HIGH)
    time.sleep(settle_delay)
    set_rts(port, PIN_LOW)  # Reset (calls inverted to traverse (1,1) instead of (0,0))
    set_dtr(port, PIN_HIGH)
    set_rts(port, PIN_LOW)  # RTS re-write so Windows propagates the DTR change
    time.sleep(settle_delay)
    set_dtr(port, PIN_HIGH)
    set_rts(port, PIN_HIGH)  # Chip out of reset


def hard_reset(
    port: serial.Serial,
    hold_delay: float = 0.1,
    post_release_delay: float = 0.0,
    flow_control: bool = False,
) -> None:
    """Pulse ``EN`` low to hard-reset the chip.

    Used both to bounce the chip out of the bootloader and to restart a
    running app. The ``post_release_delay`` parameter exists for chips
    talking over their internal USB peripheral: the device disappears from
    the bus while reset is asserted and needs a moment to re-enumerate
    before any further DTR/RTS writes will take effect.

    :param hold_delay: Time ``EN`` is held low. esptool uses ``0.1`` for
        UART bridges and ``0.2`` for chips on the internal USB peripheral;
        esp-idf-monitor uses ``chip_config['reset']``. Ignored when
        ``flow_control`` is ``True`` — that path uses fixed ``0.1`` waits
        to match the proven esptool sequence.
    :param post_release_delay: Extra wait after raising ``EN`` before
        returning. Use ``0.2`` when ``hold_delay`` is the USB ``0.2``;
        leave at ``0.0`` for UART bridges where no re-enumeration is needed.
        Ignored when ``flow_control`` is ``True``.
    :param flow_control: Set ``True`` for adapters with always-on hardware
        flow control (e.g. the SiLabs CP2102C). Those adapters re-assert
        RTS when the port closes, which can drag ``EN`` low and reset the
        chip during shutdown. The flow-control path therefore clears the
        ``HUPCL`` termios flag so the kernel does not pulse the
        modem-control lines on close, keeps DTR asserted across close so
        any RTS twitch carries DTR with it, and on Windows (where
        ``HUPCL`` is unavailable) deasserts DTR before close to settle the
        reset circuit.
    """
    if flow_control:
        has_hupcl = _set_hupcl(port, False)
        set_dtr(port, PIN_HIGH)  # IO0=HIGH
        set_rts(port, PIN_LOW)  # EN=LOW
        time.sleep(0.1)
        set_dtr(port, PIN_LOW)
        time.sleep(0.1)
        set_rts(port, PIN_HIGH)  # EN=HIGH
        if not has_hupcl:
            set_dtr(port, PIN_HIGH)
        return
    set_rts(port, PIN_LOW)  # EN=LOW
    time.sleep(hold_delay)
    set_rts(port, PIN_HIGH)  # EN=HIGH, chip out of reset
    if post_release_delay:
        time.sleep(post_release_delay)
