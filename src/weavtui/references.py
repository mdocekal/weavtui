"""Cross-reference graph between collections, and labels for listing references."""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text

from weavtui.backend import CollectionInfo
from weavtui.theme import GREEN, PINK


@dataclass
class Edges:
    outgoing: list[tuple[str, str]] = field(default_factory=list)  # (property, target)
    incoming: list[tuple[str, str]] = field(default_factory=list)  # (source, property)


def reference_graph(infos: list[CollectionInfo]) -> dict[str, Edges]:
    """Outgoing and incoming references for every collection (including dangling targets)."""
    graph: dict[str, Edges] = {c.name: Edges() for c in infos}
    for c in infos:
        for ref in c.references:
            for target in ref.targets:
                graph[c.name].outgoing.append((ref.name, target))
                graph.setdefault(target, Edges()).incoming.append((c.name, ref.name))
    return graph


def outgoing_label(prop: str, target: str, known: bool = True, color: str = GREEN) -> Text:
    text = Text.assemble(("→ ", f"bold {color}"), (prop, "italic"), "  ▶ ")
    text.append(target, style="bold" if known else "red")
    if not known:
        text.append(" (missing)", style="red dim")
    return text


def incoming_label(source: str, prop: str, color: str = PINK) -> Text:
    return Text.assemble(("← ", f"bold {color}"), (source, "bold"), (f".{prop}", "italic"))
