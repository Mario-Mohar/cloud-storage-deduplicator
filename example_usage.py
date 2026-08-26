#!/usr/bin/env python3
"""
Example usage script demonstrating how to use the Google Drive Deduplicator
programmatically rather than through the CLI.
"""

import logging
from drive_dedup.auth import GoogleDriveAuth
from drive_dedup.drive_client import GoogleDriveClient
from drive_dedup.dedup import DuplicateDetector

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def main():
    """Example of programmatic usage."""

    # Step 1: Set up authentication
    auth = GoogleDriveAuth(
        credentials_file="credentials.json",
        token_file="token.json"
    )

    # Step 2: Get authenticated service
    service = auth.get_drive_service()
    drive_client = GoogleDriveClient(service, max_workers=5)

    # Step 3: Set up duplicate detector
    detector = DuplicateDetector(
        drive_client=drive_client,
        min_file_size=1024,  # Only files >= 1KB
        use_fallback_hash=False  # Don't use fallback for Google Workspace files
    )

    # Step 4: Scan for duplicates
    print("Scanning Google Drive for duplicates...")

    def progress_callback(count, file):
        if count % 100 == 0:  # Print every 100 files
            print(f"Processed {count} files...")

    duplicate_groups = detector.scan_for_duplicates(progress_callback)

    # Step 5: Display results
    stats = detector.get_stats()
    print(f"\nScan Results:")
    print(f"- Total files scanned: {stats.total_files_scanned}")
    print(f"- Files with MD5: {stats.files_with_md5}")
    print(f"- Google Workspace files: {stats.google_workspace_files}")
    print(f"- Duplicate groups found: {stats.duplicate_groups_found}")
    print(f"- Total duplicates: {stats.total_duplicates}")

    if duplicate_groups:
        print(f"\nDuplicate Groups:")
        for i, group in enumerate(duplicate_groups[:5], 1):  # Show first 5 groups
            print(f"Group {i}: {group.duplicate_count} duplicates of '{group.kept_file.name}'")
            for dup_file in group.duplicate_files:
                print(f"  - {dup_file.name} ({dup_file.id})")

        if len(duplicate_groups) > 5:
            print(f"... and {len(duplicate_groups) - 5} more groups")

        # Step 6: Perform dry-run operation
        print(f"\nPerforming dry-run to see what would be moved...")
        log_entries = detector.move_duplicates(
            duplicate_groups=duplicate_groups,
            target_folder_id="dummy_folder_id",  # Won't be used in dry-run
            dry_run=True
        )

        # Step 7: Generate and save report
        report = detector.generate_report(duplicate_groups, log_entries)

        # Save reports
        detector.save_report(report, "duplicate_report.txt")
        detector.save_json_log(log_entries, "duplicate_operations.jsonl")

        print(f"Reports saved:")
        print(f"- Human-readable: duplicate_report.txt")
        print(f"- Machine-readable: duplicate_operations.jsonl")

    else:
        print(f"\n🎉 No duplicates found! Your Google Drive is clean.")

if __name__ == "__main__":
    main()