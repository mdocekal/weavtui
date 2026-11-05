"""UML-like class diagram of the schema, drawn with box-drawing characters.

Layout is a small Sugiyama-style layered layout:

1. Cycles are broken by reversing DFS back edges; nodes get layers by longest path, so
   references mostly point left-to-right (each layer is a column).
2. Edges spanning several layers get one-row "dummy" pass-through nodes in the layers in
   between, so every drawn segment connects two adjacent columns.
3. Nodes in each column are ordered by the barycenter of their neighbours to reduce
   crossings, then stacked top to bottom.
4. Each segment gets its own vertical track in the channel between two columns, so lines
   never run through boxes; crossings are drawn as ``┼``.

Collections without any references are laid out in a grid below the graph. References
of a collection to itself are not drawn as arrows; the box lists them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.geometry import Region
from textual.message import Message
from textual.widgets import Static

from weavtui.backend import CollectionInfo

MAX_INNER = 44  # widest box content, longer lines are truncated
MIN_INNER = 14
BOX_GAP = 1  # rows between boxes in a column
GRID_GAP = 3  # columns between boxes in the bottom grid


@dataclass
class Box:
    name: str
    lines: list[tuple[str, str]]  # (text, kind); kind: name/alias/sep/prop/ref/note
    ref_rows: dict[str, int]  # reference property -> row offset inside the box
    missing: bool = False
    x: int = 0
    y: int = 0

    HEADER_ROW = 1  # incoming arrows attach to the name row

    @property
    def inner(self) -> int:
        widest = max((len(t) for t, k in self.lines if k != "sep"), default=0)
        return max(MIN_INNER, min(MAX_INNER, widest))

    @property
    def width(self) -> int:
        return self.inner + 4

    @property
    def height(self) -> int:
        return len(self.lines) + 2

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def build_box(info: CollectionInfo | None, name: str) -> Box:
    if info is None:
        return Box(name, [(name, "name"), ("", "sep"), ("(missing collection)", "note")], {})
    count = "" if info.count is None else f"  {info.count}"
    lines: list[tuple[str, str]] = [(f"{name}{count}", "name")]
    if info.aliases:
        lines.append((f"aka {', '.join(info.aliases)}", "alias"))
    lines.append(("", "sep"))
    width = max((len(p.name) for p in info.properties), default=0)
    if info.properties:
        lines += [(f"{p.name:<{width}}  {p.data_type}", "prop") for p in info.properties]
    else:
        lines.append(("(no properties)", "note"))
    ref_rows: dict[str, int] = {}
    if info.references:
        lines.append(("", "sep"))
        width = max(len(r.name) for r in info.references)
        for r in info.references:
            ref_rows[r.name] = len(lines) + 1  # +1 for the top border
            lines.append((f"{r.name:<{width}}  → {', '.join(r.targets)}", "ref"))
    return Box(name, lines, ref_rows)


@dataclass
class Edge:
    source: str
    prop: str
    target: str


@dataclass
class _Node:
    """A box or a one-row dummy pass-through in one layer."""

    key: str
    layer: int
    box: Box | None = None
    y: int = 0

    @property
    def height(self) -> int:
        return self.box.height if self.box else 1


@dataclass
class _Segment:
    left: _Node
    right: _Node
    edge: Edge
    reversed: bool  # True when the edge points right-to-left (cycle broken)
    y_left: int = 0
    y_right: int = 0
    x_left: int = 0  # first cell of the left horizontal
    x_right: int = 0  # last cell of the right horizontal
    track: int = 0


@dataclass
class Diagram:
    boxes: dict[str, Box]
    width: int
    height: int
    cells: list[list[str]] = field(default_factory=list)
    kinds: list[list[str]] = field(default_factory=list)  # styling class per cell
    owner: list[list[str | None]] = field(default_factory=list)  # box name per cell

    def render(self, selected: str | None, colors: dict[str, str]) -> Text:
        styles = {
            "line": Style(color=colors["line"]),
            "arrow": Style(color=colors["line"], bold=True),
            "border": Style(color=colors["border"]),
            "name": Style(bold=True),
            "alias": Style(dim=True, italic=True),
            "prop": Style(),
            "ptype": Style(dim=True),
            "ref": Style(color=colors["line"]),
            "note": Style(dim=True, italic=True),
            "missing": Style(color=colors["error"]),
            "label": Style(dim=True),
            "": Style(),
        }
        selected_border = Style(color=colors["selected"], bold=True)
        text = Text(no_wrap=True, overflow="crop")
        for y in range(self.height):
            row, kinds, owners = self.cells[y], self.kinds[y], self.owner[y]
            start = 0
            for x in range(1, self.width + 1):
                if x < self.width and kinds[x] == kinds[start] and owners[x] == owners[start]:
                    continue
                style = styles[kinds[start]]
                if owners[start] == selected and kinds[start] == "border":
                    style = selected_border
                elif owners[start] == selected and kinds[start] == "name":
                    style = Style(bold=True, color=colors["selected"])
                text.append("".join(row[start:x]), style)
                start = x
            if y < self.height - 1:
                text.append("\n")
        return text


_JUNCTIONS = {
    frozenset("ew"): "─",
    frozenset("ns"): "│",
    frozenset("es"): "┌",
    frozenset("ws"): "┐",
    frozenset("en"): "└",
    frozenset("wn"): "┘",
    frozenset("ews"): "┬",
    frozenset("ewn"): "┴",
    frozenset("nse"): "├",
    frozenset("nsw"): "┤",
    frozenset("nsew"): "┼",
    frozenset("e"): "─",
    frozenset("w"): "─",
    frozenset("n"): "│",
    frozenset("s"): "│",
}


def _edges(infos: list[CollectionInfo]) -> list[Edge]:
    return [
        Edge(c.name, r.name, t)
        for c in infos
        for r in c.references
        for t in r.targets
        if t != c.name  # self references are listed in the box only
    ]


def _layers(nodes: list[str], edges: list[Edge]) -> tuple[dict[str, int], set[int]]:
    """Longest-path layering after reversing DFS back edges. Returns layers and the
    indices of reversed edges."""
    out: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for i, e in enumerate(edges):
        out[e.source].append((i, e.target))
    reversed_edges: set[int] = set()
    state: dict[str, int] = {}  # 1 = on stack, 2 = done

    def dfs(v: str) -> None:
        state[v] = 1
        for i, w in out[v]:
            if state.get(w) == 1:
                reversed_edges.add(i)
            elif w not in state:
                dfs(w)
        state[v] = 2

    for v in nodes:
        if v not in state:
            dfs(v)

    preds: dict[str, list[str]] = defaultdict(list)
    for i, e in enumerate(edges):
        a, b = (e.target, e.source) if i in reversed_edges else (e.source, e.target)
        preds[b].append(a)
    layer: dict[str, int] = {}

    def depth(v: str) -> int:
        if v not in layer:
            layer[v] = 0  # guard; the graph is acyclic after reversal
            layer[v] = max((depth(u) + 1 for u in preds[v]), default=0)
        return layer[v]

    for v in nodes:
        depth(v)
    return layer, reversed_edges


def layout(infos: list[CollectionInfo]) -> Diagram:
    by_name = {c.name: c for c in infos}
    edges = _edges(infos)
    linked = sorted({e.source for e in edges} | {e.target for e in edges}, key=str.lower)
    isolated = sorted((c.name for c in infos if c.name not in linked), key=str.lower)
    boxes = {n: build_box(by_name.get(n), n) for n in linked + isolated}
    for n in linked:
        boxes[n].missing = n not in by_name

    layer_of, reversed_edges = _layers(linked, edges)
    n_layers = max(layer_of.values(), default=-1) + 1
    columns: list[list[_Node]] = [[] for _ in range(n_layers)]
    nodes = {n: _Node(n, layer_of[n], boxes[n]) for n in linked}
    for n in linked:
        columns[layer_of[n]].append(nodes[n])

    # Split edges into adjacent-layer segments through dummy nodes.
    segments: list[_Segment] = []
    for i, e in enumerate(edges):
        rev = i in reversed_edges
        a, b = (e.target, e.source) if rev else (e.source, e.target)
        chain = [nodes[a]]
        for layer in range(layer_of[a] + 1, layer_of[b]):
            dummy = _Node(f"~{i}~{layer}", layer)
            columns[layer].append(dummy)
            chain.append(dummy)
        chain.append(nodes[b])
        segments += [_Segment(chain[k], chain[k + 1], e, rev) for k in range(len(chain) - 1)]

    _order_columns(columns, segments)

    # Column x positions and stacking.
    col_width = [max((n.box.width for n in col if n.box), default=6) for col in columns]
    per_channel: dict[int, list[_Segment]] = defaultdict(list)
    for s in segments:
        per_channel[s.left.layer].append(s)
    channel_width = {k: 2 * len(per_channel[k]) + 4 for k in range(n_layers)}
    xs, x = [], 0
    for k in range(n_layers):
        xs.append(x)
        x += col_width[k] + channel_width[k]
    graph_width = x - channel_width[n_layers - 1] if n_layers else 0
    graph_height = 0
    for k, col in enumerate(columns):
        y = 0
        for node in col:
            node.y = y
            if node.box:
                node.box.x, node.box.y = xs[k], y
            y += node.height + BOX_GAP
        graph_height = max(graph_height, y - BOX_GAP)

    # Attachment points of each segment.
    for s in segments:
        s.y_left, s.x_left = _port(s.left, s, side="right", xs=xs, col_width=col_width)
        s.y_right, s.x_right = _port(s.right, s, side="left", xs=xs, col_width=col_width)
    for k, segs in per_channel.items():
        channel_x = xs[k] + col_width[k]
        for s, track in zip(_order_tracks(segs), range(len(segs)), strict=True):
            s.track = channel_x + 2 + 2 * track

    # Bottom grid for collections without references.
    grid_top = graph_height + (3 if linked and isolated else 0)
    wrap = max(graph_width, 100)
    x, y, row_h = 0, grid_top + (2 if isolated else 0), 0
    for n in isolated:
        b = boxes[n]
        if x and x + b.width > wrap:
            x, y, row_h = 0, y + row_h + BOX_GAP, 0
        b.x, b.y = x, y
        x += b.width + GRID_GAP
        row_h = max(row_h, b.height)
    width = max([graph_width] + [b.x + b.width for b in boxes.values()] + [1])
    height = max([graph_height] + [b.y + b.height for b in boxes.values()] + [1])

    d = Diagram(boxes, width, height)
    d.cells = [[" "] * width for _ in range(height)]
    d.kinds = [[""] * width for _ in range(height)]
    d.owner = [[None] * width for _ in range(height)]
    _draw_segments(d, segments, columns, xs, col_width)
    for b in boxes.values():
        _draw_box(d, b)
    if isolated:
        label = "Collections without references" if linked else "Collections"
        _put(d, 0, grid_top, label, "label")
    return d


def _order_columns(columns: list[list[_Node]], segments: list[_Segment]) -> None:
    """Barycenter heuristic, a few sweeps in both directions."""
    left_of: dict[str, list[_Node]] = defaultdict(list)
    right_of: dict[str, list[_Node]] = defaultdict(list)
    for s in segments:
        left_of[s.right.key].append(s.left)
        right_of[s.left.key].append(s.right)

    def sweep(cols: list[list[_Node]], neighbours: dict[str, list[_Node]]) -> None:
        for k in range(1, len(cols)):
            pos = {n.key: i for i, n in enumerate(cols[k - 1])}
            current = {n.key: i for i, n in enumerate(cols[k])}

            def bary(n: _Node, pos: dict[str, int] = pos, cur: dict[str, int] = current) -> float:
                ps = [pos[m.key] for m in neighbours[n.key] if m.key in pos]
                return sum(ps) / len(ps) if ps else cur[n.key]

            cols[k].sort(key=bary)

    for _ in range(3):
        sweep(columns, left_of)
        rev = columns[::-1]
        sweep(rev, right_of)


def _port(node: _Node, s: _Segment, side: str, xs: list[int], col_width: list[int]):
    """(row, x) where a segment touches a node. ``side`` is the node side."""
    k = node.layer
    if node.box is None:  # dummy: a horizontal line across the whole column
        return node.y, (xs[k] + col_width[k] - 1 if side == "right" else xs[k])
    box = node.box
    # Arrows leave the source at its reference row and arrive at the target's name row.
    offset = box.ref_rows[s.edge.prop] if node.key == s.edge.source else Box.HEADER_ROW
    row = box.y + offset
    x = box.x + box.width if side == "right" else box.x - 1
    return row, x


def _order_tracks(segs: list[_Segment]) -> list[_Segment]:
    """Order tracks so a horizontal on the right never runs over a horizontal on the
    left at the same row (that would look like a connection)."""
    order = sorted(segs, key=lambda s: (s.y_left, s.y_right))
    # If A.y_left == B.y_right and the rows belong to different lines, A's track must be
    # left of B's, otherwise A's left horizontal would overlap B's right horizontal.
    for _ in range(len(order)):
        changed = False
        for i, a in enumerate(order):
            for j in range(i):
                b = order[j]
                if a.y_left == b.y_right:
                    order.insert(j, order.pop(i))
                    changed = True
                    break
            if changed:
                break
        if not changed:
            break
    return order


def _line(d: Diagram, dirs: list[list[set[str]]], x1: int, y1: int, x2: int, y2: int) -> None:
    if y1 == y2:
        for x in range(min(x1, x2), max(x1, x2)):
            dirs[y1][x].add("e")
            dirs[y1][x + 1].add("w")
    else:
        for y in range(min(y1, y2), max(y1, y2)):
            dirs[y][x1].add("s")
            dirs[y + 1][x1].add("n")


def _draw_segments(d, segments, columns, xs, col_width) -> None:
    dirs = [[set() for _ in range(d.width)] for _ in range(d.height)]
    arrows: list[tuple[int, int, str]] = []
    for s in segments:
        _line(d, dirs, s.x_left, s.y_left, s.track, s.y_left)
        _line(d, dirs, s.track, s.y_left, s.track, s.y_right)
        _line(d, dirs, s.track, s.y_right, s.x_right, s.y_right)
        if s.right.box is not None and not s.reversed:
            arrows.append((s.x_right, s.y_right, "▶"))
        if s.left.box is not None and s.reversed:
            arrows.append((s.x_left, s.y_left, "◀"))
    for col in columns:  # dummies: straight line across the column
        for node in col:
            if node.box is None:
                k = node.layer
                _line(d, dirs, xs[k], node.y, xs[k] + col_width[k] - 1, node.y)
    for y, row in enumerate(dirs):
        for x, ds in enumerate(row):
            if ds:
                d.cells[y][x] = _JUNCTIONS.get(frozenset(ds), "┼")
                d.kinds[y][x] = "line"
    for x, y, ch in arrows:
        d.cells[y][x] = ch
        d.kinds[y][x] = "arrow"


def _put(d: Diagram, x: int, y: int, text: str, kind: str, owner: str | None = None) -> None:
    for i, ch in enumerate(text):
        if 0 <= x + i < d.width:
            d.cells[y][x + i] = ch
            d.kinds[y][x + i] = kind
            d.owner[y][x + i] = owner


def _draw_box(d: Diagram, b: Box) -> None:
    w, inner = b.width, b.inner
    border = "missing" if b.missing else "border"
    _put(d, b.x, b.y, "┌" + "─" * (w - 2) + "┐", border, b.name)
    for i, (text, kind) in enumerate(b.lines, start=1):
        y = b.y + i
        if kind == "sep":
            _put(d, b.x, y, "├" + "─" * (w - 2) + "┤", border, b.name)
            continue
        if len(text) > inner:
            text = text[: inner - 1] + "…"
        _put(d, b.x, y, "│ " + " " * inner + " │", border, b.name)
        if kind == "prop":  # name normal, type dim
            name_part, _, type_part = text.partition("  ")
            _put(d, b.x + 2, y, name_part, "prop", b.name)
            pad = len(text) - len(type_part)
            _put(d, b.x + 2 + pad, y, type_part, "ptype", b.name)
        elif kind == "name" and b.missing:
            _put(d, b.x + 2, y, text, "missing", b.name)
        else:
            _put(d, b.x + 2, y, text, kind, b.name)
    _put(d, b.x, b.y + b.height - 1, "└" + "─" * (w - 2) + "┘", border, b.name)


def to_mermaid(infos: list[CollectionInfo]) -> str:
    """Mermaid classDiagram source for the schema."""
    known = {c.name for c in infos}
    lines = ["classDiagram"]
    for c in sorted(infos, key=lambda c: c.name.lower()):
        body = []
        if c.aliases:
            body.append(f"    <<{', '.join(c.aliases)}>>")
        body += [f"    {p.data_type} {p.name}" for p in c.properties]
        if body:
            lines += [f"  class {c.name} {{", *body, "  }"]
        else:
            lines.append(f"  class {c.name}")
    missing = sorted({t for c in infos for r in c.references for t in r.targets} - known)
    for name in missing:
        lines += [f"  class {name} {{", "    <<missing>>", "  }"]
    for c in sorted(infos, key=lambda c: c.name.lower()):
        for r in c.references:
            for t in r.targets:
                lines.append(f"  {c.name} --> {t} : {r.name}")
    return "\n".join(lines) + "\n"


# -- widget -------------------------------------------------------------------------------


def _rich_color(color: str | None, fallback: str) -> str:
    """Textual theme colours may be ANSI names like "ansi_red"; Rich wants "red"."""
    if not color:
        return fallback
    return color.removeprefix("ansi_")


class DiagramView(ScrollableContainer, can_focus=True):
    """Scrollable schema diagram with a selected collection."""

    BINDINGS = [
        Binding("left", "move('left')", "Select", show=False),
        Binding("right", "move('right')", "Select", show=False),
        Binding("up", "move('up')", "Select", show=False),
        Binding("down", "move('down')", "Select", show=False),
        Binding("enter", "open", "Browse objects"),
        Binding("e", "app.export_diagram", "Export Mermaid"),
    ]

    class Selected(Message):
        """The user moved the selection to another box."""

        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    class Opened(Message):
        """The user wants to browse the selected collection."""

        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.infos: list[CollectionInfo] = []
        self.diagram = layout([])
        self.selected: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("Loading…", id="diagram-canvas")

    def on_mount(self) -> None:
        self.watch(self.app, "theme", lambda _: self._redraw(), init=False)

    def set_infos(self, infos: list[CollectionInfo], selected: str | None) -> None:
        self.infos = infos
        self.diagram = layout(infos)
        self.selected = selected if selected in self.diagram.boxes else None
        self._redraw()

    def select(self, name: str | None, scroll: bool = True) -> None:
        if name not in self.diagram.boxes or name == self.selected:
            return
        self.selected = name
        self._redraw()
        if scroll:
            self.call_after_refresh(self._scroll_to_selected)

    def _colors(self) -> dict[str, str]:
        theme = self.app.current_theme
        return {
            "line": _rich_color(theme.primary, "green"),
            "border": _rich_color(theme.secondary, "green"),
            "selected": _rich_color(theme.accent, "magenta"),
            "error": _rich_color(theme.error, "red"),
        }

    def _redraw(self) -> None:  # not "_render": Widget uses that name
        canvas = self.query_one("#diagram-canvas", Static)
        if self.diagram.boxes:
            canvas.update(self.diagram.render(self.selected, self._colors()))
        else:
            canvas.update(Text("No collections", style="dim"))

    def _scroll_to_selected(self) -> None:
        if self.selected:
            b = self.diagram.boxes[self.selected]
            self.scroll_to_region(
                Region(b.x, b.y, b.width, b.height), animate=False, immediate=True
            )

    def action_move(self, direction: str) -> None:
        if not self.selected:
            names = sorted(self.diagram.boxes, key=str.lower)
            if names:
                self.select(names[0])
                self.post_message(self.Selected(names[0]))
            return
        cur = self.diagram.boxes[self.selected]
        cx, cy = cur.x + cur.width / 2, cur.y + cur.height / 2
        best, best_score = None, float("inf")
        for name, b in self.diagram.boxes.items():
            if name == self.selected:
                continue
            dx, dy = b.x + b.width / 2 - cx, b.y + b.height / 2 - cy
            along, across = {
                "right": (dx, dy),
                "left": (-dx, dy),
                "down": (dy, dx),
                "up": (-dy, dx),
            }[direction]
            if along <= 0:
                continue
            score = along + 2 * abs(across)
            if score < best_score:
                best, best_score = name, score
        if best:
            self.select(best)
            self.post_message(self.Selected(best))

    def box_at(self, x: int, y: int) -> str | None:
        """Name of the box covering diagram cell (x, y), if any."""
        d = self.diagram
        if 0 <= y < d.height and 0 <= x < d.width:
            return d.owner[y][x]
        return None

    def on_click(self, event: events.Click) -> None:
        """First click selects a box, a click on the selected box opens it."""
        self.focus()
        offset = event.get_content_offset(self.query_one("#diagram-canvas", Static))
        name = self.box_at(offset.x, offset.y) if offset else None
        if name is None:
            return
        event.stop()
        if name == self.selected:
            self.action_open()
        else:
            self.select(name, scroll=False)
            self.post_message(self.Selected(name))

    def action_open(self) -> None:
        if self.selected and self.selected in {c.name for c in self.infos}:
            self.post_message(self.Opened(self.selected))
        elif self.selected:
            self.notify(f"{self.selected} does not exist", severity="warning")
