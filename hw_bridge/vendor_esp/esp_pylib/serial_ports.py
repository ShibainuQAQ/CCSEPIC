# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Serial port discovery, filtering, and sorting.

Imports from pyserial are performed at module load time;
``esp-pylib[serial]`` (or any other install that provides ``pyserial``) is
required to use this module. The `ImportError` raised on missing
pyserial is intentionally explicit so that callers (and the ``[serial]``
extra) get an actionable message instead of a confusing
``ModuleNotFoundError`` from inside the function bodies.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING
from typing import Any

from esp_pylib.constants import ESPRESSIF_VID
from esp_pylib.constants import LINUX_DEVICE_PATTERNS
from esp_pylib.constants import MACOS_DEVICE_PATTERNS
from esp_pylib.constants import MACOS_PORT_EXCLUDE_LIST
from esp_pylib.errors import NoSerialPortFoundError
from esp_pylib.errors import PortVidPidNotFoundError

try:
    from serial.tools.list_ports import comports as _comports
except ImportError as exc:  # pragma: no cover - covered indirectly through extras
    raise ImportError(
        "pyserial is required for esp_pylib.serial_ports. Install with: pip install 'esp-pylib[serial]'"
    ) from exc

if TYPE_CHECKING:
    from serial.tools.list_ports_common import ListPortInfo

__all__ = [
    'detect_port',
    'get_port_list',
    'get_port_names',
    'get_port_vid_pid',
    'parse_port_filters',
]

# CLI filter keys (e.g. ``--port-filter vid=0x303A``) are short, single-word
# identifiers. The mapping to plural keyword argument names matches the
# parameter names of `get_port_list` so callers can do
# ``get_port_list(**parse_port_filters(...))``.
_FILTER_KEY_MAP: dict[str, str] = {
    'vid': 'vids',
    'pid': 'pids',
    'name': 'names',
    'serial': 'serials',
}


def _is_excluded(port: ListPortInfo) -> bool:
    """Return True for ports we should never expose to users.

    Currently macOS-only: hides built-in virtual ports
    (Bluetooth incoming, debug consoles, …) that are never connected to a
    real serial device but always show up in ``comports()``.
    """
    if sys.platform == 'darwin':
        device = str(port.device or '')
        return device.endswith(MACOS_PORT_EXCLUDE_LIST)
    return False


def _sort_key(port: ListPortInfo) -> tuple[int, int]:
    """Sort key: Espressif VID first, then platform-known patterns.

    `get_port_list` stable-sorts by this key so ports with equal priority keep
    their relative order from the pre-sort pass (pyserial's `comports()` order
    on Linux/Windows; reversed on macOS — see `get_port_list`). Lower tuples
    sort first; ``rank`` is negated so the highest-priority bucket ends up at
    the top of an ascending sort.
    """
    device = str(port.device or '')
    if port.vid == ESPRESSIF_VID:
        return (-2, 0)
    if sys.platform.startswith('linux'):
        patterns = LINUX_DEVICE_PATTERNS
    elif sys.platform == 'darwin':
        patterns = MACOS_DEVICE_PATTERNS
    else:
        return (0, 0)
    for i, p in enumerate(patterns):
        if p in device:
            return (-1, i)
    return (0, 0)


def get_port_list(
    vids: list[int] | None = None,
    pids: list[int] | None = None,
    names: list[str] | None = None,
    serials: list[str] | None = None,
) -> list[ListPortInfo]:
    """Enumerate serial ports, apply filters, and sort by priority.

    All filter arguments are optional. A filter is "active" only when its
    list is non-empty; an empty/``None`` filter does not exclude anything.
    Within a single filter list the matches are OR'ed (a port matches if
    *any* listed value matches), but distinct filters are AND'ed (a port
    must satisfy all active filters). String comparisons for ``names`` and
    ``serials`` are case-insensitive substring matches.

    :param vids: Filter by USB Vendor IDs (exact match).
    :param pids: Filter by USB Product IDs (exact match).
    :param names: Filter by device name substrings (case-insensitive).
    :param serials: Filter by serial number substrings (case-insensitive).
    :return: Sorted list of matching ports: Espressif-VID first, then
        platform-known patterns, then others. Within each priority bucket,
        relative order follows pyserial's `comports()` enumeration so the
        most recently attached port tends to rank first (``detect_port``
        returns ``ports[0]``). On Linux and Windows that order is preserved
        as-is; on macOS the list is reversed first because pyserial often
        lists the newest port last there. This within-bucket ordering is
        best-effort — it depends on pyserial and OS enumeration (e.g. Linux
        ``glob`` order is not guaranteed) and may vary between runs.
    """
    matched: list[ListPortInfo] = []
    for port in _comports():
        if _is_excluded(port):
            continue
        if vids and (port.vid is None or port.vid not in vids):
            continue
        if pids and (port.pid is None or port.pid not in pids):
            continue
        if names:
            device = (port.device or '').lower()
            if not any(n.lower() in device for n in names):
                continue
        if serials:
            sn = (port.serial_number or '').lower()
            if not any(s.lower() in sn for s in serials):
                continue
        matched.append(port)
    # Stable sort by priority bucket. Within each bucket, keep pyserial's
    # comports() relative order on Linux/Windows so the newest port tends to
    # rank first there. On macOS pyserial (IOKit) often lists newest last, so
    # reverse first. Ordering is best-effort — not guaranteed across runs.
    if sys.platform == 'darwin':
        matched.reverse()
    matched.sort(key=_sort_key)
    return matched


def get_port_names(**filters: Any) -> list[str]:
    """Return matching ``port.device`` strings (sorted, filtered).

    Convenience wrapper around `get_port_list` for callers that only
    need device paths (e.g. shell completion).
    """
    return [str(port.device or '') for port in get_port_list(**filters)]


def detect_port(**filters: Any) -> str:
    """Return the device path of the highest-priority matching port.

    :raises NoSerialPortFoundError: if no port matches the given filters.
    """
    ports = get_port_list(**filters)
    if not ports:
        raise NoSerialPortFoundError('No serial ports found. Check the connection and the device drivers.')
    # ``port.device`` is typed as ``Any`` because pyserial has no stubs;
    # the explicit ``str(...)`` guarantees the return type and matches the signature.
    return str(ports[0].device)


def get_port_vid_pid(port_name: str | None) -> tuple[int | None, int | None]:
    """Look up the USB ``(VID, PID)`` for ``port_name`` via `comports`.

    Raises `PortVidPidNotFoundError` when the lookup itself can't
    proceed: empty / missing port name, a pyserial URL handler
    (``rfc2217://``) which by design has no USB
    identity, or a well-formed ``COM*`` / absolute POSIX path that is not
    listed by `comports`. Each failure mode carries its own
    descriptive message so logs can disambiguate them without parsing.

    When the port **is** found, the function returns its ``vid`` / ``pid``
    if both fields are available. Candidate paths with missing USB metadata
    are skipped so another path for the same device can still provide the full
    identity; if no candidate has IDs, the legacy ``(None, None)`` result is
    returned for the listed port.

    :param port_name: Device path as reported by pyserial (e.g.
        ``/dev/cu.usbserial-1410``, ``/host_dev/ttyUSB0``, or ``COM3``).
    :returns: ``(vid, pid)`` as reported by pyserial.
    :raises PortVidPidNotFoundError: when the port can't be looked up at
        all (empty name, URL handler, symlink with a missing target,
        port not listed by `comports`).

    The function performs two platform-specific fix-ups so the lookup
    matches the device the tool will actually open:

    * If the requested path is a symlink (`os.path.islink`), the resolved
      path and the original path are both matched against comports. The
      resolved path is tried first so regular udev aliases prefer the
      backing device metadata, while the original path still covers Docker
      setups where ``/dev/ttyUSB0`` points at a bind-mounted
      ``/host_dev/ttyUSB0``. A stale udev alias whose target node is gone
      is treated like any other path not listed by comports.
    * macOS ``/dev/tty.*`` paths fall through to the matching ``/dev/cu.*``
      device, because outgoing communication on macOS goes through the
      "call-up" device while users (and udev rules) often hand us the
      ``tty`` name.
    """
    if not port_name:
        raise PortVidPidNotFoundError('Cannot resolve VID/PID: no port name provided.')
    if not port_name.lower().startswith('com') and not os.path.isabs(port_name):
        raise PortVidPidNotFoundError(
            f'Cannot resolve VID/PID for {port_name!r}: only COM* and absolute device paths are supported '
            '(pyserial URL handlers have no USB identity).'
        )
    lookup_ports = [port_name]
    if os.path.islink(port_name):
        resolved = os.path.realpath(port_name)
        if os.path.exists(resolved) and resolved != port_name:
            lookup_ports.insert(0, resolved)

    if sys.platform == 'darwin':
        lookup_ports += [p.replace('tty', 'cu') for p in lookup_ports if 'tty' in p]

    ports = list(_comports())
    found_without_ids = False
    for lookup_port in lookup_ports:
        for port in ports:
            if port.device != lookup_port:
                continue
            if port.vid is not None and port.pid is not None:
                return port.vid, port.pid
            found_without_ids = True
    if found_without_ids:
        return None, None
    raise PortVidPidNotFoundError(
        f'Cannot resolve VID/PID for {port_name!r}: not listed by pyserial. '
        'The device may be unplugged or hidden from this process.'
    )


def parse_port_filters(values: tuple[str, ...]) -> dict[str, list[Any]]:
    """Parse ``key=value`` strings produced by ``--port-filter`` flags.

    Supported keys: ``vid``, ``pid``, ``name``, ``serial``. ``vid`` and ``pid``
    are parsed via `int(..., 0)` so users can pass decimal, hex
    (``0x303A``), octal, or binary literals interchangeably.

    The returned dict is shaped to splat directly into `get_port_list`,
    e.g. ``get_port_list(**parse_port_filters(("vid=0x303A",)))``. All four
    keys are present (with empty lists when not specified) so callers do not
    have to defensively check for missing keys.
    """
    result: dict[str, list[Any]] = {key: [] for key in _FILTER_KEY_MAP.values()}
    for value in values:
        if '=' not in value:
            raise ValueError(f'Invalid port filter format: {value!r}. Expected key=value.')
        key, raw = value.split('=', 1)
        key = key.strip().lower()
        if key not in _FILTER_KEY_MAP:
            raise ValueError(f'Unknown port filter key: {key!r}. Valid keys: {sorted(_FILTER_KEY_MAP)}.')
        target = _FILTER_KEY_MAP[key]
        raw = raw.strip()
        if key in ('vid', 'pid'):
            try:
                result[target].append(int(raw, 0))
            except ValueError as exc:
                raise ValueError(f'Invalid integer for port filter {key!r}: {raw!r}.') from exc
        else:
            result[target].append(raw)
    return result
