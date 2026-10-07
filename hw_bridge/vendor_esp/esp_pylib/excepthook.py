# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""
Exception reporting hooks: sys.excepthook and threading.excepthook.
Reports uncaught exceptions to the IDE via WebSocket with full traceback in ``suggestion``.
"""

from __future__ import annotations

import sys
import threading
import traceback
from types import TracebackType
from typing import Any
from typing import Callable
from typing import Optional
from typing import Type
from typing import cast

from esp_pylib.ws import send_log_message

_SysExcepthook = Callable[
    [Type[BaseException], BaseException, Optional[TracebackType]],
    None,
]
_ThreadExcepthook = Callable[[Any], None]

_previous_sys_excepthook: _SysExcepthook = sys.excepthook
# (py37-drop): collapse to `_previous_thread_excepthook: _ThreadExcepthook = threading.excepthook`
_previous_thread_excepthook: _ThreadExcepthook | None = cast(
    '_ThreadExcepthook | None', getattr(threading, 'excepthook', None)
)


def _extract_location(tb: TracebackType | None) -> tuple[str | None, int | None]:
    """Return (file, line) of the innermost frame where the exception was raised.

    A traceback is a linked list whose head (``tb``) is the *outermost* frame
    that propagated the exception; ``tb.tb_next`` walks deeper toward the raise
    site. Using ``tb.tb_frame.f_lineno`` reports the call site of the outermost
    frame (which may have advanced past the call), so we walk to the innermost
    traceback entry and read ``tb_lineno`` (the line attached to the traceback,
    not the frame's mutable execution position) for IDE click-to-navigate.
    """
    if tb is None:
        return (None, None)
    while tb.tb_next is not None:
        tb = tb.tb_next
    return (tb.tb_frame.f_code.co_filename, tb.tb_lineno)


def _format_traceback(typ: type[BaseException], value: BaseException, tb: TracebackType | None) -> str | None:
    if tb is None:
        return None
    return ''.join(traceback.format_exception(typ, value, tb)).strip() or None


def _should_report(typ: type[BaseException]) -> bool:
    """Do not send SystemExit / KeyboardInterrupt to WebSocket (normal exit / Ctrl+C).

    Uses ``issubclass`` so user-defined subclasses (e.g. argparse's ``SystemExit`` chains
    or custom interrupt types) are filtered the same way the default ``sys.excepthook``
    treats them.
    """
    return not issubclass(typ, (SystemExit, KeyboardInterrupt))


def _sys_excepthook(typ: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
    """Custom sys.excepthook: report to WebSocket (and stderr), then chain to previous hook."""
    try:
        if _should_report(typ):
            file, line = _extract_location(tb)
            send_log_message(
                'exception',
                str(value) if value else '',
                _format_traceback(typ, value, tb),
                file or '<unknown>',
                line if line is not None else 0,
            )
    except Exception:
        pass
    finally:
        _previous_sys_excepthook(typ, value, tb)


# (py37-drop): tighten `args: Any` to `args: threading.ExceptHookArgs` once Python >=3.8 is required.
def _thread_excepthook(args: Any) -> None:
    """Custom threading.excepthook: report thread exceptions to WebSocket and stderr, then chain."""
    try:
        typ, value, tb = args.exc_type, args.exc_value, args.exc_traceback
        if typ is not None and _should_report(typ):
            if value is None:
                value = typ()
            file, line = _extract_location(tb)
            send_log_message(
                'exception',
                str(value) if value else '',
                _format_traceback(typ, value, tb) if tb else None,
                file or '<unknown>',
                line if line is not None else 0,
            )
    except Exception:
        pass
    finally:
        if _previous_thread_excepthook is not None:
            _previous_thread_excepthook(args)


def install_exception_reporting() -> None:
    """Install exception hooks for IDE reporting. Safe to call multiple times."""
    global _previous_sys_excepthook, _previous_thread_excepthook
    if sys.excepthook is not _sys_excepthook:
        _previous_sys_excepthook = sys.excepthook
        sys.excepthook = _sys_excepthook
    # (py37-drop): `threading.excepthook` is only available in Python 3.8+
    if hasattr(threading, 'excepthook') and threading.excepthook is not _thread_excepthook:
        _previous_thread_excepthook = cast('_ThreadExcepthook', threading.excepthook)
        threading.excepthook = _thread_excepthook
