"""The main weavtui application."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.json import JSON
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from weavtui.backend import (
    Backend,
    BackendError,
    CollectionInfo,
    Page,
    default_value,
    to_jsonable,
)
from weavtui.diagram import DiagramView, to_mermaid
from weavtui.references import incoming_label, outgoing_label, reference_graph
from weavtui.screens import (
    ConfirmScreen,
    ConnectErrorScreen,
    EditObjectScreen,
    ObjectScreen,
    SavePathScreen,
)
from weavtui.theme import DEFAULT_THEME, THEMES

MIN_PAGE_SIZE = 10
CELL_WIDTH = 40


def page_size_for(visible_rows: int) -> int:
    """Rows per page: visible table rows rounded up to the next ten, at least 10."""
    return max(MIN_PAGE_SIZE, math.ceil(visible_rows / 10) * 10)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(to_jsonable(value), ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= CELL_WIDTH else text[: CELL_WIDTH - 1] + "…"


class ObjectsTable(DataTable):
    BINDINGS = [Binding("delete", "app.delete_object", "Delete object")]

    def on_resize(self) -> None:
        self.app.schedule_page_size_update()  # type: ignore[attr-defined]


class WeavtuiApp(App[None]):
    CSS_PATH = "weavtui.tcss"
    TITLE = "weavtui"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("n", "new_object", "New object"),
        Binding("D", "delete_collection", "Delete collection", key_display="D"),
        Binding("left_square_bracket", "prev_page", "Prev page", key_display="["),
        Binding("right_square_bracket", "next_page", "Next page", key_display="]"),
        Binding("slash", "focus_search", "Search", key_display="/"),
        Binding("g", "diagram", "Diagram"),
    ]

    def __init__(self, backend: Backend, start: str = "diagram") -> None:
        """``start`` is the first view after connecting: "diagram" or "browse"."""
        super().__init__()
        self.start = start
        for theme in THEMES:
            self.register_theme(theme)
        self.theme = DEFAULT_THEME
        self.backend = backend
        self.collections: dict[str, CollectionInfo] = {}
        self.current: str | None = None
        self.query_text = ""
        self.filter_text = ""
        self.page_index = 0
        self.page_size = MIN_PAGE_SIZE
        # "after" uuid that starts each page when browsing unfiltered; pages without a
        # known cursor (e.g. after a resize) are fetched by offset instead.
        self.cursors: dict[int, str | None] = {0: None}
        self.has_next = False
        self._page_size_timer: Timer | None = None  # not "_resize_timer": App uses that name

    # -- layout -------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Collections", classes="section")
                yield OptionList(id="collections")
            initial = "tab-diagram" if self.start == "diagram" else "tab-objects"
            with TabbedContent(id="main", initial=initial):
                with TabPane("Diagram", id="tab-diagram"):
                    yield Label(
                        "←↑→↓ select · Enter browse objects · e export Mermaid · "
                        "references to itself are listed in the box",
                        id="diagram-legend",
                    )
                    yield DiagramView(id="diagram-view")
                with TabPane("Objects", id="tab-objects"):
                    with Horizontal(id="query-bar"):
                        yield Input(placeholder="Keyword search (BM25), Enter to run", id="search")
                        yield Input(
                            placeholder="Filter, e.g. year >= 2020 and published = true",
                            id="filter",
                        )
                    yield ObjectsTable(id="objects", cursor_type="row", zebra_stripes=True)
                    yield Label("", id="pager")
                with TabPane("Schema", id="tab-schema"), VerticalScroll():
                    yield Static("", id="schema")
        yield Footer()

    MAIN_SCREEN_ACTIONS = frozenset(
        {
            "refresh",
            "new_object",
            "delete_collection",
            "prev_page",
            "next_page",
            "focus_search",
            "diagram",
            "export_diagram",
        }
    )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Main-screen shortcuts must not fire (or show in the footer) over other screens.
        if action in self.MAIN_SCREEN_ACTIONS and len(self.screen_stack) > 1:
            return False
        # Paging/search keys are hidden from the footer on the diagram tab.
        return not (action in self.OBJECTS_ONLY_ACTIONS and self._on_diagram_tab())

    OBJECTS_ONLY_ACTIONS = frozenset({"prev_page", "next_page", "focus_search", "diagram"})

    def _on_diagram_tab(self) -> bool:
        tabs = self.query(TabbedContent)
        return bool(tabs) and tabs.first().active == "tab-diagram"

    @on(TabbedContent.TabActivated)
    def _tab_changed(self) -> None:
        self.refresh_bindings()

    def on_mount(self) -> None:
        self.connect()

    def schedule_page_size_update(self) -> None:
        # Debounce: a window drag produces a burst of resize events.
        if self._page_size_timer is not None:
            self._page_size_timer.stop()
        self._page_size_timer = self.set_timer(0.2, self._apply_page_size)

    def _measured_page_size(self) -> int | None:
        table = self.query_one(ObjectsTable)
        visible = table.scrollable_content_region.height - table.header_height
        return page_size_for(visible) if table.size.height > 0 else None

    def _apply_page_size(self) -> None:
        new = self._measured_page_size()
        if new is None or new == self.page_size:
            return
        first = self.page_index * self.page_size  # keep the first shown object on screen
        self.page_size = new
        self.page_index = first // new
        self.cursors = {0: None}
        if self.current:
            self.load_page()

    async def on_unmount(self) -> None:
        await asyncio.to_thread(self.backend.close)

    # -- connection & collections -------------------------------------------------------

    @work(exclusive=True, group="connect")
    async def connect(self) -> None:
        label = self.backend.settings.label()
        self.sub_title = f"connecting to {label}…"
        try:
            version = await asyncio.to_thread(self.backend.connect)
        except BackendError as exc:
            self.sub_title = f"{label} · disconnected"
            if await self.push_screen_wait(ConnectErrorScreen(str(exc))):
                self.connect()
            else:
                self.exit()
            return
        self.sub_title = f"{label} · Weaviate {version}"
        await self.load_collections()
        if self.start == "diagram":
            self.query_one(DiagramView).focus()

    async def load_collections(self) -> None:
        try:
            infos = await asyncio.to_thread(self.backend.list_collections)
        except BackendError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        self.collections = {c.name: c for c in infos}
        options = self.query_one("#collections", OptionList)
        options.clear_options()
        options.add_options(Option(self._collection_label(c), id=c.name) for c in infos)
        keep_current = self.current if self.current in self.collections else None
        self.query_one(DiagramView).set_infos(infos, keep_current)
        if not infos:
            self.current = None
            self._clear_objects("No collections")
            self.query_one("#schema", Static).update("")
            return
        keep = self.current if self.current in self.collections else infos[0].name
        options.highlighted = options.get_option_index(keep)
        if keep != self.current:
            self.select_collection(keep)

    @staticmethod
    def _collection_label(info: CollectionInfo) -> Text:
        label = Text.assemble(info.name, (f"  {'?' if info.count is None else info.count}", "dim"))
        if info.aliases:
            label.append(f"\n  ↳ {', '.join(info.aliases)}", style="dim italic")
        return label

    @on(OptionList.OptionHighlighted, "#collections")
    def _collection_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id and event.option.id != self.current:
            self.select_collection(event.option.id)

    def select_collection(self, name: str) -> None:
        self.current = name
        self.query_one(DiagramView).select(name)
        self._reset_paging()
        self.load_page()
        self.load_schema()

    # -- objects table ------------------------------------------------------------------

    def _reset_paging(self) -> None:
        self.page_index = 0
        self.cursors = {0: None}
        self.has_next = False
        self.page_size = self._measured_page_size() or self.page_size

    def _clear_objects(self, message: str = "") -> None:
        self.query_one(ObjectsTable).clear(columns=True)
        self.query_one("#pager", Label).update(message)

    @work(exclusive=True, group="page")
    async def load_page(self) -> None:
        name, idx, size = self.current, self.page_index, self.page_size
        if name is None:
            return
        searching = bool(self.query_text.strip() or self.filter_text.strip())
        kwargs: dict[str, Any] = {
            "limit": size,
            "query": self.query_text,
            "filter_text": self.filter_text,
        }
        if not searching and idx in self.cursors:
            kwargs["after"] = self.cursors[idx]
        else:
            kwargs["offset"] = idx * size
        self.query_one("#pager", Label).update("Loading…")
        try:
            page = await asyncio.to_thread(self.backend.fetch_page, name, **kwargs)
        except BackendError as exc:
            self._clear_objects("Error")
            self.notify(str(exc), severity="error", timeout=8)
            return
        if name != self.current or size != self.page_size:
            return
        if not searching and page.objects:
            self.cursors[idx + 1] = page.objects[-1].uuid
        self.has_next = len(page.objects) == size and (
            page.total is None or (idx + 1) * size < page.total
        )
        self._show_page(name, page)

    def _show_page(self, name: str, page: Page) -> None:
        table = self.query_one(ObjectsTable)
        table.clear(columns=True)
        info = self.collections.get(name)
        columns = [p.name for p in info.properties] if info else []
        for obj in page.objects:  # properties missing from the schema listing
            columns.extend(k for k in obj.properties if k not in columns)
        show_score = bool(self.query_text.strip())
        table.add_column("uuid", key="uuid")
        if show_score:
            table.add_column("score", key="score")
        for col in columns:
            table.add_column(col, key=col)
        for obj in page.objects:
            row = [obj.uuid[:8]]
            if show_score:
                row.append("" if obj.score is None else f"{obj.score:.3f}")
            row.extend(_cell(obj.properties.get(c)) for c in columns)
            table.add_row(*row, key=obj.uuid)

        first = self.page_index * self.page_size + 1
        last = self.page_index * self.page_size + len(page.objects)
        if page.total is not None:
            pages = max(1, -(-page.total // self.page_size))
            status = f"Page {self.page_index + 1}/{pages} · {page.total} objects"
        else:
            status = f"Page {self.page_index + 1}"
        if page.objects:
            status += f" · showing {first}–{last}"
        else:
            status += " · no objects"
        if self.query_text.strip() or self.filter_text.strip():
            status += " · filtered"
        self.query_one("#pager", Label).update(status)

    def action_next_page(self) -> None:
        if self.current and self.has_next:
            self.page_index += 1
            self.load_page()

    def action_prev_page(self) -> None:
        if self.current and self.page_index > 0:
            self.page_index -= 1
            self.load_page()

    def action_focus_search(self) -> None:
        self.query_one(TabbedContent).active = "tab-objects"
        self.query_one("#search", Input).focus()

    @on(Input.Submitted, "#search, #filter")
    def _search_submitted(self) -> None:
        self.query_text = self.query_one("#search", Input).value
        self.filter_text = self.query_one("#filter", Input).value
        self._reset_paging()
        self.load_page()
        self.query_one(ObjectsTable).focus()

    def _selected_uuid(self) -> str | None:
        table = self.query_one(ObjectsTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    # -- schema -------------------------------------------------------------------------

    @work(exclusive=True, group="schema")
    async def load_schema(self) -> None:
        name = self.current
        if name is None:
            return
        try:
            config = await asyncio.to_thread(self.backend.collection_config, name)
        except BackendError as exc:
            self.query_one("#schema", Static).update(Text(str(exc), style="red"))
            return
        if name != self.current:
            return
        info = self.collections.get(name)
        summary = Text.assemble(("Collection  ", "dim"), (name, "bold"))
        summary.append("\nAliases     ", style="dim")
        summary.append(", ".join(info.aliases) if info and info.aliases else "-")
        if info and info.description:
            summary.append("\nDescription ", style="dim").append(info.description)
        edges = reference_graph(list(self.collections.values())).get(name)
        if edges and (edges.outgoing or edges.incoming):
            summary.append("\nReferences  ", style="dim")
            theme = self.current_theme
            lines = [
                outgoing_label(p, t, t in self.collections, theme.primary)
                for p, t in edges.outgoing
            ]
            lines += [incoming_label(s, p, theme.accent or "magenta") for s, p in edges.incoming]
            summary.append(Text("\n            ").join(lines))
        self.query_one("#schema", Static).update(
            Group(summary, Text(), JSON(json.dumps(config, ensure_ascii=False)))
        )

    # -- object actions -----------------------------------------------------------------

    @on(DataTable.RowSelected, "#objects")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        if self.current and event.row_key.value:
            self.open_object(self.current, event.row_key.value)

    @work(exclusive=True, group="object")
    async def open_object(self, name: str, uuid: str) -> None:
        try:
            record = await asyncio.to_thread(self.backend.get_object, name, uuid)
        except BackendError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        if record is None:
            self.notify(f"Object {uuid} no longer exists", severity="warning")
            self.load_page()
            return
        choice = await self.push_screen_wait(ObjectScreen(name, record))
        if choice == "edit":
            edited = await self.push_screen_wait(
                EditObjectScreen(f"Edit {name} / {uuid}", record.properties)
            )
            if edited is None:
                return
            original = to_jsonable(record.properties)
            changes = {k: v for k, v in edited.items() if original.get(k) != v}
            changes.update({k: None for k in original if k not in edited})
            if not changes:
                self.notify("No changes")
                return
            try:
                await asyncio.to_thread(self.backend.update_object, name, uuid, changes)
            except BackendError as exc:
                self.notify(str(exc), severity="error", timeout=8)
                return
            self.notify(f"Updated {len(changes)} propert{'y' if len(changes) == 1 else 'ies'}")
            self.load_page()
        elif choice == "delete":
            await self._delete_object(name, uuid)

    async def _delete_object(self, name: str, uuid: str) -> None:
        if not await self.push_screen_wait(ConfirmScreen(f"Delete object {uuid} from {name}?")):
            return
        try:
            await asyncio.to_thread(self.backend.delete_object, name, uuid)
        except BackendError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        self.notify(f"Deleted {uuid}")
        await self.load_collections()
        self.load_page()

    @work(exclusive=True, group="object")
    async def action_delete_object(self) -> None:
        uuid = self._selected_uuid()
        if self.current and uuid:
            await self._delete_object(self.current, uuid)

    @work(exclusive=True, group="object")
    async def action_new_object(self) -> None:
        name = self.current
        if name is None:
            self.notify("Select a collection first", severity="warning")
            return
        info = self.collections[name]
        template = {p.name: default_value(p.data_type) for p in info.properties}
        props = await self.push_screen_wait(EditObjectScreen(f"New object in {name}", template))
        if props is None:
            return
        props = {k: v for k, v in props.items() if v is not None}
        try:
            uuid = await asyncio.to_thread(self.backend.insert_object, name, props)
        except BackendError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        self.notify(f"Created {uuid}")
        await self.load_collections()
        self.load_page()

    @work(exclusive=True, group="object")
    async def action_delete_collection(self) -> None:
        name = self.current
        if name is None:
            return
        info = self.collections.get(name)
        count = "?" if info is None or info.count is None else info.count
        confirmed = await self.push_screen_wait(
            ConfirmScreen(
                f"Delete collection {name} with all its {count} objects? This cannot be undone.",
                type_to_confirm=name,
            )
        )
        if not confirmed:
            return
        try:
            await asyncio.to_thread(self.backend.delete_collection, name)
        except BackendError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        self.notify(f"Deleted collection {name}")
        self.current = None
        await self.load_collections()

    def action_diagram(self) -> None:
        self.query_one(TabbedContent).active = "tab-diagram"
        self.query_one(DiagramView).focus()

    @on(DiagramView.Selected)
    def _diagram_selected(self, event: DiagramView.Selected) -> None:
        if event.name in self.collections and event.name != self.current:
            options = self.query_one("#collections", OptionList)
            options.highlighted = options.get_option_index(event.name)  # -> select_collection

    @on(DiagramView.Opened)
    def _diagram_opened(self, event: DiagramView.Opened) -> None:
        self._diagram_selected(DiagramView.Selected(event.name))
        self.query_one(TabbedContent).active = "tab-objects"
        self.query_one(ObjectsTable).focus()

    @work(exclusive=True, group="object")
    async def action_export_diagram(self) -> None:
        source = to_mermaid(list(self.collections.values()))
        path = await self.push_screen_wait(
            SavePathScreen("Export schema as Mermaid", Path("weaviate-schema.mmd"))
        )
        if path is None:
            return
        if path.exists() and not await self.push_screen_wait(
            ConfirmScreen(f"{path} already exists. Overwrite it?", confirm_label="Overwrite")
        ):
            return
        try:
            await asyncio.to_thread(path.write_text, source)
        except OSError as exc:
            self.notify(f"Cannot write {path}: {exc}", severity="error", timeout=8)
            return
        self.copy_to_clipboard(source)
        self.notify(f"Mermaid diagram saved to {path} (also copied to the clipboard)")

    @work(exclusive=True, group="refresh")
    async def action_refresh(self) -> None:
        await self.load_collections()
        if self.current:
            self.load_page()
            self.load_schema()
