# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""ROM ELF file resolution.

Single implementation replacing duplicated code in esp-idf-monitor and
esp-coredump. Uses ``IDF_PATH`` and ``ESP_ROM_ELF_DIR`` from the
environment — the cross-tool variables used for ROM ELF lookup.
"""

from __future__ import annotations

import json
import os

__all__ = [
    'get_idf_path',
    'get_rom_elf_dir',
    'get_roms_json_paths',
    'get_rom_elf_path',
]


def get_idf_path() -> str:
    """Return ``IDF_PATH`` from the environment (empty string when unset)."""
    return os.getenv('IDF_PATH', '')


def get_rom_elf_dir() -> str:
    """Return ``ESP_ROM_ELF_DIR`` from the environment (empty string when unset)."""
    return os.getenv('ESP_ROM_ELF_DIR', '')


def get_roms_json_paths() -> list[str]:
    """Return both possible ``roms.json`` locations under ``IDF_PATH``.

    ``tools/idf_py_actions/roms.json`` is listed for compatibility with
    ESP-IDF before v5.5, when the file was moved under ``components/esp_rom``.
    """
    idf = get_idf_path()
    return [
        os.path.join(idf, 'components', 'esp_rom', 'roms.json'),
        os.path.join(idf, 'tools', 'idf_py_actions', 'roms.json'),
    ]


def _load_target_roms(target: str) -> list[dict[str, object]]:
    """Load ``roms.json`` revision entries for *target*.

    Tries each path from `get_roms_json_paths`. Unreadable files, invalid
    JSON, or JSON that omits *target* (or lists it with an empty revision
    list) are skipped so the next location can be tried. Once a file lists
    *target* with a non-empty revision list, that list is returned
    exclusively.

    Returns an empty list when ``IDF_PATH`` is unset or no usable entry is
    found.
    """
    if not get_idf_path():
        return []

    for roms_json_path in get_roms_json_paths():
        try:
            with open(roms_json_path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        candidate = data.get(target)
        if not isinstance(candidate, list) or not candidate:
            continue
        return candidate

    return []


def _select_rom_revision(target_roms: list[dict[str, object]], chip_rev: int) -> int | None:
    """Select an exact or next-lower ROM revision from *target_roms*.

    *chip_rev* and each entry's ``rev`` use the same encoding as
    ``efuse_hal_chip_revision()``: ``major * 100 + minor`` (for example
    ``0`` is v0.0, ``3`` is v0.03, ``101`` is v1.1, ``300`` is v3.0). Do not
    mix bare major numbers with this encoding when ordering revisions.

    Prefers an entry whose ``rev`` equals *chip_rev*. If none exists, picks
    the highest ``rev`` that is still ``<= chip_rev``, matching
    https://github.com/espressif/esp-rom-elfs#choosing-the-rom-elf-file

    Returns ``None`` when *target_roms* has no usable integer ``rev`` that
    is ``<= chip_rev``.
    """
    candidate_revs: list[int] = []
    for rom in target_roms:
        if not isinstance(rom, dict):
            continue
        rev = rom.get('rev')
        if isinstance(rev, int) and rev <= chip_rev:
            candidate_revs.append(rev)
    return max(candidate_revs) if candidate_revs else None


def get_rom_elf_path(target: str, chip_rev: int) -> str | None:
    """Resolve the ROM ELF path for *target* and *chip_rev*.

    *chip_rev* is the full chip revision in ``major * 100 + minor`` form
    (same as ``efuse_hal_chip_revision()`` and ``roms.json`` ``rev``
    fields). Reads ``roms.json`` from ``IDF_PATH``, selects an exact
    revision match or the next lower listed revision, and returns
    ``{ESP_ROM_ELF_DIR}/{target}_rev{selected_rev}_rom.elf``.

    Returns ``None`` when ``IDF_PATH`` or ``ESP_ROM_ELF_DIR`` are unset,
    when no ``roms.json`` lists *target* with at least one revision entry,
    or when no entry has ``rev <= chip_rev``.
    """
    rom_elf_dir = get_rom_elf_dir()
    if not get_idf_path() or not rom_elf_dir:
        return None

    selected_rev = _select_rom_revision(_load_target_roms(target), chip_rev)
    if selected_rev is None:
        return None

    return os.path.join(rom_elf_dir, f'{target}_rev{selected_rev}_rom.elf')
