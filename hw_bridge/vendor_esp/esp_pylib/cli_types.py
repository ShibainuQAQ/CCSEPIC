# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Reusable Click parameter types for Espressif tool CLIs."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

import rich_click as click
from click.shell_completion import CompletionItem

if TYPE_CHECKING:
    from click import Context
    from click import Parameter

__all__ = [
    'AnyIntType',
    'AutoSizeType',
    'BaudRateType',
    'SerialPortType',
    'arg_auto_int',
]

# Baud rates offered in shell completion (decimal strings).
COMMON_BAUD_RATES = (9600, 115200, 230400, 460800, 576000, 921600)


def arg_auto_int(x: str) -> int:
    """Parse an integer literal in decimal, hex (``0x``), octal, or binary form."""
    return int(x, 0)


class AnyIntType(click.ParamType):
    """Parse integers in any base (decimal, hex, octal, binary)."""

    name = 'integer'

    def convert(
        self,
        value: str | int,
        param: Parameter | None,
        ctx: Context | None,
    ) -> int:
        if isinstance(value, int):
            return value
        try:
            return arg_auto_int(value)
        except ValueError:
            raise click.BadParameter(f'{value!r} is not a valid integer.')


class AutoSizeType(AnyIntType):
    """Like `AnyIntType` but accepts ``k`` / ``M`` suffixes and optionally ``all``.

    Suffixes use binary kilo (1024) and mega (1024²). When ``allow_all`` is
    True (default), the literal string ``all`` is returned unchanged for
    callers that resolve size at runtime (e.g. whole-flash reads).
    """

    def __init__(self, allow_all: bool = True):
        self.allow_all = allow_all
        super().__init__()

    def convert(
        self,
        value: str | int,
        param: Parameter | None,
        ctx: Context | None,
    ) -> Any:
        if isinstance(value, int):
            return value
        if self.allow_all and value.lower() == 'all':
            return value
        if len(value) > 1 and value[-1] in ('k', 'M'):
            try:
                num = arg_auto_int(value[:-1])
            except ValueError:
                raise click.BadParameter(f'{value!r} is not a valid integer.')
            if value[-1] == 'k':
                num *= 1024
            elif value[-1] == 'M':
                num *= 1024 * 1024
            return num
        return super().convert(value, param, ctx)


class BaudRateType(click.ParamType):
    """Serial baud rate with shell completion for common values."""

    name = 'baud-rate'

    def convert(
        self,
        value: str,
        param: Parameter | None,
        ctx: Context | None,
    ) -> int:
        try:
            return int(value)
        except ValueError:
            raise click.BadParameter(f'{value!r} is not a valid baud rate.')

    def shell_complete(
        self,
        ctx: Context | None,
        param: Parameter | None,
        incomplete: str,
    ) -> list[CompletionItem]:
        return [CompletionItem(str(baud)) for baud in COMMON_BAUD_RATES if str(baud).startswith(incomplete)]


class SerialPortType(click.ParamType):
    """Click parameter type for serial-port arguments with shell completion.

    The ``convert()`` method intentionally returns the raw value unchanged.
    Validating that the port actually exists at parse time would break common
    workflows (passing an unplugged device path, ``/dev/ttyACM0`` on a
    container without device passthrough, …); validation is the tool's job
    when it tries to open the port.

    Shell completion is best-effort: if pyserial is not installed we silently
    return an empty list so completion-time errors don't surface to users.
    """

    name = 'serial-port'

    def convert(
        self,
        value: str,
        param: Parameter | None,
        ctx: Context | None,
    ) -> str:
        return value

    @staticmethod
    def _format_port_completion_help(port: Any) -> str:
        """Build a shell-completion help string from a pyserial ``ListPortInfo``.

        The format matches the convention shared across Espressif tools::

            "Description: <text>, VID: 0xVVVV, PID: 0xPPPP"

        Missing fields (``None`` / empty string, or the literal ``n/a``
        pyserial reports for ports without metadata) are silently dropped, so a
        USB device that doesn't advertise a description still shows just its
        VID/PID, and a non-USB serial port (no VID/PID metadata) shows just
        its description — or returns an empty string if nothing is known.
        """
        parts: list[str] = []
        description = getattr(port, 'description', None)
        if description and description.strip().lower() != 'n/a':
            parts.append(f'Description: {description}')
        vid = getattr(port, 'vid', None)
        if vid is not None:
            parts.append(f'VID: 0x{vid:04X}')
        pid = getattr(port, 'pid', None)
        if pid is not None:
            parts.append(f'PID: 0x{pid:04X}')
        return ', '.join(parts)

    def shell_complete(
        self,
        ctx: Context | None,
        param: Parameter | None,
        incomplete: str,
    ) -> list[CompletionItem]:
        try:
            from esp_pylib.serial_ports import get_port_list
        except ImportError:
            return []

        try:
            ports = get_port_list()
        except Exception:
            return []

        incomplete_lower = incomplete.lower()
        items = []
        for port in ports:
            device = str(port.device or '')
            if device.lower().startswith(incomplete_lower):
                items.append(CompletionItem(device, help=self._format_port_completion_help(port)))
        return items
