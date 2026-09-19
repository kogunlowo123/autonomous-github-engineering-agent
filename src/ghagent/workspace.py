"""Applying model-proposed edits to a working copy, all-or-nothing and under policy."""

from __future__ import annotations

from pathlib import Path

from ghagent.errors import EditError
from ghagent.models import Edit
from ghagent.security import PathPolicy


def _resolve_inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise EditError(f"{relative}: resolves outside the repository")
    current = target
    root_resolved = root.resolve()
    while current != root_resolved:
        if current.is_symlink():
            raise EditError(f"{relative}: symbolic links are not editable")
        current = current.parent
    return target


def apply_edits(
    root: Path,
    edits: list[Edit],
    policy: PathPolicy,
    *,
    max_files: int,
    max_file_bytes: int,
) -> list[str]:
    """Apply ``edits`` under ``root`` and return the changed paths.

    Every edit is validated and computed in memory first. If any edit is invalid nothing is written,
    so a failed attempt never leaves a half-applied change behind.

    Raises:
        EditError: With a message precise enough to feed back to the model.
    """
    paths = list(dict.fromkeys(e.path for e in edits))
    if len(paths) > max_files:
        raise EditError(f"change touches {len(paths)} files, the limit is {max_files}")

    pending: dict[str, str] = {}
    for edit in edits:
        reason = policy.check(edit.path)
        if reason:
            raise EditError(f"{edit.path}: {reason}")
        target = _resolve_inside(root, edit.path)

        if edit.path in pending:
            current = pending[edit.path]
        elif target.exists():
            if not target.is_file():
                raise EditError(f"{edit.path}: not a regular file")
            raw = target.read_bytes()
            if b"\x00" in raw:
                raise EditError(f"{edit.path}: binary files are not editable")
            current = raw.decode("utf-8", errors="strict")
        else:
            current = ""

        if edit.action == "create":
            if target.exists() or edit.path in pending:
                raise EditError(f"{edit.path}: file already exists; use a replace edit")
            if edit.content is None:
                raise EditError(f"{edit.path}: create edits need 'content'")
            new_text = edit.content
        else:
            if not target.exists() and edit.path not in pending:
                raise EditError(f"{edit.path}: file does not exist")
            if not edit.search:
                raise EditError(f"{edit.path}: replace edits need a non-empty 'search'")
            if edit.replace is None:
                raise EditError(f"{edit.path}: replace edits need 'replace'")
            count = current.count(edit.search)
            if count != 1:
                raise EditError(
                    f"{edit.path}: 'search' text must match exactly once, found {count} matches"
                )
            new_text = current.replace(edit.search, edit.replace, 1)

        if "\x00" in new_text:
            raise EditError(f"{edit.path}: content contains NUL characters")
        if len(new_text.encode("utf-8")) > max_file_bytes:
            raise EditError(f"{edit.path}: result exceeds {max_file_bytes} bytes")
        pending[edit.path] = new_text

    for relative, text in pending.items():
        target = _resolve_inside(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")
    return list(pending)
