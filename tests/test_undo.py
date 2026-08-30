"""Tests for reversing a run."""

import json

import pytest

from drive_dedup.models import DriveFile, DuplicateGroup, LogEntry
from drive_dedup.undo import (
    UndoNotPossible,
    plan_undo,
    read_log,
    run_undo,
)


class FakeClient:
    """Just enough of a storage client to plan and perform an undo."""

    def __init__(self, parents_by_id, failing=()):
        self._parents = parents_by_id
        self._failing = set(failing)
        self.moves = []

    def get_file_parents(self, file_id):
        return self._parents.get(file_id)

    def move_file(self, file_id, target_folder_id):
        class Result:
            def __init__(self, success, message=None):
                self.success = success
                self.error_message = message

        if file_id in self._failing:
            return Result(False, "permission denied")
        self.moves.append((file_id, target_folder_id))
        return Result(True)


def moved_record(**overrides):
    record = {
        "action": "moved",
        "move_target_folder_id": "DUPS",
        "moved": [
            {"id": "f1", "name": "urlaub.jpg", "from": ["FOTOS"]},
            {"id": "f2", "name": "rechnung.pdf", "from": ["BELEGE"]},
        ],
    }
    record.update(overrides)
    return record


class TestPlanUndo:
    def test_builds_one_step_per_moved_file(self):
        steps, notes = plan_undo([moved_record()])
        assert [s.file_id for s in steps] == ["f1", "f2"]
        assert steps[0].target_folder_id == "FOTOS"
        assert steps[0].duplicates_folder_id == "DUPS"
        assert notes == []

    def test_ignores_dry_run_records(self):
        # A dry run moved nothing, so there is nothing to put back.
        steps, _ = plan_undo([moved_record(action="dry-run")])
        assert steps == []

    def test_old_logs_are_refused_with_a_reason(self):
        # Version 1 recorded which files moved, never where from. Saying so is
        # the whole point -- failing obscurely would be worse than not trying.
        old = {"action": "moved", "move_target_folder_id": "DUPS",
               "duplicate_ids": ["f1", "f2"]}
        with pytest.raises(UndoNotPossible) as excinfo:
            plan_undo([old])
        assert "where the files came from" in str(excinfo.value)

    def test_empty_log_is_not_an_error(self):
        steps, notes = plan_undo([])
        assert steps == [] and notes == []

    def test_file_without_a_recorded_origin_is_noted_not_dropped_silently(self):
        record = moved_record(moved=[{"id": "f9", "name": "waise.txt", "from": []}])
        steps, notes = plan_undo([record])
        assert steps == []
        assert any("waise.txt" in n for n in notes)

    def test_several_parents_are_kept_for_the_report(self):
        record = moved_record(
            moved=[{"id": "f1", "name": "a.jpg", "from": ["FOTOS", "ALBEN"]}]
        )
        steps, _ = plan_undo([record])
        assert steps[0].target_folder_id == "FOTOS"
        assert steps[0].other_targets == ["ALBEN"]
        assert steps[0].had_several_parents


class TestRunUndo:
    def test_moves_files_back(self):
        steps, _ = plan_undo([moved_record()])
        client = FakeClient({"f1": ["DUPS"], "f2": ["DUPS"]})
        result = run_undo(client, steps, dry_run=False)
        assert result.restored == 2
        assert client.moves == [("f1", "FOTOS"), ("f2", "BELEGE")]

    def test_dry_run_touches_nothing(self):
        steps, _ = plan_undo([moved_record()])
        client = FakeClient({"f1": ["DUPS"], "f2": ["DUPS"]})
        result = run_undo(client, steps, dry_run=True)
        assert result.restored == 2
        assert client.moves == []

    def test_file_sorted_by_hand_since_is_left_alone(self):
        # Somebody moved it out of the duplicates folder themselves. It is not
        # ours to move back.
        steps, _ = plan_undo([moved_record()])
        client = FakeClient({"f1": ["WOANDERS"], "f2": ["DUPS"]})
        result = run_undo(client, steps, dry_run=False)
        assert result.restored == 1
        assert result.skipped == 1
        assert client.moves == [("f2", "BELEGE")]
        assert any("no longer in the duplicates folder" in n for n in result.notes)

    def test_missing_file_is_skipped_not_fatal(self):
        steps, _ = plan_undo([moved_record()])
        client = FakeClient({"f1": None, "f2": ["DUPS"]})
        result = run_undo(client, steps, dry_run=False)
        assert result.skipped == 1
        assert result.restored == 1

    def test_a_failed_move_is_counted_and_named(self):
        steps, _ = plan_undo([moved_record()])
        client = FakeClient({"f1": ["DUPS"], "f2": ["DUPS"]}, failing={"f1"})
        result = run_undo(client, steps, dry_run=False)
        assert result.failed == 1
        assert result.restored == 1
        assert any("permission denied" in n for n in result.notes)


class TestReadLog:
    def test_skips_blank_and_broken_lines(self, tmp_path):
        path = tmp_path / "run.jsonl"
        path.write_text(
            json.dumps(moved_record()) + "\n"
            "\n"
            "{kaputt\n"
            + json.dumps(moved_record()) + "\n",
            encoding="utf-8"
        )
        assert len(list(read_log(str(path)))) == 2


class TestLogEntryRecordsOrigin:
    def test_moved_carries_the_source_parents(self):
        kept = DriveFile(id="k", name="a.jpg", mime_type="image/jpeg",
                         md5_checksum="x", parents=["FOTOS"],
                         created_time="2020-01-01T00:00:00.000Z")
        dup = DriveFile(id="d", name="a.jpg", mime_type="image/jpeg",
                        md5_checksum="x", parents=["KOPIEN"],
                        created_time="2021-01-01T00:00:00.000Z")
        group = DuplicateGroup(files=[kept, dup], comparison_key="x")

        entry = LogEntry.from_duplicate_group(group, "moved", target_folder_id="DUPS")

        assert entry.duplicate_ids == ["d"]          # weiterhin da
        assert entry.moved == [{"id": "d", "name": "a.jpg", "from": ["KOPIEN"]}]
        assert entry.version == 2

    def test_a_log_written_now_can_be_undone(self):
        # Der Beweis, dass die beiden Hälften zusammenpassen.
        kept = DriveFile(id="k", name="a.jpg", mime_type="image/jpeg",
                         md5_checksum="x", parents=["FOTOS"],
                         created_time="2020-01-01T00:00:00.000Z")
        dup = DriveFile(id="d", name="a.jpg", mime_type="image/jpeg",
                        md5_checksum="x", parents=["KOPIEN"],
                        created_time="2021-01-01T00:00:00.000Z")
        group = DuplicateGroup(files=[kept, dup], comparison_key="x")
        entry = LogEntry.from_duplicate_group(group, "moved", target_folder_id="DUPS")

        from dataclasses import asdict
        steps, _ = plan_undo([asdict(entry)])
        client = FakeClient({"d": ["DUPS"]})
        result = run_undo(client, steps, dry_run=False)

        assert result.restored == 1
        assert client.moves == [("d", "KOPIEN")]
