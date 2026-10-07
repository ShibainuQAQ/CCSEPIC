# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""
WebSocket client: send structured log messages to an IDE when ESP_IDE_WS is set.
Silently does nothing when the env var is unset, websockets is not installed, or the connection fails.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import TYPE_CHECKING
from typing import Any

from esp_pylib.errors import FatalError

if TYPE_CHECKING:
    from websockets.sync.client import ClientConnection

__all__ = [
    'close',
    'ensure_connected',
    'is_enabled',
    'send_event',
    'send_log_message',
    'set_ws_url',
    'wait_for_event',
]

_ENV_VAR = 'ESP_IDE_WS'

# Sentinel distinguishing "env var has not been read yet" from "env var was read and is unset".
# Caching the negative result keeps is_enabled() — called on every warn/err — truly one-shot:
# without it, every call on a CLI run with no IDE would re-acquire the lock and re-read os.environ.
_URL_UNREAD: object = object()

_ws_url: str | None = _URL_UNREAD  # type: ignore[assignment]
_connection: ClientConnection | None = None
# Guards _ws_url and _connection. The threading.excepthook integration means
# send_log_message can be invoked from any thread, so all mutation of the
# shared connection state must be serialized.
_lock = threading.Lock()


def _get_ws_url() -> str | None:
    """Return the configured WebSocket URL, reading the env var once and caching.

    Uses double-checked locking so the common (already-resolved) path stays lock-free,
    but the env-var fallback cannot race with `set_ws_url` and clobber an
    explicit URL set from another thread. Both the "found a URL" and "no URL configured"
    outcomes are cached so repeated calls from hot paths (e.g. ``is_enabled()`` in
    every warn/err) do no further env or lock work.
    """
    global _ws_url
    if _ws_url is not _URL_UNREAD:
        return _ws_url
    with _lock:
        if _ws_url is _URL_UNREAD:
            _ws_url = os.environ.get(_ENV_VAR)
        return _ws_url


def is_enabled() -> bool:
    """Return ``True`` when an IDE WebSocket URL is configured.

    Cheap to call (the cached fast path of `_get_ws_url` is just a non empty check),
    so callers can use it to skip work — such as stack walking or building rich payloads —
    that would otherwise be discarded inside the no-op branch of `send_log_message`.
    """
    return bool(_get_ws_url())


def _detach_connection() -> ClientConnection | None:
    """Detach and return the shared connection. Caller must hold ``_lock``.

    The returned connection should be closed via `_close_safely` *outside*
    the lock so a slow close handshake cannot stall other threads.
    """
    global _connection
    conn = _connection
    _connection = None
    return conn


def _close_safely(conn: ClientConnection | None) -> None:
    """Close a connection, swallowing errors. Safe to call without holding ``_lock``.

    ``websockets`` ``close()`` performs a close handshake and may block on I/O
    (or hang until socket timeouts), so it must never run inside ``_lock`` —
    otherwise other threads logging, swapping URLs, or shutting down would stall.
    """
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


def close() -> None:
    """Close the shared WebSocket connection (call on tool exit).

    Detaches under ``_lock`` and runs the actual close *outside* the lock so a slow
    close handshake cannot stall other threads logging or swapping URLs.
    """
    with _lock:
        conn = _detach_connection()
    _close_safely(conn)


def set_ws_url(url: str | None) -> None:
    """
    Set an explicit WebSocket URL (e.g. esp-idf-monitor ``--ws`` or its
    legacy ``ESP_IDF_MONITOR_WS`` env var).
    Pass ``None`` to clear the override and read ``ESP_IDE_WS`` on next use.
    """
    global _ws_url
    with _lock:
        conn = _detach_connection()
        # Restore the unread sentinel on clear so the next _get_ws_url() re-reads the
        # env var instead of caching None and never looking again.
        _ws_url = url if url is not None else _URL_UNREAD  # type: ignore[assignment]
    _close_safely(conn)


def ensure_connected(retries: int = 3, delay: float = 1.0) -> None:
    """
    Open a WebSocket connection to the configured URL, retrying on failure.
    Raises `esp_pylib.errors.FatalError` if ``websockets`` is missing, the URL is unset, or all attempts fail.
    """
    _ensure_connection(retries=retries, delay=delay, force_new=True, strict=True)


def _ensure_connection(
    *,
    retries: int = 1,
    delay: float = 0.0,
    force_new: bool = False,
    strict: bool = False,
) -> ClientConnection | None:
    """
    Return a WebSocket client for the configured URL.

    If ``force_new`` is False, an existing connection is reused when present. If True, any prior connection is
    cleared before each attempt (used by `ensure_connected`).

    If ``strict`` is True, raises `esp_pylib.errors.FatalError` when the URL is unset, ``websockets`` is
    missing, or all connection attempts fail. If False, those cases return ``None``.

    Network I/O (``connect()`` and inter-attempt ``sleep()``) runs *outside* ``_lock`` so a slow
    or hanging connect cannot block other threads from sending log messages, swapping URLs, or
    closing the module. ``_lock`` is held only for the brief initial check and the final swap.
    The trade-off: two threads racing on the very first connect may briefly open two sockets;
    the loser detects the winner during the swap and closes its own connection so nothing leaks.
    """
    global _connection
    url = _get_ws_url()
    if not url:
        if strict:
            raise FatalError(f'WebSocket not configured ({_ENV_VAR} not set)')
        return None
    try:
        from websockets.sync.client import connect
    except ImportError:
        if strict:
            raise FatalError('Please install the "websockets" package for IDE integration (esp-pylib[ide]).')
        return None

    if force_new:
        with _lock:
            stale = _detach_connection()
        _close_safely(stale)
    else:
        with _lock:
            if _connection is not None:
                return _connection

    new_conn: ClientConnection | None = None
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            new_conn = connect(url)
            break
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(delay)

    if new_conn is None:
        if strict:
            if last_error is not None:
                raise FatalError(f'Cannot connect to WebSocket server at {url}: {last_error}') from last_error
            raise FatalError(f'Cannot connect to WebSocket server at {url}')
        return None

    # Decide outcome under the lock, but defer all socket closes (which can block on I/O)
    # until after the lock is released so other threads aren't stalled.
    to_close: ClientConnection | None = None
    result: ClientConnection | None
    url_changed = False
    with _lock:
        if _ws_url != url:
            # set_ws_url() ran during connect(): our socket points at a stale URL.
            url_changed = True
            to_close = new_conn
            result = None
        elif not force_new and _connection is not None:
            # Lost a race against another thread that connected first: prefer the existing
            # connection and close ours to avoid leaking a socket.
            to_close = new_conn
            result = _connection
        else:
            # Win path: install ours and detach whatever was there (force_new pre-clear may
            # have been overwritten by another thread; with force_new we are last writer wins).
            to_close = _detach_connection()
            _connection = new_conn
            result = new_conn
    _close_safely(to_close)
    if url_changed and strict:
        raise FatalError('WebSocket URL changed during connect')
    return result


def send_log_message(
    typ: str,
    message: str,
    suggestion: str | None,
    file: str,
    line: int,
) -> None:
    """Send a structured log message to the IDE. No-op if ESP_IDE_WS is unset."""
    if not _get_ws_url():
        return
    conn = _ensure_connection()
    if conn is None:
        return

    payload = json.dumps(
        {
            'type': typ,
            'file': file,
            'line': line,
            'message': message,
            'suggestion': suggestion,
        }
    )
    try:
        conn.send(payload)
    except Exception:
        # Drop the broken connection and try once more with a fresh one.
        close()
        conn = _ensure_connection()
        if conn is not None:
            try:
                conn.send(payload)
            except Exception:
                pass


def send_event(event: str, **kwargs: Any) -> None:
    """
    Send a debug event to the IDE.
    Used by esp-idf-monitor for GDB stub and coredump coordination.

    Raises `esp_pylib.errors.FatalError` when the URL is unset, ``websockets`` is missing,
    the connection cannot be established, or the send itself fails. Each case carries its own
    message (set by `_ensure_connection` or wrapped here) so callers can distinguish
    misconfiguration from a broken IDE link.

    A ``type`` entry in ``**kwargs`` cannot overwrite the reserved envelope key; the protocol
    fields are applied last. (``event`` is already protected by Python's call semantics.)
    """
    conn = _ensure_connection(strict=True)
    assert conn is not None  # _ensure_connection(strict=True) raises rather than returning None
    payload = json.dumps({**kwargs, 'type': 'event', 'event': event})
    try:
        conn.send(payload)
    except Exception as e:
        close()
        raise FatalError(f'Failed to send event {event!r} to IDE: {e}') from e


def wait_for_event(event: str, retries: int = 3) -> dict[str, Any]:
    """
    Block until the IDE sends a message with matching event type.
    Used by esp-idf-monitor to wait for 'debug_finished' from the IDE.

    Raises `esp_pylib.errors.FatalError` when the URL is unset, ``websockets`` is missing,
    the connection cannot be established, or no matching message arrives within ``retries`` reconnects.
    """
    conn = _ensure_connection(strict=True)
    assert conn is not None  # _ensure_connection(strict=True) raises rather than returning None
    reconnect_failed = False
    for _ in range(retries):
        try:
            while True:
                raw = conn.recv()
                try:
                    msg: Any = json.loads(raw)
                except json.JSONDecodeError:
                    # Malformed payload: skip and keep waiting on the same connection.
                    # A reconnect would be wasteful since the transport is healthy.
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get('event') == event:
                    return msg
        except Exception:
            # Transport error: drop the connection and try to reopen for the next retry.
            close()
            conn = _ensure_connection()
            if conn is None:
                reconnect_failed = True
                break
    if reconnect_failed:
        raise FatalError(f'Lost WebSocket connection while waiting for event: {event}')
    raise FatalError(f'Did not receive expected event: {event}')
