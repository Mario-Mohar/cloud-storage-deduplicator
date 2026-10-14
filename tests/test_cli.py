"""Tests for the command-line entry point, with a fake storage client."""

from unittest.mock import patch

from click.testing import CliRunner

from drive_dedup import cli
from drive_dedup.models import DriveFile, OperationResult
from drive_dedup.undo import plan_undo, read_log


class FakeStorageClient:
    """Two duplicate groups, and moves that always succeed."""

    def __init__(self):
        self.moves = []

    def list_all_files(self, min_size=0, progress_callback=None):
        files = []
        for n in range(2):
            for copy in ("a", "b"):
                files.append(DriveFile(
                    id=f"{copy}{n}", name=f"file{n}.pdf",
                    mime_type="application/pdf", size=100,
                    md5_checksum=f"sum{n}", parents=[f"SRC{n}"],
                    created_time=f"2026-01-0{1 if copy == 'a' else 2}T00:00:00Z"
                ))
        for index, drive_file in enumerate(files, start=1):
            if progress_callback:
                progress_callback(index, drive_file)
            yield drive_file

    def get_folder_info(self, folder_id):
        return {'id': folder_id, 'name': '_duplicates'}

    def move_files_batch(self, file_ids, target_folder_id):
        self.moves.extend(file_ids)
        return [
            OperationResult(success=True, file_id=f, operation="move")
            for f in file_ids
        ]


def run_cli(args, client):
    with patch.object(cli, 'get_provider_client',
                      return_value=(client, "Google Drive")), \
            patch.object(cli, 'setup_logging'):
        return CliRunner().invoke(cli.main, args)


class TestBatchedRunsCanBeUndone:
    """Issue #11: --batch-size must still write --json-log and the report."""

    def test_batched_run_writes_json_log_and_report(self, tmp_path, monkeypatch):
        # Batch progress is kept in the working directory between batches.
        monkeypatch.chdir(tmp_path)
        client = FakeStorageClient()
        log_path = tmp_path / "run.jsonl"
        report_path = tmp_path / "report.txt"

        result = run_cli([
            '--no-dry-run', '--move-folder-id', 'DUPSFOLDER123',
            '--batch-size', '1',
            '--json-log', str(log_path), '--log-file', str(report_path),
        ], client)

        assert result.exit_code == 0, result.output
        assert "2 files moved" in result.output
        steps, _ = plan_undo(list(read_log(str(log_path))))
        assert sorted((s.file_id, s.target_folder_id) for s in steps) == [
            ("b0", "SRC0"), ("b1", "SRC1")
        ]
        assert "Files moved: 2" in report_path.read_text()
