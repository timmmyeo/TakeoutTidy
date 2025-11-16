import os
import json
import subprocess
import glob
from datetime import datetime

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
        exit(1)
    return True


def update_file_mtime(media_path, creation_time_epoch):
    """Updates the file system timestamp (mtime) using the epoch time."""
    try:
        # Update both access and modification times
        os.utime(media_path, (creation_time_epoch, creation_time_epoch))
        print(f"-> Updated filesystem time for {os.path.basename(media_path)}")

    except Exception as e:
        print(f"-> Could not update filesystem time: {e}")


def load_json_metadata_map(takeout_path):
    """
    Reads all JSON files into an in-memory map keyed by the 'title' field.
    This handles supplemental JSON files robustly.
    """
    metadata_map = {}
    json_files = glob.glob(os.path.join(takeout_path, "**/*.json"), recursive=True)
    json_files_len = len(json_files)
    print(f"Found {json_files_len} potential JSON files...")

    for i, json_path in enumerate(json_files):
        if (i % 500) == 0:
            print(f"({i}/{json_files_len}) Processing json_path {json_path}")
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

                # The 'title' field in the JSON is the original filename Google uses
                title = data.get("title")
                if title:
                    # Store the JSON data and the path to the JSON file itself
                    metadata_map[title] = {"data": data, "json_path": json_path}
        except json.JSONDecodeError:
            print(f"Could not decode JSON file: {json_path}")
        except Exception as e:
            print(f"Error processing JSON file {json_path}: {e}")

    print(
        f"Successfully mapped {len(metadata_map)} unique metadata entries by 'title'."
    )
    return metadata_map


def process_files_with_map(takeout_path, metadata_map):
    """Iterates over all media files and attempts to match them using the metadata map."""
    count_merged = 0

    # Iterate over all non-JSON files (images and videos)
    failures = []  # Paths of media that failed to process with exiftool
    untouched = []  # Paths of media that did not have metadata overwritten
    media_files = glob.glob(os.path.join(takeout_path, "**/*.*"), recursive=True)
    for i, media_path in enumerate(media_files):
        if (i % 500) == 0:
            print(f"({i}/{len(media_files)}) Processing media {media_path}")

        if media_path.lower().endswith(".json"):
            continue

        filename = os.path.basename(media_path)

        # Check if this filename is a key in our metadata map
        if filename in metadata_map:
            match = metadata_map[filename]
            json_path = match["json_path"]
            json_data = match["data"]

            print(f"\nMatch found for '{filename}'. Merging metadata...")

            # Use ExifTool to write metadata from the JSON into the media file
            # This handles both standard JPEGs and videos (MP4/MOV)
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
            creation_time_epoch = int(json_data["creationTime"]["timestamp"])
            update_file_mtime(media_path, creation_time_epoch)
            count_merged += 1

        # This else block is for files where the metadata was probably already embedded
        # in the original upload and no *edits* were made in Google Photos.
        # It's usually fine to skip these.
        else:
            print(f"No specific JSON metadata edits found for: {filename}")
            untouched.append(media_path)

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

    print(f"\nProcessing complete. Merged metadata for {count_merged} files.")


# --- Main execution ---

# !!! IMPORTANT: CHANGE THIS PATH to the root of your extracted Google Takeout folder !!!
TAKEout_DIRECTORY = "/mnt/e/pictures/google_photos/Takeout/Google Photos"

# Safety check
if not os.path.isdir(TAKEout_DIRECTORY):
    print(f"Directory not found: {TAKEout_DIRECTORY}")
    print(
        "Please update the 'TAKEout_DIRECTORY' variable in the script to the root of your extracted Google Photos folder."
    )
else:
    print("--- Starting Google Photos Metadata Merge ---")
    print(
        "!!! WARNING: This script modifies files in place. ENSURE YOU HAVE A BACKUP COPY OF YOUR DATA !!!"
    )
    input("Press Enter to continue or Ctrl+C to cancel...")

    # Step 1: Build the map from all JSON files
    metadata_map = load_json_metadata_map(TAKEout_DIRECTORY)

    # Step 2: Iterate over all media files and use the map to merge data
    process_files_with_map(TAKEout_DIRECTORY, metadata_map)
