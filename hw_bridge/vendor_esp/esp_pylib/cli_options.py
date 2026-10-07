# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
"""Reusable Click option classes for Espressif tool CLIs."""

from __future__ import annotations

import inspect

import rich_click as click

__all__ = ['EspRichGroup', 'MutuallyExclusiveOption', 'OptionEatAll']


class EspRichGroup(click.RichGroup):
    """``rich_click`` group that wires `OptionEatAll` on the root CLI.

    Sets ``ctx._commands_list`` before parsing so eat-all options stop at
    subcommand names (``mytool --port-filter a=b flash`` does not treat
    ``flash`` as a filter value). Use ``@click.group(cls=EspRichGroup)`` on
    any group that has subcommands and parent-level `OptionEatAll`
    options. Tools with extra ``parse_args`` logic should subclass this class
    and call ``super().parse_args(ctx, args)`` (see ``esptool``'s ``Group``).
    """

    def parse_args(self, ctx: click.Context, args: list[str]):
        ctx._commands_list = self.list_commands(ctx)
        return super().parse_args(ctx, args)


class OptionEatAll(click.Option):
    """Grab all arguments up to the next option or subcommand.

    Imitates argparse ``nargs='*'`` for options.

    Prefer ``multiple=True`` with ordinary `ParamType` instances so
    each eaten token is stored separately (``--filter a b`` → ``('a', 'b')``).

    Without ``multiple=True``, all eaten tokens are passed as one ``list`` to
    ``type.convert()`` in a single call. That is only appropriate for custom
    types that expect a token list (e.g. esptool's ``AddrFilenamePairType`` on
    ``--encrypt-files``). Built-in types such as ``str`` or ``click.File`` will
    mis-parse or fail.

    On a group with subcommands, use `EspRichGroup` (or set
    ``ctx._commands_list`` yourself in ``parse_args``) so subcommand names are
    not swallowed as option values.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._previous_parser_process = None
        self._eat_all_parser = None
        if self.type and hasattr(self.type, 'name'):
            self.metavar = f'[{self._get_metavar() or self.type.name.upper()}]'

    def _get_metavar(self):
        """Return the type metavar; compatible with Click 8.0–8.2 ``get_metavar`` signatures."""
        get_metavar = self.type.get_metavar
        sig = inspect.signature(get_metavar)
        ctx = click.get_current_context(silent=True)
        if 'ctx' in sig.parameters:
            return get_metavar(self, ctx)
        return get_metavar(self)

    def add_to_parser(self, parser, ctx):
        def parser_process(value, state):
            done = False
            values = [value]
            while state.rargs and not done:
                for prefix in self._eat_all_parser.prefixes:
                    if state.rargs[0].startswith(prefix):
                        done = True
                        break
                if state.rargs[0] in self._commands_list:
                    done = True
                if not done:
                    values.append(state.rargs.pop(0))

            if self.multiple:
                for v in values:
                    self._previous_parser_process(v, state)
            else:
                self._previous_parser_process(values, state)

        retval = super().add_to_parser(parser, ctx)
        # Patches Click's internal OptionParser maps (_long_opt / _short_opt).
        # Same approach as esptool's OptionEatAll; re-check on Click upgrades.
        for name in self.opts:
            current_parser = parser._long_opt.get(name) or parser._short_opt.get(name)
            if current_parser:
                self._eat_all_parser = current_parser
                self._previous_parser_process = current_parser.process
                current_parser.process = parser_process
                self._commands_list = getattr(ctx, '_commands_list', [])
                break
        return retval


class MutuallyExclusiveOption(click.Option):
    """Enforce mutually exclusive CLI flags (argparse-style exclusive group).

    Pass ``exclusive_with=['other_opt', ...]`` using each option's Click
    ``name`` (underscore form, e.g. ``no_compress`` for ``--no-compress``).
    When both this option and any listed peer appear on the command line,
    `click.UsageError` is raised and the help text notes the conflict.

    Error messages resolve each peer's real long option from the command
    (e.g. ``@click.option('--port-x', 'port_alt')`` shows ``--port-x``, not
    ``--port-alt``). Help text appended at decorator time still derives flags
    from ``name``; if you override ``name`` away from the long option, add the
    correct flag wording to ``help`` yourself.
    """

    def __init__(self, *args, **kwargs):
        self._exclusive_with = list(kwargs.pop('exclusive_with', []))
        self.mutually_exclusive = set(self._exclusive_with)
        if self._exclusive_with:
            ex_str = ', '.join(self._to_option_name(opt) for opt in self._exclusive_with)
            base_help = (kwargs.get('help') or '').strip()
            note = f'NOTE: This argument is mutually exclusive with arguments: {ex_str}.'
            kwargs['help'] = f'{base_help} {note}'.strip() if base_help else note
        super().__init__(*args, **kwargs)

    @staticmethod
    def _to_option_name(name: str) -> str:
        return f'--{name.replace("_", "-")}'

    @staticmethod
    def _flag_for_name(ctx: click.Context, name: str) -> str:
        for param in ctx.command.get_params(ctx):
            if param.name == name:
                for opt in param.opts:
                    if opt.startswith('--'):
                        return str(opt)
                if param.opts:
                    return str(param.opts[0])
                break
        return MutuallyExclusiveOption._to_option_name(name)

    def handle_parse_result(self, ctx, opts, args):
        if self.mutually_exclusive.intersection(opts) and self.name in opts:
            options = ', '.join(self._flag_for_name(ctx, opt) for opt in self._exclusive_with)
            raise click.UsageError(
                f'Illegal usage: {self._flag_for_name(ctx, self.name)} '
                f'is mutually exclusive with arguments: {options}.',
            )
        return super().handle_parse_result(ctx, opts, args)
