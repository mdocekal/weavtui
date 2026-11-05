"""Secondary screens: object detail, JSON editor and confirmation dialogs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.json import JSON
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static, TextArea

from weavtui.backend import ObjectRecord, to_jsonable


def _properties_table(properties: dict[str, Any]) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0), expand=True)
    table.add_column(style="bold", no_wrap=True)
    table.add_column(ratio=1, overflow="fold")
    for name in sorted(properties):
        value = to_jsonable(properties[name])
        if value is None:
            table.add_row(name, Text("null", style="dim italic"))
        elif isinstance(value, str):
            table.add_row(name, value)
        else:
            table.add_row(name, JSON(json.dumps(value, ensure_ascii=False)))
    return table


def _vector_summary(vectors: dict[str, Any]) -> Text:
    if not vectors:
        return Text("(no vectors)", style="dim")
    text = Text()
    for name, vec in vectors.items():
        if vec and isinstance(vec[0], list):  # multi-vector
            text.append(f"{name}: multi-vector, {len(vec)} x {len(vec[0])}\n", style="bold")
            continue
        head = ", ".join(f"{v:.4f}" for v in vec[:8])
        more = ", …" if len(vec) > 8 else ""
        text.append(f"{name}", style="bold")
        text.append(f" ({len(vec)} dims): [{head}{more}]\n")
    return text


class ObjectScreen(Screen[str | None]):
    """Full view of a single object. Dismisses with "edit", "delete" or None."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Back"),
        Binding("e", "dismiss('edit')", "Edit"),
        Binding("delete", "dismiss('delete')", "Delete"),
        Binding("v", "toggle_vectors", "Full vectors"),
    ]

    def __init__(self, collection: str, record: ObjectRecord) -> None:
        super().__init__()
        self.collection = collection
        self.record = record
        self.full_vectors = False

    def compose(self) -> ComposeResult:
        r = self.record
        yield Header()
        with VerticalScroll(id="object-view"):
            meta = Text()
            meta.append("Collection  ", style="dim").append(f"{self.collection}\n")
            meta.append("UUID        ", style="dim").append(f"{r.uuid}\n")
            meta.append("Created     ", style="dim").append(f"{r.created or '-'}\n")
            meta.append("Updated     ", style="dim").append(f"{r.updated or '-'}")
            yield Static(meta, id="object-meta")
            yield Label("Properties", classes="section")
            yield Static(_properties_table(r.properties))
            yield Label("Vectors", classes="section")
            yield Static(_vector_summary(r.vectors), id="object-vectors")
        yield Footer()

    def action_toggle_vectors(self) -> None:
        self.full_vectors = not self.full_vectors
        widget = self.query_one("#object-vectors", Static)
        if self.full_vectors and self.record.vectors:
            widget.update(JSON(json.dumps(self.record.vectors)))
        else:
            widget.update(_vector_summary(self.record.vectors))


class EditObjectScreen(ModalScreen[dict[str, Any] | None]):
    """Edit an object's properties as JSON. Dismisses with the new properties or None."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save", priority=True),
    ]

    def __init__(self, title: str, properties: dict[str, Any]) -> None:
        super().__init__()
        self.dialog_title = title
        self.initial = json.dumps(to_jsonable(properties), indent=2, ensure_ascii=False)

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label(self.dialog_title, classes="dialog-title")
            yield Label("Properties as JSON. Ctrl+S saves, Esc cancels.", classes="hint")
            yield TextArea(self.initial, id="editor", show_line_numbers=True, tab_behavior="indent")
            yield Label("", id="edit-error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        try:
            data = json.loads(self.query_one("#editor", TextArea).text)
        except json.JSONDecodeError as exc:
            self.query_one("#edit-error", Label).update(f"Invalid JSON: {exc}")
            return
        if not isinstance(data, dict):
            self.query_one("#edit-error", Label).update("Top level must be a JSON object")
            return
        self.dismiss(data)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no question. If ``type_to_confirm`` is set, the user must type it to enable Yes."""

    BINDINGS = [Binding("escape", "dismiss(False)", "Cancel")]

    def __init__(
        self, message: str, *, type_to_confirm: str | None = None, confirm_label: str = "Delete"
    ) -> None:
        super().__init__()
        self.message = message
        self.type_to_confirm = type_to_confirm
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.message, classes="dialog-title")
            if self.type_to_confirm:
                yield Label(f"Type [b]{self.type_to_confirm}[/b] to confirm:", classes="hint")
                yield Input(id="confirm-input")
            with Horizontal(classes="buttons"):
                yield Button(
                    self.confirm_label,
                    variant="error",
                    id="yes",
                    disabled=bool(self.type_to_confirm),
                )
                yield Button("Cancel", id="no")

    def on_mount(self) -> None:
        if self.type_to_confirm:
            self.query_one(Input).focus()
        else:
            self.query_one("#no", Button).focus()

    @on(Input.Changed, "#confirm-input")
    def _check(self, event: Input.Changed) -> None:
        self.query_one("#yes", Button).disabled = event.value != self.type_to_confirm

    @on(Input.Submitted, "#confirm-input")
    def _submit(self, event: Input.Submitted) -> None:
        if event.value == self.type_to_confirm:
            self.dismiss(True)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class SavePathScreen(ModalScreen[Path | None]):
    """Ask for a file path. A directory gets ``default_name`` appended."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, default: Path) -> None:
        super().__init__()
        self.dialog_title = title
        self.default = default

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.dialog_title, classes="dialog-title")
            yield Static(
                f"File path (Enter saves, Esc cancels). Relative paths start in {Path.cwd()}",
                classes="hint",
            )
            yield Input(str(self.default), id="save-path")
            yield Label("", id="save-error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted, "#save-path")
    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        raw = self.query_one("#save-path", Input).value.strip()
        if not raw:
            self.query_one("#save-error", Label).update("Enter a path")
            return
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if path.is_dir():
            path = path / self.default.name
        if not path.parent.is_dir():
            self.query_one("#save-error", Label).update(f"Directory {path.parent} does not exist")
            return
        self.dismiss(path)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class ConnectErrorScreen(ModalScreen[bool]):
    """Shown when connecting fails. Dismisses with True to retry, False to quit."""

    BINDINGS = [Binding("r", "dismiss(True)", "Retry"), Binding("q", "dismiss(False)", "Quit")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Connection failed", classes="dialog-title error")
            yield Static(self.message, classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Retry", variant="primary", id="retry")
                yield Button("Quit", id="quit")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "retry")
