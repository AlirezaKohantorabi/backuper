"""Documentation guard: every code file has a document, every function/class is mentioned in it,
and the changelog covers the current version."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE_DIRS = ("backuper", "tests")


def _code_files() -> list[Path]:
    return sorted(p for d in CODE_DIRS for p in (ROOT / d).rglob("*.py") if "__pycache__" not in p.parts)


def _names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
            if isinstance(node, ast.ClassDef):
                names += [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return names


def _doc(path: Path) -> Path:
    return ROOT / "docs" / "code" / path.relative_to(ROOT).with_suffix(".md")


def test_every_code_file_has_a_document():
    missing = [str(p.relative_to(ROOT)) for p in _code_files() if not _doc(p).exists()]
    assert not missing, f"no docs/code/… page for: {missing}"


def test_every_function_and_class_is_documented():
    gaps = {}
    for p in _code_files():
        doc = _doc(p).read_text(encoding="utf-8") if _doc(p).exists() else ""
        absent = [n for n in _names(p) if n not in doc]
        if absent:
            gaps[str(p.relative_to(ROOT))] = absent
    assert not gaps, f"undocumented names: {gaps}"


def test_changelog_mentions_current_version():
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "backuper" / "__init__.py").read_text()).group(1)
    assert f"[{version}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
