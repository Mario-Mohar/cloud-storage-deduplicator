"""Command-line interface for Cloud Storage Deduplicator (Google Drive & OneDrive)."""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import click

from .models import DriveFile, DuplicateGroup

from .auth import GoogleDriveAuth
from .dedup import DuplicateDetector
from .drive_client import GoogleDriveClient
from .utils import format_file_size, setup_logging, validate_folder_id

logger = logging.getLogger(__name__)

# Provider choices
PROVIDERS = ['google', 'onedrive']


def _list_folders(
    provider: str,
    credentials_file: str,
    token_file: str,
    client_id: Optional[str],
    oauth_scopes: Optional[list],
    account_type: str = "consumers"
) -> None:
    """List all folders with their IDs."""
    click.echo(f"Listing folders from {provider.title()}...")
    click.echo()

    if provider == 'google':
        from .auth import GoogleDriveAuth

        auth = GoogleDriveAuth(
            credentials_file=credentials_file,
            token_file=token_file,
            scopes=oauth_scopes
        )
        service = auth.get_drive_service()

        # List all folders
        folders = []
        page_token = None

        while True:
            response = service.files().list(
                q="mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="nextPageToken, files(id, name, parents)",
                pageSize=1000,
                pageToken=page_token
            ).execute()

            folders.extend(response.get('files', []))
            page_token = response.get('nextPageToken')
            if not page_token:
                break

        if not folders:
            click.echo("Keine Ordner gefunden.")
            return

        click.echo(f"{'Ordnername':<50} {'Folder-ID'}")
        click.echo("-" * 90)
        for folder in sorted(folders, key=lambda x: x['name'].lower()):
            name = folder['name'][:48] + '..' if len(folder['name']) > 50 else folder['name']
            click.echo(f"{name:<50} {folder['id']}")

    elif provider == 'onedrive':
        from .onedrive_auth import OneDriveAuth
        import requests

        auth = OneDriveAuth(
            client_id=client_id,
            token_file=token_file,
            scopes=oauth_scopes,
            account_type=account_type
        )
        access_token = auth.get_access_token()

        headers = {"Authorization": f"Bearer {access_token}"}
        folders = []

        def list_folder_recursive(folder_id: str, path: str = ""):
            """Recursively list all folders."""
            if folder_id == "root":
                url = "https://graph.microsoft.com/v1.0/me/drive/root/children"
            else:
                url = f"https://graph.microsoft.com/v1.0/me/drive/items/{folder_id}/children"

            while url:
                response = requests.get(url, headers=headers, params={"$filter": "folder ne null"})
                if response.status_code != 200:
                    break

                data = response.json()
                for item in data.get('value', []):
                    if 'folder' in item:
                        full_path = f"{path}/{item['name']}" if path else item['name']
                        folders.append({
                            'name': full_path,
                            'id': item['id']
                        })
                        # Recursively get subfolders
                        list_folder_recursive(item['id'], full_path)

                url = data.get('@odata.nextLink')

        click.echo("Scanne Ordnerstruktur...")
        list_folder_recursive("root")

        if not folders:
            click.echo("Keine Ordner gefunden.")
            return

        click.echo()
        click.echo(f"{'Ordnerpfad':<60} {'Folder-ID'}")
        click.echo("-" * 120)
        for folder in sorted(folders, key=lambda x: x['name'].lower()):
            name = folder['name'][:58] + '..' if len(folder['name']) > 60 else folder['name']
            click.echo(f"{name:<60} {folder['id']}")

    click.echo()
    click.echo(f"Gefunden: {len(folders)} Ordner")


def save_duplicate_groups(groups: List[DuplicateGroup], filepath: str) -> None:
    """Save duplicate groups to JSON file for later resume."""
    data = []
    for group in groups:
        group_data = {
            'comparison_key': group.comparison_key,
            'files': [
                {
                    'id': f.id,
                    'name': f.name,
                    'mime_type': f.mime_type,
                    'size': f.size,
                    'md5_checksum': f.md5_checksum,
                    'parents': f.parents,
                    'created_time': f.created_time,
                    'modified_time': f.modified_time
                }
                for f in group.files
            ],
            'kept_file_id': group.kept_file.id if group.kept_file else None
        }
        data.append(group_data)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("Saved %d duplicate groups to %s", len(groups), filepath)


def load_duplicate_groups(filepath: str) -> List[DuplicateGroup]:
    """Load duplicate groups from JSON file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    groups = []
    for group_data in data:
        files = [
            DriveFile(
                id=fd['id'],
                name=fd['name'],
                mime_type=fd['mime_type'],
                size=fd['size'],
                md5_checksum=fd['md5_checksum'],
                parents=fd['parents'],
                created_time=fd['created_time'],
                modified_time=fd['modified_time']
            )
            for fd in group_data['files']
        ]

        kept_file = None
        if group_data.get('kept_file_id'):
            kept_file = next((f for f in files if f.id == group_data['kept_file_id']), None)

        group = DuplicateGroup(
            files=files,
            comparison_key=group_data['comparison_key'],
            kept_file=kept_file
        )
        groups.append(group)

    logger.info("Loaded %d duplicate groups from %s", len(groups), filepath)
    return groups


def get_provider_client(
    provider: str,
    credentials_file: str,
    token_file: str,
    client_id: Optional[str],
    concurrency: int,
    oauth_scopes: Optional[list],
    account_type: str = "consumers"
):
    """Get authenticated client for the specified provider.

    Args:
        provider: Cloud provider ('google' or 'onedrive')
        credentials_file: Path to credentials file (Google only)
        token_file: Path to token file
        client_id: Azure AD client ID (OneDrive only)
        concurrency: Number of concurrent workers
        oauth_scopes: Optional custom OAuth scopes

    Returns:
        Tuple of (client, provider_name)
    """
    if provider == 'google':
        from .auth import GoogleDriveAuth
        from .drive_client import GoogleDriveClient

        auth = GoogleDriveAuth(
            credentials_file=credentials_file,
            token_file=token_file,
            scopes=oauth_scopes
        )
        service = auth.get_drive_service()
        client = GoogleDriveClient(service, max_workers=concurrency)
        return client, "Google Drive"

    elif provider == 'onedrive':
        from .onedrive_auth import OneDriveAuth
        from .onedrive_client import OneDriveClient

        auth = OneDriveAuth(
            client_id=client_id,
            token_file=token_file,
            scopes=oauth_scopes,
            account_type=account_type
        )
        access_token = auth.get_access_token()
        # Pass auth instance for automatic token refresh
        client = OneDriveClient(access_token, max_workers=concurrency, auth_instance=auth)
        return client, "OneDrive"

    else:
        raise click.ClickException(f"Unknown provider: {provider}")


@click.command()
@click.option(
    '--provider',
    type=click.Choice(PROVIDERS),
    default='google',
    help='Cloud storage provider (default: google)'
)
@click.option(
    '--list-folders',
    is_flag=True,
    default=False,
    help='List all folders with their IDs and exit'
)
@click.option(
    '--dry-run/--no-dry-run',
    default=True,
    help='Perform dry run without actually moving files (default: true)'
)
@click.option(
    '--move-folder-id',
    type=str,
    help='ID of folder to move duplicates to'
)
@click.option(
    '--move-to-folder',
    type=str,
    default=None,
    help='Name of folder to move duplicates to (will be created if needed, e.g. "Duplikate")'
)
@click.option(
    '--create-folder-if-missing/--no-create-folder',
    default=True,
    help='Create target folder if it does not exist (default: true)'
)
@click.option(
    '--min-size',
    type=int,
    default=0,
    help='Minimum file size in bytes to consider (default: 0)'
)
@click.option(
    '--concurrency',
    type=int,
    default=5,
    help='Maximum number of concurrent API operations (default: 5)'
)
@click.option(
    '--log-file',
    type=str,
    help='Path to save human-readable log file'
)
@click.option(
    '--json-log',
    type=str,
    help='Path to save machine-readable JSON log'
)
@click.option(
    '--save-scan',
    type=str,
    default=None,
    help='Save scan results to file for later resume'
)
@click.option(
    '--resume-scan',
    type=str,
    default=None,
    help='Resume from saved scan results file (skip scanning)'
)
@click.option(
    '--batch-size',
    type=int,
    default=0,
    help='Process duplicates in batches of N groups (0 = all at once)'
)
@click.option(
    '--use-fallback-hash/--no-fallback-hash',
    default=False,
    help='Use fallback hash for files without checksums (default: false)'
)
@click.option(
    '--scopes',
    type=str,
    help='Comma-separated list of OAuth scopes to override defaults'
)
@click.option(
    '--log-level',
    type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR']),
    default='INFO',
    help='Logging level (default: INFO)'
)
@click.option(
    '--credentials-file',
    type=str,
    default='credentials.json',
    help='Path to OAuth credentials file - Google only (default: credentials.json)'
)
@click.option(
    '--token-file',
    type=str,
    default=None,
    help='Path to store/load access token (default: token.json for Google, onedrive_token.json for OneDrive)'
)
@click.option(
    '--client-id',
    type=str,
    envvar='ONEDRIVE_CLIENT_ID',
    help='Azure AD Application (client) ID - OneDrive only (optional, uses built-in ID if not provided)'
)
@click.option(
    '--account-type',
    type=click.Choice(['common', 'personal', 'work']),
    default='personal',
    help='Microsoft account type - OneDrive only (default: personal)'
)
@click.option(
    '--confirm/--no-confirm',
    default=False,
    help='Require confirmation for non-reversible operations (default: false)'
)
def main(
    provider: str,
    list_folders: bool,
    dry_run: bool,
    move_folder_id: Optional[str],
    move_to_folder: Optional[str],
    create_folder_if_missing: bool,
    min_size: int,
    concurrency: int,
    log_file: Optional[str],
    json_log: Optional[str],
    save_scan: Optional[str],
    resume_scan: Optional[str],
    batch_size: int,
    use_fallback_hash: bool,
    scopes: Optional[str],
    log_level: str,
    credentials_file: str,
    token_file: Optional[str],
    client_id: Optional[str],
    account_type: str,
    confirm: bool
) -> None:
    """Cloud Storage Deduplicator - Find and manage duplicate files.

    This tool scans your cloud storage (Google Drive or OneDrive) for duplicate
    files and can optionally move them to a designated folder. By default, it
    runs in dry-run mode to show what would be done without making changes.

    Supported providers:
        - google: Google Drive (default)
        - onedrive: Microsoft OneDrive

    Examples:
        # Scan Google Drive (dry run)
        drive-dedup

        # Scan OneDrive (dry run)
        drive-dedup --provider onedrive --client-id YOUR_CLIENT_ID

        # Move duplicates to specific folder in Google Drive
        drive-dedup --move-folder-id=1ABC123xyz --no-dry-run

        # Move duplicates in OneDrive
        drive-dedup --provider onedrive --client-id YOUR_CLIENT_ID \\
            --move-folder-id=FOLDER_ID --no-dry-run

        # Only consider files larger than 1MB
        drive-dedup --min-size=1048576

        # Include files without native checksums in comparison
        drive-dedup --use-fallback-hash
    """
    # Set up logging
    setup_logging(log_level=log_level, log_file=log_file)

    # Set default token file based on provider
    if token_file is None:
        token_file = 'onedrive_token.json' if provider == 'onedrive' else 'token.json'

    try:
        # Parse custom scopes if provided
        oauth_scopes = None
        if scopes:
            oauth_scopes = [scope.strip() for scope in scopes.split(',')]

        # Map account type for OneDrive
        onedrive_account_type = {
            'personal': 'consumers',
            'work': 'organizations',
            'common': 'common'
        }.get(account_type, 'consumers')

        # OneDrive needs your own Azure app - report early, without a traceback
        if provider == 'onedrive' and not client_id:
            click.echo(
                "Error: --client-id is required for --provider onedrive.\n"
                "Register an Azure AD app (Public client/native, redirect URI "
                "http://localhost:8400) and pass its Application (client) ID.\n"
                "See the 'OneDrive setup' section of the README.",
                err=True
            )
            sys.exit(2)

        # Handle --list-folders option
        if list_folders:
            _list_folders(provider, credentials_file, token_file, client_id, oauth_scopes, onedrive_account_type)
            return

        # Validate inputs
        if not dry_run and not move_folder_id and not move_to_folder:
            click.echo("Error: --move-folder-id or --move-to-folder is required when not in dry-run mode", err=True)
            sys.exit(1)

        # Only validate folder ID format for Google Drive (OneDrive uses different format)
        if provider == 'google' and move_folder_id and not validate_folder_id(move_folder_id):
            click.echo(f"Error: Invalid folder ID format: {move_folder_id}", err=True)
            sys.exit(1)

        if min_size < 0:
            click.echo("Error: --min-size must be non-negative", err=True)
            sys.exit(1)

        if concurrency < 1 or concurrency > 20:
            click.echo("Error: --concurrency must be between 1 and 20", err=True)
            sys.exit(1)

        # Get provider client
        logger.info("Setting up %s authentication", provider)
        storage_client, provider_name = get_provider_client(
            provider=provider,
            credentials_file=credentials_file,
            token_file=token_file,
            client_id=client_id,
            concurrency=concurrency,
            oauth_scopes=oauth_scopes,
            account_type=onedrive_account_type
        )

        # Show configuration
        click.echo(f"Cloud Storage Deduplicator - {provider_name}")
        click.echo("=" * 50)
        click.echo(f"Provider: {provider_name}")
        click.echo(f"Mode: {'DRY RUN' if dry_run else 'LIVE MODE'}")
        click.echo(f"Minimum file size: {format_file_size(min_size)}")
        click.echo(f"Fallback hash for files without checksums: {use_fallback_hash}")
        click.echo(f"Concurrency: {concurrency}")
        if move_folder_id:
            click.echo(f"Target folder ID: {move_folder_id}")
        click.echo()

        # Confirm potentially destructive operations
        if not dry_run and confirm:
            if not click.confirm("Are you sure you want to move duplicate files?"):
                click.echo("Operation cancelled.")
                sys.exit(0)

        # Validate/create target folder if needed
        target_folder_id = move_folder_id

        # If --move-to-folder is specified, find or create that folder by name
        if not dry_run and move_to_folder:
            click.echo(f"Suche/erstelle Ordner '{move_to_folder}'...")
            target_folder_id = storage_client.find_or_create_folder(move_to_folder)
            click.echo(f"Verwende Ordner '{move_to_folder}' (ID: {target_folder_id})")
        elif not dry_run and target_folder_id:
            folder_info = storage_client.get_folder_info(target_folder_id)
            if not folder_info:
                if create_folder_if_missing:
                    click.echo(f"Creating target folder '_duplicates'...")
                    target_folder_id = storage_client.create_folder("_duplicates")
                    click.echo(f"Created folder with ID: {target_folder_id}")
                else:
                    click.echo(f"Error: Target folder {target_folder_id} not found", err=True)
                    sys.exit(1)
            else:
                click.echo(f"Using existing folder: {folder_info['name']}")

        # Set up duplicate detector
        detector = DuplicateDetector(
            drive_client=storage_client,
            min_file_size=min_size,
            use_fallback_hash=use_fallback_hash
        )

        # Progress tracking
        files_processed = 0
        last_report = 0

        def progress_callback(count: int, file) -> None:
            nonlocal files_processed, last_report
            files_processed = count
            if count - last_report >= 500:  # Report every 500 files
                click.echo(f"Processed {count:,} files...")
                last_report = count

        # Resume from saved scan or perform new scan
        if resume_scan:
            click.echo(f"Loading saved scan results from {resume_scan}...")
            try:
                duplicate_groups = load_duplicate_groups(resume_scan)
                click.echo(f"Loaded: {len(duplicate_groups)} duplicate groups")
                total_duplicates = sum(g.duplicate_count for g in duplicate_groups)
                click.echo(f"Total: {total_duplicates} duplicates to move")
            except Exception as e:
                click.echo(f"Failed to load: {e}", err=True)
                sys.exit(1)
        else:
            # Scan for duplicates
            click.echo(f"Scanning {provider_name} for duplicate files...")
            duplicate_groups = detector.scan_for_duplicates(progress_callback)

            stats = detector.get_stats()
            click.echo(f"Scan completed: {stats.total_files_scanned:,} files processed")
            click.echo(f"Found {stats.duplicate_groups_found:,} duplicate groups with {stats.total_duplicates:,} duplicates")
            click.echo(f"Total size of duplicates: {format_file_size(stats.total_size_duplicates)}")

            # Save scan results if requested
            if save_scan and duplicate_groups:
                save_duplicate_groups(duplicate_groups, save_scan)
                click.echo(f"Scan results saved to: {save_scan}")

        click.echo()

        if not duplicate_groups:
            click.echo(f"No duplicates found! Your {provider_name} is clean.")
            return

        # Process in batches if requested
        if batch_size > 0 and not dry_run:
            total_groups = len(duplicate_groups)
            total_moved = 0
            total_errors = 0
            batch_num = 0

            # Auto-save file for batch processing.
            # Deliberately NOT save_scan itself: that file is what the user asked
            # to keep ("save scan results for later resume"), while this one is
            # scratch state that gets truncated after every batch and removed at
            # the end. Sharing one path meant the saved scan was destroyed.
            batch_save_file = (
                str(Path(save_scan).with_suffix(".progress.json")) if save_scan
                else "batch_progress.json"
            )

            while duplicate_groups:
                batch_num += 1
                current_batch = duplicate_groups[:batch_size]
                remaining = duplicate_groups[batch_size:]

                click.echo()
                click.echo(f"=== Batch {batch_num}: processing {len(current_batch)} groups ({len(remaining)} remaining) ===")

                # Move this batch
                moved_before = detector.stats.files_moved
                errors_before = detector.stats.errors
                log_entries = detector.move_duplicates(
                    duplicate_groups=current_batch,
                    target_folder_id=target_folder_id or "",
                    dry_run=False
                )

                # Count results off the detector's own counters. log_entries
                # holds one entry per GROUP, so counting them reported groups
                # while calling them files -- 100 files in 30 groups came out as
                # "30 files moved".
                batch_moved = detector.stats.files_moved - moved_before
                batch_errors = detector.stats.errors - errors_before
                moved_before = detector.stats.files_moved
                errors_before = detector.stats.errors
                total_moved += batch_moved
                total_errors += batch_errors

                click.echo(f"Batch {batch_num} done: {batch_moved} moved, {batch_errors} errors")

                # Save remaining groups for resume
                duplicate_groups = remaining
                if remaining:
                    save_duplicate_groups(remaining, batch_save_file)
                    click.echo(f"Progress saved to: {batch_save_file}")
                    click.echo(f"Zum Fortsetzen: --resume-scan {batch_save_file}")

            click.echo()
            click.echo(f"=== DONE: {total_moved} files moved, {total_errors} errors ===")

            # Remove progress file if complete
            if Path(batch_save_file).exists() and not remaining:
                Path(batch_save_file).unlink()
                click.echo("Progress file removed (all batches complete)")
            return

        # Normal processing (all at once)
        log_entries = detector.move_duplicates(
            duplicate_groups=duplicate_groups,
            target_folder_id=target_folder_id or "",
            dry_run=dry_run
        )

        # Generate and display report
        report = detector.generate_report(duplicate_groups, log_entries)

        if log_file:
            detector.save_report(report, log_file)
            click.echo(f"Report saved to: {log_file}")

        if json_log:
            detector.save_json_log(log_entries, json_log)
            click.echo(f"JSON log saved to: {json_log}")

        # Show summary
        click.echo()
        click.echo("SUMMARY:")
        click.echo(f"  Provider: {provider_name}")
        click.echo(f"  Files scanned: {stats.total_files_scanned:,}")
        click.echo(f"  Duplicate groups: {stats.duplicate_groups_found:,}")
        click.echo(f"  Total duplicates: {stats.total_duplicates:,}")
        click.echo(f"  Files moved: {stats.files_moved:,}")
        if stats.errors > 0:
            click.echo(f"  Errors: {stats.errors:,}")

        if dry_run and duplicate_groups:
            click.echo()
            click.echo("This was a dry run. Use --no-dry-run to actually move files.")
            click.echo("   Make sure to specify --move-folder-id for the target folder.")

    except KeyboardInterrupt:
        click.echo("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error("Unexpected error: %s", e, exc_info=True)
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
