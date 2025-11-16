import os
import glob
from datetime import datetime
import sys
import argparse
import hashlib

# --- Configuration ---

# The prefix used to identify the "Dated" archive folders (the ones containing duplicates)
ARCHIVE_PREFIX = "Photos from "
# The name of the log file to be created inside each archive folder
FOLDER_LOG_FILENAME = "duplicates_log.txt"
# Chunk size for hashing large files (e.g., videos)
HASH_CHUNK_SIZE = 65536

# --- Helper Function for Hashing ---


def hash_file(filepath):
    """Computes the SHA256 hash of a file efficiently by reading it in chunks."""
    try:
        hasher = hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(HASH_CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        print(f"WARNING: Could not hash file {filepath}: {e}")
        return None


# --- Core Logic ---


def find_keeper_files(root_dir):
    """
    Traverses the directory structure to identify the canonical 'keeper' file
    for each unique file HASH, prioritizing files outside of ARCHIVE_PREFIX folders.

    Returns a dictionary: {file_hash: full_path_to_keeper}
    """
    keeper_map = {}
    total_files = 0
    print("Step 1: Identifying keeper files (prioritizing albums via file HASHING)...")

    # Iterate over all files recursively
    for root, _, files in os.walk(root_dir):
        # Check if the current directory is an "archive" folder (e.g., "Photos from 2025")
        is_archive_folder = os.path.basename(root).startswith(ARCHIVE_PREFIX)

        for filename in files:
            # Skip JSON files for the keeper map
            if filename.lower().endswith(".json"):
                continue

            total_files += 1
            full_path = os.path.join(root, filename)

            # --- Progress Logging Update (Every 500 files) ---
            if (total_files % 500) == 0:
                # Use \r to return to the start of the line, creating a single-line progress indicator
                print(
                    f"Status: Hashing file {total_files}...",
                    end="\r",
                    file=sys.stdout,
                    flush=True,
                )

            file_hash = hash_file(full_path)
            if file_hash is None:
                continue

            # If the hash is already known, check priority
            if file_hash in keeper_map:
                existing_keeper_path = keeper_map[file_hash]

                # Check if the existing keeper is in an archive folder
                is_existing_in_archive = os.path.basename(
                    os.path.dirname(existing_keeper_path)
                ).startswith(ARCHIVE_PREFIX)

                # If the new file is NOT in an archive folder (i.e., it's in an album),
                # AND the existing keeper IS in an archive folder, update the keeper to the new (album) path.
                if not is_archive_folder and is_existing_in_archive:
                    keeper_map[file_hash] = full_path
                # If both are in albums, the first one found remains the keeper (hash ensures they are identical)
                # If both are in archives, the first one found remains the keeper (no need to change)

            # If the hash is new, add it.
            else:
                keeper_map[file_hash] = full_path

    # Clear the progress line after completion and print the final counts
    print(" " * 50, end="\r", file=sys.stdout, flush=True)
    print(f"\nFinished hashing {total_files} files.")
    print(f"Found {len(keeper_map)} unique files (by hash).")
    return keeper_map


def process_duplicates(root_dir, keeper_map, execution_mode):
    """
    Traverses the directory, finds duplicate files in archive folders using HASH,
    and processes them based on the execution_mode.
    """
    total_processed = 0
    total_replaced = 0

    print(f"Step 2: Processing archive folders in mode: {execution_mode.upper()}...")

    for root, _, files in os.walk(root_dir):
        # Only process files inside folders starting with the archive prefix
        if not os.path.basename(root).startswith(ARCHIVE_PREFIX):
            continue

        archive_folder_name = os.path.basename(root)

        # Initialize a log list for this specific folder
        folder_log_entries = [
            f"--- Log for Archive Folder: {archive_folder_name} ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---\n"
        ]
        folder_log_entries.append(f"Execution Mode: {execution_mode.upper()}\n")
        folder_log_entries.append(
            f"Keeper priority: Files with matching HASH outside this archive folder.\n"
        )

        current_folder_replacements = 0

        for filename in files:
            total_processed += 1
            full_path_duplicate = os.path.join(root, filename)

            # 1. Skip JSON files
            if filename.lower().endswith(".json"):
                continue

            json_path_duplicate = full_path_duplicate + ".json"

            # 2. Hash the file in the archive folder
            duplicate_hash = hash_file(full_path_duplicate)

            if duplicate_hash in keeper_map:
                keeper_path = keeper_map[duplicate_hash]

                # If the keeper is the duplicate itself, skip (safety check, should rarely happen)
                if keeper_path == full_path_duplicate:
                    folder_log_entries.append(
                        f"KEEP (Archive Keeper): {full_path_duplicate}\n"
                    )
                    continue

                # --- This file is a TRUE DUPLICATE and will be processed based on mode ---

                acted_on_media = False

                # A. Delete the original duplicate media file
                if execution_mode in ["symlink", "delete-only"]:
                    try:
                        os.remove(full_path_duplicate)
                        folder_log_entries.append(
                            f"DELETE (Duplicate Media): {os.path.basename(full_path_duplicate)}\n"
                        )
                        acted_on_media = True
                    except Exception as e:
                        error_msg = f"ERROR deleting file {full_path_duplicate}: {e}. Skipping symlink/log."
                        folder_log_entries.append(error_msg + "\n")
                        print(f"Error in {archive_folder_name}: {error_msg}")
                        continue
                else:  # log-only mode
                    folder_log_entries.append(
                        f"LOG ONLY (Would Delete Media): {os.path.basename(full_path_duplicate)}\n"
                    )

                # B. Delete the associated JSON file
                if os.path.exists(json_path_duplicate):
                    if execution_mode in ["symlink", "delete-only"]:
                        try:
                            os.remove(json_path_duplicate)
                            folder_log_entries.append(
                                f"DELETE (Duplicate JSON): {os.path.basename(json_path_duplicate)}\n"
                            )
                        except Exception as e:
                            error_msg = (
                                f"ERROR deleting JSON {json_path_duplicate}: {e}"
                            )
                            folder_log_entries.append(error_msg + "\n")
                            print(f"Error in {archive_folder_name}: {error_msg}")
                    else:  # log-only mode
                        folder_log_entries.append(
                            f"LOG ONLY (Would Delete JSON): {os.path.basename(json_path_duplicate)}\n"
                        )

                # C. Create the symbolic link (Only in 'symlink' mode)
                if execution_mode == "symlink" and acted_on_media:
                    try:
                        # We use the absolute path approach here as it's safer across different OS/WSL configurations
                        os.symlink(keeper_path, full_path_duplicate)

                        # Log the full action including the keeper path
                        folder_log_entries.append(
                            f"LINKED (Replaced Duplicate): {os.path.basename(full_path_duplicate)} -> {keeper_path}\n"
                        )
                        total_replaced += 1
                        current_folder_replacements += 1
                    except NotImplementedError:
                        error_msg = "FATAL ERROR: Symbolic links not supported on this OS. Stopping."
                        folder_log_entries.append(error_msg + "\n")
                        print(error_msg)
                        sys.exit(1)
                    except OSError as e:
                        # Windows specific error if not run as Administrator
                        if "operation not permitted" in str(e).lower():
                            error_msg = "FATAL ERROR: Symlink failed. Windows requires Administrator privileges to create symlinks. Stopping."
                            folder_log_entries.append(error_msg + "\n")
                            print(error_msg)
                            sys.exit(1)
                        error_msg = (
                            f"ERROR creating symlink at {full_path_duplicate}: {e}"
                        )
                        folder_log_entries.append(error_msg + "\n")
                        print(f"Error in {archive_folder_name}: {error_msg}")
                elif execution_mode == "delete-only" and acted_on_media:
                    folder_log_entries.append(
                        f"DELETED (No Symlink Created): {os.path.basename(full_path_duplicate)} (Keeper: {keeper_path})\n"
                    )
                    total_replaced += 1
                    current_folder_replacements += 1  # Count as acted upon
                elif execution_mode == "log-only":
                    folder_log_entries.append(
                        f"LOG ONLY (Would Symlink): {os.path.basename(full_path_duplicate)} -> {keeper_path}\n"
                    )

            else:
                # File is unique (by hash)
                folder_log_entries.append(
                    f"KEEP (Unique Archive File): {full_path_duplicate}\n"
                )

        # --- Write Folder Log ---
        # Log is created if duplicates were acted upon, OR if we are in log-only mode
        if current_folder_replacements > 0 or execution_mode == "log-only":
            folder_log_path = os.path.join(root, FOLDER_LOG_FILENAME)
            try:
                with open(folder_log_path, "w", encoding="utf-8") as log_file:
                    log_file.writelines(folder_log_entries)
                print(
                    f"   -> {execution_mode.upper()} completed. Log saved to {FOLDER_LOG_FILENAME} inside the folder."
                )
            except Exception as e:
                print(
                    f"   -> ERROR: Could not write log file in {archive_folder_name}: {e}"
                )
        else:
            print(f"   -> No duplicates acted on in this folder. Log skipped.")

    print(f"\n--- Processing Complete ---")
    print(f"Total files in archive folders processed: {total_processed}")
    print(f"Total files acted upon (delete/symlink): {total_replaced}")
    print(f"Final Mode: {execution_mode.upper()}")


# --- Main execution ---


def main():
    parser = argparse.ArgumentParser(
        description="Deletes true duplicate Google Photos files (verified by SHA-256 hash) from 'Photos from <year>' folders and processes them based on the selected mode.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "takeout_path",
        type=str,
        help=(
            "The full path to the root of the extracted Google Photos Takeout directory.\n"
            "Example for Windows/WSL: /mnt/e/pictures/google_photos/Takeout/Google Photos"
        ),
    )

    parser.add_argument(
        "--mode",
        choices=["log-only", "symlink", "delete-only"],
        required=True,
        help=(
            "Execution mode:\n"
            "  log-only: Identifies duplicates and logs actions, but makes no changes to the file system (Safe Preview).\n"
            "  symlink: Deletes duplicates and replaces them with symbolic links to the keeper file (Recommended for space saving).\n"
            "  delete-only: Deletes duplicates and their JSON files without creating symlinks (Maximum space saved, structure broken)."
        ),
    )

    args = parser.parse_args()
    TAKEout_DIRECTORY = args.takeout_path
    EXECUTION_MODE = args.mode

    # Safety check
    if not os.path.isdir(TAKEout_DIRECTORY):
        print(f"Directory not found: {TAKEout_DIRECTORY}")
        print("Please ensure the path is correct and the folder exists.")
        sys.exit(1)

    print("--- Starting Google Photos Duplicate Removal (HASH-BASED) ---")
    print("!!! NOTE: This process will be slower due to hashing all media files. !!!")
    print(f"Target Directory: {TAKEout_DIRECTORY}")
    print(f"Execution Mode: {EXECUTION_MODE.upper()}")

    if EXECUTION_MODE != "log-only":
        print(
            "\n!!! WARNING: This script will perform file system modifications (DELETION/SYMLINKING). ENSURE YOU HAVE A BACKUP !!!"
        )
        if EXECUTION_MODE == "symlink":
            print(
                "!!! WARNING: SYM-LINKING requires Administrator privileges on Windows/WSL. !!!"
            )
        input(
            f"Press Enter to continue (Mode: {EXECUTION_MODE}) or Ctrl+C to cancel..."
        )
    else:
        print("\nMode is LOG-ONLY: No files will be modified or deleted.")
        input("Press Enter to begin logging, or Ctrl+C to cancel...")

    # Step 1: Build the keeper map
    keeper_map = find_keeper_files(TAKEout_DIRECTORY)

    # Step 2: Process all duplicate folders
    process_duplicates(TAKEout_DIRECTORY, keeper_map, EXECUTION_MODE)


if __name__ == "__main__":
    main()
