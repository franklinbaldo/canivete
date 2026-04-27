"""Rich helpers — banner, theme, tips."""

from __future__ import annotations

import random

from rich.console import Console
from rich.text import Text
from rich.tree import Tree

from canivete import __version__

console = Console()
err_console = Console(stderr=True)


# Tips shown at the bottom of the overview banner. Pulled at random — gives
# a different concrete usage example each time you run `canivete` bare.
TIPS: list[str] = [
    'canivete tg photo /path/file.png --caption "look at this"',
    'canivete tg document /workspace/report.pdf',
    'canivete cron add --in 30m "check whisper logs"',
    'canivete cron add --at "2026-12-31T23:59:00-03:00" "year recap"',
    'canivete cron list',
    'canivete tg --help    (every subcommand has --help)',
]


# Subcommand registry: title, icon, color, description.
# `cli.py` reads this to render the overview tree consistently.
COMMANDS: list[tuple[str, str, str, str]] = [
    ("tg",   "📨", "blue",   "send messages and files via Telegram"),
    ("cron", "⏰", "yellow", "schedule prompts for yourself"),
]


def overview_tree() -> Tree:
    """The iconic tree banner — sub-branches evoke a swiss-army knife
    fanning out its blades. Used on `canivete` with no arguments."""
    root_label = Text()
    root_label.append("🔧 ", style="bold")
    root_label.append("canivete", style="bold cyan")
    root_label.append("  ·  ", style="dim")
    root_label.append("swiss-army CLI for AI agents", style="dim italic")

    tree = Tree(root_label, guide_style="cyan")
    for name, icon, color, desc in COMMANDS:
        leaf = Text()
        leaf.append(f"{icon}  ", style="")
        leaf.append(name, style=f"bold {color}")
        leaf.append("    ", style="")
        leaf.append(desc, style="dim")
        tree.add(leaf)
    return tree


def footer_line() -> Text:
    """Version + a random tip. Mirrors the boot-screen aesthetic — small
    hint that there's more to discover."""
    line = Text()
    line.append(f"v{__version__}", style="dim cyan")
    line.append("  ·  ", style="dim")
    line.append("tip: ", style="dim")
    line.append(random.choice(TIPS), style="italic")
    return line


def show_overview() -> None:
    console.print()
    console.print(overview_tree())
    console.print()
    console.print("  ", footer_line())
    console.print()
