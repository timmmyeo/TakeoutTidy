import os
import json
import subprocess
import glob
from datetime import datetime
import argparse
import sys

# --- Configuration ---

# List of file extensions that should be processed as media files.
# This prevents log files (.txt) and other non-media files from being targeted.
MEDIA_EXTENSIONS = [
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".mp4",
    ".mov",
    ".avi",
    ".webp",
    ".heic",
    ".thm",
    ".tiff",
    ".webm",
    ".3gp",  # Added support for 3GP video format
]


# --- Helper Functions ---


def run_exiftool(args):
    """Helper function to run the exiftool command-line utility."""
    try:
        # We suppress standard output here, ExifTool prints a lot of info
        subprocess.run(
            ["exiftool", *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as e:
        print(f"ExifTool error on arguments {args}: {e.stderr.decode().strip()}")
        return False
    except FileNotFoundError:
        print(
            "Error: exiftool not found. Make sure it is installed and in your system's PATH."
        )
        # Exit if ExifTool is critical and not found
        sys.exit(1)
    return True


def update_file_mtime(media_path, creation_time_epoch):
    """Updates the file system timestamp (mtime) using the epoch time."""
    try:
        # Update both access and modification times
        os.utime(media_path, (creation_time_epoch, creation_time_epoch))
        print(f"-> Updated filesystem time for {os.path.basename(media_path)}")

    except (
        OSError
    ) as e:  # Refined: Catches OS-level errors (permissions, file not found, etc.)
        print(
            f"-> Could not update filesystem time for {os.path.basename(media_path)} (OSError): {e}"
        )
    except Exception as e:
        print(
            f"-> Could not update filesystem time for {os.path.basename(media_path)} (Generic Error): {e}"
        )


def load_json_metadata_map(takeout_path):
    """
    Reads all JSON files into an in-memory map keyed by the 'title' field.
    Includes validation to skip non-media metadata JSON files.

    Returns a dictionary: {filename: {"data": json_object, "json_path": path}}
    """
    metadata_map = {}
    json_files = glob.glob(os.path.join(takeout_path, "**/*.json"), recursive=True)
    json_files_len = len(json_files)
    print(f"Found {json_files_len} potential JSON files...")

    for i, json_path in enumerate(json_files):
        # Progress logging
        if (i % 500) == 0 and i > 0:
            print(
                f"({i}/{json_files_len}) Processing JSON files...",
                end="\r",
                file=sys.stdout,
                flush=True,
            )

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

                # --- Robustness Check: Ensure this is a media metadata JSON ---
                title = data.get("title")
                has_creation_time = data.get("creationTime")

                # Check for two essential keys to filter out non-media files (e.g., album titles, general metadata)
                if title and has_creation_time:
                    # Store the JSON data and the path to the JSON file itself
                    metadata_map[title] = {"data": data, "json_path": json_path}
                else:
                    # Skip files like 'metadata.json' or 'user-generated-memory-titles.json'
                    pass

        except json.JSONDecodeError:
            print(f"\nCould not decode JSON file: {json_path}")
        except Exception as e:
            print(f"\nError processing JSON file {json_path}: {e}")

    # Clear progress line
    print(" " * 50, end="\r", file=sys.stdout, flush=True)
    print(
        f"Successfully mapped {len(metadata_map)} unique metadata entries by 'title' and structure."
    )
    return metadata_map


def process_files_with_map(takeout_path, metadata_map):
    """
    Iterates over all media files and attempts to match them using the metadata map.
    Includes clean exit handling for KeyboardInterrupt.
    """
    count_merged = 0
    failures = []  # Paths of media that failed to process with exiftool
    untouched = []  # Paths of media that did not have metadata overwritten
    interrupted = False

    # Filter media files based on the defined extensions
    media_files = [
        f
        for f in glob.glob(os.path.join(takeout_path, "**/*.*"), recursive=True)
        if os.path.splitext(f.lower())[1] in MEDIA_EXTENSIONS
    ]

    media_files_len = len(media_files)
    print(f"Found {media_files_len} media files to check...")

    try:
        for i, media_path in enumerate(media_files):
            if (i % 500) == 0 and i > 0:
                print(
                    f"({i}/{media_files_len}) Processing media files...",
                    end="\r",
                    file=sys.stdout,
                    flush=True,
                )

            filename = os.path.basename(media_path)

            # Check if this filename is a key in our metadata map
            if filename in metadata_map:
                match = metadata_map[filename]
                json_path = match["json_path"]
                json_data = match["data"]

                print(f"\nMatch found for '{filename}'. Merging metadata...")

                # Use ExifTool to write metadata from the JSON into the media file
                success = run_exiftool(
                    [
                        "-tagsFromFile",
                        json_path,
                        "-overwrite_original",  # WARNING: Modifies the file in place
                        media_path,
                    ]
                )
                if not success:
                    failures.append(media_path)

                # Update the file system modification time as well
                try:
                    creation_time_epoch = int(json_data["creationTime"]["timestamp"])
                    update_file_mtime(media_path, creation_time_epoch)
                    count_merged += 1
                except (KeyError, ValueError, TypeError):
                    print(
                        f"-> WARNING: JSON data missing/invalid creationTime for {filename}. Skipping utime update."
                    )
                    failures.append(media_path)

            # This else block is for files where the metadata was probably already embedded
            else:
                untouched.append(media_path)

    except KeyboardInterrupt:
        interrupted = True
        print(
            "\n\nProcessing interrupted by user (Ctrl+C). Cleaning up and logging partial results..."
        )

    finally:
        # Clear progress line
        print(" " * 50, end="\r", file=sys.stdout, flush=True)

        ## 📝 Logging Failures
        if failures:
            failure_log_path = "./exiftool_merge_failures.txt"
            with open(failure_log_path, "w", encoding="utf-8") as f:
                for path in failures:
                    f.write(path + "\n")
            print(
                f"**LOGGED:** {len(failures)} files failed the ExifTool merge. See **{failure_log_path}** for paths."
            )

        ## 📝 Logging Untouched Files
        if untouched:
            untouched_log_path = "./metadata_untouched_files.txt"
            with open(untouched_log_path, "w", encoding="utf-8") as f:
                for path in untouched:
                    f.write(path + "\n")
            print(
                f"**LOGGED:** {len(untouched)} files were skipped (no JSON found). See **{untouched_log_path}** for paths."
            )

        status = "Interrupted" if interrupted else "Complete"
        print(f"\nProcessing {status}. Merged metadata for {count_merged} files.")


# --- Main execution ---


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Merges metadata from Google Takeout JSON files into the corresponding media files (images/videos). "
            "Requires ExifTool to be installed and accessible in the system's PATH."
        ),
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

    args = parser.parse_args()
    TAKEout_DIRECTORY = args.takeout_path

    # Safety check
    if not os.path.isdir(TAKEout_DIRECTORY):
        print(f"Directory not found: {TAKEout_DIRECTORY}")
        sys.exit(1)

    print("--- Starting Google Photos Metadata Merge ---")
    print(f"Target Directory: {TAKEout_DIRECTORY}")
    print(
        "\n!!! WARNING: This script uses ExifTool to modify files in place ('-overwrite_original')."
    )
    print("!!! ENSURE YOU HAVE A BACKUP COPY OF YOUR DATA BEFORE PROCEEDING !!!")

    try:
        input("Press Enter to continue or Ctrl+C to cancel...")
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(0)

    # Step 1: Build the map from all JSON files
    metadata_map = load_json_metadata_map(TAKEout_DIRECTORY)

    # Step 2: Iterate over all media files and use the map to merge data
    process_files_with_map(TAKEout_DIRECTORY, metadata_map)


if __name__ == "__main__":
    # Wrap main in a try-except to catch any interrupts that bubble up from other places
    try:
        main()
    except KeyboardInterrupt:
        # If interrupted outside of the dedicated block (e.g., in load_json_metadata_map), just exit cleanly
        print("\nProgram exit due to user interruption (Ctrl+C).")
        sys.exit(0)
