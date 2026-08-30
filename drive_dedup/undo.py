"""Reverse a run: move duplicates out of the duplicates folder and back home.

The tool argues its safety from reversibility -- nothing is deleted, everything
is moved into a folder you name. Until now reversing that meant opening the
folder and dragging a few hundred files back by hand, which is not a solved
problem, just a deferred one.

Planning is separated from doing on purpose. `plan_undo` reads a log and
decides what should happen without touching anything, which is what makes the
dry run truthful and the whole thing testable without a cloud account.
"""

import json
import logging
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from .models import LOG_VERSION

logger = logging.getLogger(__name__)


class UndoNotPossible(Exception):
    """The log cannot be reversed, with a reason worth printing."""


@dataclass
class UndoStep:
    """One file to put back."""

    file_id: str
    name: str
    target_folder_id: str      # where it came from
    duplicates_folder_id: str  # where the run put it
    # Drive lets a file sit in several folders. Restoring to the first is the
    # decision taken here; the rest are recorded so the report can say so.
    other_targets: List[str]

    @property
    def had_several_parents(self) -> bool:
        return bool(self.other_targets)


def read_log(path: str) -> Iterator[Dict]:
    """Yield the records of a JSONL log, skipping blank and broken lines."""
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                logger.warning("Skipping unreadable line %d of %s", number, path)
                continue
            if isinstance(record, dict):
                yield record


def plan_undo(records: List[Dict]) -> Tuple[List[UndoStep], List[str]]:
    """Work out what putting this run back would mean.

    Returns the steps and a list of notes about everything that was skipped,
    so the caller can print them rather than have them disappear.

    Raises:
        UndoNotPossible: the log holds moves but records no origins at all.
    """
    steps: List[UndoStep] = []
    notes: List[str] = []
    moved_records = 0
    with_origin = 0

    for record in records:
        if record.get("action") != "moved":
            continue
        moved_records += 1

        duplicates_folder = record.get("move_target_folder_id") or ""
        if not duplicates_folder:
            notes.append("A record says files were moved but names no target folder; skipped.")
            continue

        entries = record.get("moved")
        if not isinstance(entries, list) or not entries:
            continue
        with_origin += 1

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            file_id = str(entry.get("id") or "")
            name = str(entry.get("name") or file_id)
            origins = [str(p) for p in (entry.get("from") or []) if p]
            if not file_id:
                continue
            if not origins:
                notes.append(f"{name}: no origin folder recorded, cannot be put back.")
                continue
            steps.append(UndoStep(
                file_id=file_id,
                name=name,
                target_folder_id=origins[0],
                duplicates_folder_id=duplicates_folder,
                other_targets=origins[1:],
            ))

    if moved_records and not with_origin:
        raise UndoNotPossible(
            "This log records moves but not where the files came from. It was "
            f"written before log version {LOG_VERSION}, and undo needs that "
            "information. Runs made from now on can be undone."
        )

    return steps, notes


@dataclass
class UndoResult:
    restored: int = 0
    skipped: int = 0
    failed: int = 0
    notes: Optional[List[str]] = None

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []


def run_undo(client, steps: List[UndoStep], dry_run: bool = True) -> UndoResult:
    """Put the files back, or say what that would mean.

    Every file is checked before it is touched: somebody may have sorted it by
    hand since the run, and a file that is no longer in the duplicates folder
    is not ours to move. A file that is gone entirely is not an error worth
    stopping for either.
    """
    result = UndoResult()

    for step in steps:
        parents = client.get_file_parents(step.file_id)

        if parents is None:
            result.skipped += 1
            result.notes.append(f"{step.name}: gone, skipped.")
            continue

        if step.duplicates_folder_id not in parents:
            result.skipped += 1
            result.notes.append(
                f"{step.name}: no longer in the duplicates folder, left alone."
            )
            continue

        if step.had_several_parents:
            result.notes.append(
                f"{step.name}: was in {len(step.other_targets) + 1} folders, "
                f"restoring to the first ({step.target_folder_id})."
            )

        if dry_run:
            result.restored += 1
            continue

        operation = client.move_file(step.file_id, step.target_folder_id)
        if operation.success:
            result.restored += 1
        else:
            result.failed += 1
            result.notes.append(f"{step.name}: {operation.error_message}")

    return result
