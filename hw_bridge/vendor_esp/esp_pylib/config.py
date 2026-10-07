# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Config file loader for Espressif tools.

Each tool instantiates `ToolConfig` with its own section name,
candidate filenames, and override env var; the class handles search,
validation, parsing, caching, and verbose user-facing logging uniformly.

Search order (first match wins):

1. Path from the override env var (when set; missing file or wrong section
   raises `esp_pylib.errors.ConfigError`, unless ``permissive_env_var``
   is enabled — see below).
2. Current working directory.
3. OS-specific user config directory:
   * POSIX: ``~/.config/<section_name>/``
   * Windows: ``~/AppData/Local/<section_name>/``
4. User home directory.

Within each search directory, ``config_filenames`` are tried in the order
given. A file is considered a match only if it parses successfully *and*
contains the tool's section — this is what allows shared filenames like
``setup.cfg`` or ``tox.ini`` to be listed alongside a tool-specific
``<tool>.cfg`` without falsely claiming an unrelated file.

Verbose logging
---------------
Pass ``verbose=True`` to surface progress and warnings to the user. The
loader then emits via `EspLogBase.warn` and `EspLogBase.print`
on a logger object — by default `esp_pylib.logger.log` (a proxy
that dispatches to ``EspLog.instance``), but consumer tools should pass
their own `EspLogBase` instance via the
``logger`` parameter so the wiring is *explicit* and doesn't rely on
import-order side effects to set up the singleton.

The flag gates *all* loader output, including warnings about invalid
candidate files and unknown options. This is intentional: tools typically
load their config at module-import time (e.g. to populate default
constants), and unconditional output there would surprise library users
and can also produce duplicate messages if the same config is later
re-loaded from a CLI entry-point. Pair ``verbose=True`` with
``valid_options`` to get an automatic warning about typos in the user's
config file.

Env-var fallback
----------------
By default the env-var override is *strict*: if the user sets the env
var but the target file is missing or lacks the tool's section, the
loader raises `ConfigError` rather than silently using a
different file. Tools whose entry-points must not crash on a misconfigured
env var can opt in to ``permissive_env_var=True``, which falls back to
the standard search path instead.
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Any
from typing import Iterable

from esp_pylib.errors import ConfigError
from esp_pylib.logger import EspLogBase
from esp_pylib.logger import log as _default_log

__all__ = ['ToolConfig']

# Sentinel distinguishing "not yet looked up" from "looked up and got None".
# Caching the negative result keeps repeated `get()` from re-walking the
# filesystem on tools that read many config keys.
_UNREAD: Any = object()


class ToolConfig:
    """Find, parse, and cache an INI-style config file for a single tool.

    Typical usage::

        config = ToolConfig(
            section_name='esptool',
            config_filenames=['esptool.cfg', 'setup.cfg', 'tox.ini'],
            env_var='ESPTOOL_CFGFILE',
            valid_options=['timeout', 'connect_attempts', ...],
            permissive_env_var=True,
            verbose=True,
        )
        parser, path = config.load()
        timeout = config.get('timeout', fallback='10')

    The instance caches both the resolved path and the parsed
    `configparser.ConfigParser` after the first lookup. Call
    `reload` to drop the cache (for example, after the tool itself
    rewrites the config file).
    """

    def __init__(
        self,
        section_name: str,
        config_filenames: list[str],
        env_var: str | None = None,
        search_dirs: list[Path] | None = None,
        valid_options: Iterable[str] | None = None,
        permissive_env_var: bool = False,
        verbose: bool = False,
        logger: EspLogBase | None = None,
    ):
        """
        :param section_name: INI section the tool owns (e.g. ``"esptool"``).
        :param config_filenames: Candidate filenames tried in each search dir,
            in priority order.
        :param env_var: Name of an env var that, when set, overrides the
            search and points at a single config file.
        :param search_dirs: Optional override of the search directories.
            Mostly useful for tests; production code should leave it
            unset and let `_default_search_dirs` derive
            cwd/user-config-dir/home.
        :param valid_options: Optional iterable of recognized option names.
            When provided alongside ``verbose=True``, `load` emits a warning listing
            any unknown options found in the matched file. Pass ``None``
            to skip the check.
        :param permissive_env_var: When ``True``, an env-var pointing at a
            missing file or a file without the expected section silently
            falls through to the directory search instead of raising
            `ConfigError`. Use this for tools that load config at
            module-import time and must not crash startup on a
            misconfigured env var.
        :param verbose: When ``True``, the loader emits user-facing
            messages on the configured ``logger``: warnings on invalid
            candidate files / unknown options, and a "Loaded custom
            configuration from ..." line on a successful match. When
            ``False`` (the default) the loader is silent — appropriate
            for module-level config reads where surprise output would be
            noisy or misleading.
        :param logger: Sink for verbose output. Any object exposing
            `warn` and
            `print` works. ``None``
            (default) falls back to `esp_pylib.logger.log` — the
            global proxy that dispatches to whatever has been installed
            as ``EspLog.instance``. Consumer tools that own a logger
            (``esptool``, ``esp-coredump``, ...) should pass their
            instance explicitly so the dependency is visible at the call
            site rather than implicit in the active singleton at runtime.
        """
        self.section_name = section_name
        self.config_filenames = list(config_filenames)
        self.env_var = env_var
        self._custom_search_dirs = list(search_dirs) if search_dirs is not None else None
        self.valid_options = set(valid_options) if valid_options is not None else None
        self.permissive_env_var = permissive_env_var
        self.verbose = verbose
        # Stored as ``EspLogBase`` (the abstract interface) so anything
        # implementing ``warn``/``print`` is accepted — including the
        # ``_LogProxy`` returned as the default. Construction-time
        # assignment (vs. lazy lookup) means a misconfigured ``logger=``
        # argument fails fast with a clear stack trace.
        self._logger: EspLogBase = logger if logger is not None else _default_log

        # Cache state. ``_cached_path`` uses the ``_UNREAD`` sentinel so we
        # can distinguish "haven't looked yet" from "looked and found nothing".
        # ``_cached_parser`` uses ``None`` because there is always *some* parser
        # to return after load() (an empty one when no file was found), so the
        # ``is None`` check is unambiguous. The parser cache also implicitly
        # de-duplicates verbose output: ``_emit_load_messages`` is only invoked
        # on the cache-miss branch of `load`, so a successful first load
        # followed by repeated load() calls emits the "Loaded ..." line and
        # any unknown-option warnings exactly once per cache lifetime.
        self._cached_path: Any = _UNREAD
        self._cached_parser: configparser.ConfigParser | None = None

        # Tracks whether the resolved path actually came from the env var,
        # which is needed for the "(set with X)" suffix in logger output.
        # Distinct from ``bool(os.environ.get(env_var))`` because the env
        # var may be set but ignored (permissive fallback).
        self._env_var_used: bool = False

    # ------------------------------------------------------------------
    # Search directories
    # ------------------------------------------------------------------

    def _default_search_dirs(self) -> list[Path]:
        """Return the platform-aware default search directory list.

        The OS user config directory:

        * POSIX → ``~/.config/<section_name>/``
        * Windows → ``~/AppData/Local/<section_name>/``

        The directory does not need to exist; if it does not, the
        candidate paths inside it simply won't match.
        """
        home = Path.home()
        if os.name == 'nt':
            user_dir = home / 'AppData' / 'Local' / self.section_name
        else:
            user_dir = home / '.config' / self.section_name
        return [Path.cwd(), user_dir, home]

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def find(self) -> Path | None:
        """Return the first matching config file path, or ``None``.

        Cached after the first call; use `reload` to re-scan.

        :raises ConfigError: when ``env_var`` is set, points at a path that
            either does not exist or lacks the tool's section, and
            ``permissive_env_var`` is ``False`` (the default).
            Search-path candidates are silently skipped on the same
            conditions, since multiple shared filenames like ``setup.cfg``
            are expected to often exist without the tool's section.
        """
        if self._cached_path is not _UNREAD:
            return self._cached_path  # type: ignore[no-any-return]
        path = self._do_find()
        self._cached_path = path
        return path

    def _do_find(self) -> Path | None:
        # Reset the env-var-used flag at the start of every (re)scan so
        # info_fn doesn't claim "(set with X)" on a permissive fallthrough.
        self._env_var_used = False

        # 1) Explicit env-var override. Strict by default: the user
        # pointed us at a specific file, so silently falling through to
        # the search would mask configuration mistakes. ``permissive_env_var``
        # opts into the legacy fallthrough behavior for tools that load
        # config at import time.
        if self.env_var:
            env_path = os.environ.get(self.env_var)
            if env_path:
                p = Path(env_path)
                if p.is_file() and self._file_has_section(p):
                    self._env_var_used = True
                    return p
                if not self.permissive_env_var:
                    if not p.is_file():
                        raise ConfigError(f'Config file specified by {self.env_var} not found: {env_path}')
                    raise ConfigError(
                        f'Config file specified by {self.env_var} ({env_path}) has no [{self.section_name}] section.'
                    )
                # Permissive: drop the env-var path on the floor and fall
                # through to the directory search below.

        # 2) Search the standard directories. Within each directory we
        # filter by section presence so that, e.g., a ``setup.cfg`` that
        # exists for ``flake8`` doesn't shadow a tool-specific config in
        # a later directory.
        search_dirs = self._custom_search_dirs if self._custom_search_dirs is not None else self._default_search_dirs()
        for directory in search_dirs:
            for filename in self.config_filenames:
                candidate = directory / filename
                if candidate.is_file() and self._file_has_section(candidate):
                    return candidate
        return None

    def _file_has_section(self, path: Path) -> bool:
        """Return True iff ``path`` parses and contains the tool's section.

        Uses ``RawConfigParser`` (rather than the interpolating
        `ConfigParser`) so a value with stray ``%`` characters in an
        unrelated section can't make a perfectly valid file look invalid.
        Parse errors and decode errors are treated as "not a match" so the
        search can move on to the next candidate; when ``verbose`` is set,
        each skip is also reported to the user via the standard
        ``"Ignoring invalid config file ..."`` message.
        """
        cp = configparser.RawConfigParser()
        try:
            cp.read(str(path), encoding='utf-8')
        except (UnicodeDecodeError, configparser.Error) as exc:
            if self.verbose:
                self._logger.warn(f'Ignoring invalid config file {path}: {exc}')
            return False
        return cp.has_section(self.section_name)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def load(self) -> tuple[configparser.ConfigParser, Path | None]:
        """Return ``(parser, path)``. ``path`` is ``None`` when no file was found.

        The returned parser **always** contains the tool's section (empty
        when no file was found) so callers can write
        ``parser[section_name].get('key', default)`` without first checking
        for the section's existence.

        On the first call, when a file is matched and ``verbose=True`` was
        passed to the constructor, this method emits:

        * an "Ignoring unknown config option(s): ..." warning if
          ``valid_options`` was provided and the file contains options not
          in that list;
        * a "Loaded custom configuration from ..." line naming the absolute
          path of the file actually used, with a ``" (set with <ENV_VAR>)"``
          suffix when the path came from the env-var override.

        Cached after the first call; use `reload` to re-parse.
        """
        if self._cached_parser is not None:
            return self._cached_parser, self.find()

        path = self.find()
        # Interpolation is disabled so values can contain literal ``%`` (e.g.
        # ``progress=100%``) without raising ``InterpolationSyntaxError``. This
        # also keeps the load step consistent with the ``RawConfigParser`` used
        # for discovery in `_file_has_section`.
        parser = configparser.ConfigParser(interpolation=None)
        if path is not None:
            try:
                parser.read(str(path), encoding='utf-8')
            except configparser.Error as exc:
                # ``find()`` already validated the file with a RawConfigParser,
                # so a parse error here is exotic and worth surfacing rather
                # than silently swallowing.
                raise ConfigError(f'Failed to parse config file {path}: {exc}') from exc
            if not parser.has_section(self.section_name):
                # Defensive: ``find()`` only returns files with the section.
                raise ConfigError(f'Config file {path} does not have [{self.section_name}] section.')
            self._emit_load_messages(parser, path)
        else:
            # Pre-create an empty section so callers can index into the
            # parser without first checking ``has_section``.
            parser[self.section_name] = {}

        self._cached_parser = parser
        return parser, path

    def _emit_load_messages(self, parser: configparser.ConfigParser, path: Path) -> None:
        """Emit the user-facing messages tied to a successful load.

        No-op unless ``verbose`` was set at construction time. Split out
        from `load` so subclasses (or unit tests) can observe the
        exact strings without re-implementing the rest of the parsing
        pipeline.

        Called exactly once per cache lifetime: `load` only invokes
        this on the cache-miss branch, so de-duplication of repeat
        ``load()`` calls is handled entirely by the parser cache.
        """
        if not self.verbose:
            return
        if self.valid_options is not None:
            unknown = sorted(set(parser.options(self.section_name)) - self.valid_options)
            if unknown:
                self._logger.warn(
                    f'Ignoring unknown config option{"s" if len(unknown) > 1 else ""}: {", ".join(unknown)}'
                )
        env_suffix = f' (set with {self.env_var})' if self._env_var_used and self.env_var else ''
        self._logger.print(f'Loaded custom configuration from {path.absolute()}{env_suffix}')

    def get(self, key: str, fallback: str | None = None) -> str | None:
        """Return ``parser[section_name].get(key, fallback)``.

        Convenience for the common single-value lookup. Repeated calls
        reuse the cached parser (no extra disk I/O after the first call).
        """
        parser, _ = self.load()
        return parser.get(self.section_name, key, fallback=fallback)

    # ------------------------------------------------------------------
    # Cache control
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Drop the cached path and parser so the next call re-scans/re-reads.

        Useful when the tool itself writes to its config file or when tests
        mutate the environment between cases. The next `load` call
        will re-emit verbose output (if ``verbose=True``) for the same
        reason: there's a fresh cache to populate.
        """
        self._cached_path = _UNREAD
        self._cached_parser = None
