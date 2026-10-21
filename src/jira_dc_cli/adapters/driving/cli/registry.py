"""The list of commands. To add a command, write a module in `commands/` and list it in
`commands/__init__.py`."""
from __future__ import annotations

from functools import lru_cache

from .commands import MODULES
from .spec import GROUP_ORDER, Command


@lru_cache(maxsize=1)
def all_commands() -> tuple[Command, ...]:
    found: list[Command] = []
    for module in MODULES:
        found.extend(module.COMMANDS)
    order = {group: index for index, group in enumerate(GROUP_ORDER)}
    # The sort is stable: inside a group the commands stay in the order of their module.
    return tuple(sorted(found, key=lambda command: order.get(command.group, len(order))))


def find_command(name: str) -> Command | None:
    wanted = name.strip().lower()
    for command in all_commands():
        if wanted == command.name or wanted in command.aliases:
            return command
    return None


def suggestions(name: str) -> list[str]:
    import difflib  # only a mistyped command needs it

    names = [command.name for command in all_commands()] + [alias for command in all_commands() for alias in command.aliases]
    return difflib.get_close_matches(name.lower(), names, n=3, cutoff=0.6)
