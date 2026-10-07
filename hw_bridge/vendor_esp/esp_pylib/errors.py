# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""
Common error hierarchy for all Espressif tools.
Tools may subclass these for tool-specific errors.
"""


class FatalError(RuntimeError):
    """
    Base fatal error for all Espressif tools.
    Indicates an unrecoverable error that should terminate the tool.
    """

    pass


class NoSerialPortFoundError(FatalError):
    """Raised when no matching serial port is found."""

    pass


class PortVidPidNotFoundError(LookupError):
    """Raised by `esp_pylib.serial_ports.get_port_vid_pid` when the
    USB VID/PID of a port cannot be looked up.

    Subclasses `LookupError` rather than `FatalError` because
    a missing VID/PID is a *recoverable* condition — callers typically fall
    back to the standard reset path. A top-level ``except FatalError`` in a
    tool's CLI would otherwise convert "couldn't identify the adapter" into
    an unrelated hard exit.
    """

    pass


class ConfigError(FatalError):
    """Raised when configuration file is invalid or missing."""

    pass
