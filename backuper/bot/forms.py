"""A tiny form engine: walks a user through a list of Fields, one message per field.

The panel decides what happens when a form completes (see FormState.kind).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from telethon import Button

from backuper.destinations.telegram import esc
from backuper.fields import BOOL, CHOICE, CREDENTIAL, SECRET, Field, FieldError


@dataclass
class FormState:
    kind: str                       # what to do on completion, e.g. "source_add", "cred_add", "setting"
    title: str
    fields: list[Field]
    values: dict[str, Any] = field(default_factory=dict)
    current: dict[str, Any] = field(default_factory=dict)  # existing values when editing
    editing: bool = False
    index: int = 0
    target: Any = None              # source id / credential id / setting key …
    type_id: str | None = None
    message_id: int | None = None   # the prompt message we keep editing
    parent: FormState | None = None  # nested form (e.g. "new credential" while adding a source)
    parent_field: str | None = None
    choices: list[str] = field(default_factory=list)  # credential names offered in the current prompt

    # -- navigation -----------------------------------------------------------
    def merged(self) -> dict[str, Any]:
        return {**self.current, **self.values}

    def current_field(self) -> Field | None:
        while self.index < len(self.fields):
            f = self.fields[self.index]
            if f.visible(self.merged()):
                return f
            self.index += 1
        return None

    def visible_count(self) -> tuple[int, int]:
        vals = self.merged()
        vis = [f for f in self.fields if f.visible(vals)]
        cur = self.current_field()
        pos = vis.index(cur) + 1 if cur in vis else len(vis)
        return pos, len(vis)

    def accept(self, raw: Any) -> None:
        f = self.current_field()
        assert f is not None
        self.values[f.key] = f.parse(raw)
        self.index += 1

    def use_default(self) -> None:
        f = self.current_field()
        assert f is not None
        self.values[f.key] = f.default
        self.index += 1

    def keep_current(self) -> None:
        f = self.current_field()
        assert f is not None
        if f.key in self.current:
            self.values[f.key] = self.current[f.key]
        self.index += 1

    def clear(self) -> None:
        f = self.current_field()
        assert f is not None
        if f.required:
            raise FieldError(f"{f.label} can't be empty")
        self.values[f.key] = [] if f.kind == "list" else ""
        self.index += 1


def prompt(state: FormState, credentials: list[str]) -> tuple[str, list[list[Button]]]:
    """Text + buttons asking for the current field."""
    f = state.current_field()
    assert f is not None
    state.choices = list(credentials[:40]) if f.kind == CREDENTIAL else []
    pos, total = state.visible_count()
    lines = [f"<b>{esc(state.title)}</b>" + (f"  ({pos}/{total})" if total > 1 else ""), "",
             f"✏️ <b>{esc(f.label)}</b>"]
    if f.help:
        lines.append(esc(f.help))
    if f.example:
        lines.append(f"Example: <code>{esc(f.example)}</code>")
    if state.editing and f.key in state.current:
        lines.append(f"Current: <code>{esc(f.display(state.current[f.key]))}</code>")
    elif f.default not in (None, "", []):
        lines.append(f"Default: <code>{esc(f.display(f.default))}</code>")
    if f.kind == SECRET:
        lines.append("🔒 Your message will be deleted right after it's read.")
    elif f.kind not in (BOOL, CHOICE, CREDENTIAL):
        lines.append("<i>Send the value as a message.</i>")

    rows: list[list[Button]] = []
    if f.kind == BOOL:
        rows.append([Button.inline("✅ Yes", b"f:yes"), Button.inline("❌ No", b"f:no")])
    elif f.kind == CHOICE:
        rows += [[Button.inline(c, f"f:v:{i}".encode())] for i, c in enumerate(f.choices)]
    elif f.kind == CREDENTIAL:
        for i, name in enumerate(state.choices):
            rows.append([Button.inline(f"🔑 {name}", f"f:c:{i}".encode())])
        rows.append([Button.inline("➕ New credential", b"f:newcred")])

    extra = []
    if state.editing and f.key in state.current:
        extra.append(Button.inline("↩️ Keep current", b"f:keep"))
    elif f.default is not None and not (f.required and f.default in ("", [])):
        extra.append(Button.inline("⏭ Use default", b"f:def"))
    if not f.required and (state.editing or f.default in (None, "", [])):
        extra.append(Button.inline("🧹 Empty", b"f:clr"))
    if extra:
        rows.append(extra)
    rows.append([Button.inline("✖️ Cancel", b"f:cancel")])
    return "\n".join(lines), rows


def summary(state: FormState) -> str:
    vals = state.merged()
    lines = [f"<b>{esc(state.title)}</b>", ""]
    for f in state.fields:
        if f.visible(vals) and f.key in vals:
            lines.append(f"• {esc(f.label)}: <code>{esc(f.display(vals[f.key]))}</code>")
    return "\n".join(lines)
