"""How a command is described. One description feeds the parser, `jira help`, `jira commands --json`
and the generated reference, so they cannot drift apart."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .runtime import Context


@dataclass(frozen=True)
class Arg:
    names: tuple[str, ...]
    help: str
    kind: str = "str"  # str | int | float | flag | choice | words (a positional that takes every word that stands there, joined by a space)
    metavar: str = ""
    default: Any = None
    choices: tuple[str, ...] = ()
    required: bool = False

    @property
    def positional(self) -> bool:
        return not self.names[0].startswith("-")

    @property
    def dest(self) -> str:
        long_names = [name for name in self.names if name.startswith("--")]
        return (long_names or self.names)[0].lstrip("-").replace("-", "_")

    @property
    def label(self) -> str:
        return self.metavar or ("VALUE" if not self.positional else self.dest.upper())


@dataclass(frozen=True)
class Example:
    command: str
    note: str = ""


Handler = Callable[["Context", argparse.Namespace], "int | None"]


@dataclass(frozen=True)
class Command:
    name: str
    group: str
    summary: str
    handler: Handler
    description: str = ""
    args: tuple[Arg, ...] = ()
    examples: tuple[Example, ...] = ()
    notes: tuple[str, ...] = ()
    writes: bool = False
    json_output: bool = False
    output: str = ""
    aliases: tuple[str, ...] = ()
    needs_app: bool = True
    when: str = ""  # when to use the command, and when another one is better: for people, and for an agent that picks a tool

    def usage(self) -> str:
        parts = [f"jira {self.name}"]
        for arg in self.args:
            if arg.positional:
                label = arg.label + (" ..." if arg.kind == "words" else "")
                parts.append(label if arg.required else f"[{label}]")
            else:
                text = arg.names[0] if arg.kind == "flag" else f"{arg.names[0]} {arg.label}"
                parts.append(text if arg.required else f"[{text}]")
        if self.json_output:
            parts.append("[--json]")
        return " ".join(parts)


GROUP_ORDER = ("Read", "Work on an issue", "Create and import", "Sync with files", "Projects", "Setup and information", "Tools")
