"""Declarative form fields.

Every source type, the credential form and the settings page describe their
inputs with `Field`s. The Telegram admin panel renders add/edit wizards from
these declarations, and the YAML importer validates with them, so a new
source type only has to list its fields — no bot code needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from apscheduler.triggers.cron import CronTrigger

TEXT, INT, BOOL, CHOICE, LIST, SECRET, CREDENTIAL, SCHEDULE, UNC, HOST = (
    "text", "int", "bool", "choice", "list", "secret", "credential", "schedule", "unc", "host",
)

_TRUE = {"1", "y", "yes", "true", "on", "بله", "آره"}
_FALSE = {"0", "n", "no", "false", "off", "خیر", "نه"}
_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


class FieldError(ValueError):
    pass


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = TEXT
    required: bool = True
    default: Any = None
    help: str = ""
    example: str = ""
    choices: tuple[str, ...] = ()
    min: int | None = None
    max: int | None = None
    # Shown in the bot only when this predicate on the other values is true,
    # e.g. ("auth", "password") -> only if values["auth"] == "password".
    show_if: tuple[str, Any] | None = None
    tags: frozenset[str] = field(default_factory=frozenset)

    @property
    def secret(self) -> bool:
        return self.kind == SECRET

    def visible(self, values: dict[str, Any]) -> bool:
        if not self.show_if:
            return True
        key, expected = self.show_if
        return values.get(key) == expected

    # -- parsing -----------------------------------------------------------
    def parse(self, raw: Any) -> Any:
        """Convert user input (text from Telegram or a YAML value) into a stored value."""
        if raw is None or (isinstance(raw, str) and raw.strip() in ("", "-")):
            if self.required and self.default is None:
                raise FieldError(f"{self.label} is required")
            return self.default

        k = self.kind
        if k == INT:
            try:
                v = int(str(raw).strip())
            except ValueError as e:
                raise FieldError(f"{self.label} must be a whole number") from e
            if self.min is not None and v < self.min:
                raise FieldError(f"{self.label} must be ≥ {self.min}")
            if self.max is not None and v > self.max:
                raise FieldError(f"{self.label} must be ≤ {self.max}")
            return v
        if k == BOOL:
            if isinstance(raw, bool):
                return raw
            s = str(raw).strip().lower()
            if s in _TRUE:
                return True
            if s in _FALSE:
                return False
            raise FieldError(f"{self.label}: answer yes or no")
        if k == CHOICE:
            s = str(raw).strip()
            for c in self.choices:
                if c.lower() == s.lower():
                    return c
            raise FieldError(f"{self.label} must be one of: {', '.join(self.choices)}")
        if k == LIST:
            if isinstance(raw, (list, tuple)):
                items = [str(x).strip() for x in raw]
            else:
                items = [x.strip() for x in re.split(r"[,\n]", str(raw))]
            items = [x for x in items if x]
            if self.required and not items:
                raise FieldError(f"{self.label} needs at least one value")
            return items
        if k == SCHEDULE:
            return parse_schedule(str(raw))
        if k == UNC:
            return normalize_unc(str(raw))
        if k == HOST:
            s = str(raw).strip()
            if not _HOST_RE.match(s):
                raise FieldError(f"{self.label}: '{s}' is not a valid host name or IP")
            return s
        # TEXT, SECRET, CREDENTIAL
        return str(raw).strip() if k != SECRET else str(raw)

    # -- display -----------------------------------------------------------
    def display(self, value: Any) -> str:
        if value is None or value == "" or value == []:
            return "—"
        if self.kind == SECRET:
            return "••••••"
        if self.kind == BOOL:
            return "yes" if value else "no"
        if self.kind == LIST:
            return ", ".join(value)
        if self.kind == SCHEDULE:
            return describe_schedule(value)
        return str(value)


def parse_values(fields: list[Field], raw: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Validate a whole dict (used by the YAML importer)."""
    out: dict[str, Any] = {}
    known = {f.key for f in fields}
    unknown = set(raw) - known
    if unknown:
        raise FieldError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    for f in fields:
        if partial and f.key not in raw:
            continue
        out[f.key] = f.parse(raw.get(f.key))
    return out


# -- schedule helpers ----------------------------------------------------------

def parse_schedule(text: str) -> str:
    """Accept '03:00', '03:00, 15:30' or cron lines separated by ';'.

    Returns a canonical ';'-separated list of 5-field cron expressions.
    """
    text = text.strip()
    if not text or text == "-":
        return ""
    crons: list[str] = []
    if ";" in text or len(text.split()) == 5:
        parts = [p.strip() for p in text.split(";") if p.strip()]
    else:
        parts = [p.strip() for p in re.split(r"[,\s]+", text) if p.strip()]
    for p in parts:
        m = _TIME_RE.match(p)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if not (0 <= h <= 23 and 0 <= mi <= 59):
                raise FieldError(f"'{p}' is not a valid time")
            crons.append(f"{mi} {h} * * *")
            continue
        try:
            CronTrigger.from_crontab(p)
        except ValueError as e:
            raise FieldError(f"'{p}' is neither HH:MM nor a valid cron expression ({e})") from e
        crons.append(" ".join(p.split()))
    return ";".join(dict.fromkeys(crons))


def describe_schedule(value: str) -> str:
    if not value:
        return "—"
    out = []
    for cron in value.split(";"):
        f = cron.split()
        if len(f) == 5 and f[2:] == ["*", "*", "*"] and f[0].isdigit() and f[1].isdigit():
            out.append(f"daily {int(f[1]):02d}:{int(f[0]):02d}")
        else:
            out.append(f"cron '{cron}'")
    return ", ".join(out)


# -- path helpers ------------------------------------------------------------------

def normalize_unc(text: str) -> str:
    r"""'//srv/share/dir', '\\srv\share\dir\' -> '\\srv\share\dir'."""
    s = text.strip().replace("/", "\\")
    if not s.startswith("\\\\"):
        raise FieldError(r"Network path must look like \\server\share\folder")
    parts = [p for p in s[2:].split("\\") if p]
    if len(parts) < 2:
        raise FieldError(r"Network path needs at least \\server\share")
    return "\\\\" + "\\".join(parts)


def unc_server(path: str) -> str:
    return path.lstrip("\\").split("\\", 1)[0]
