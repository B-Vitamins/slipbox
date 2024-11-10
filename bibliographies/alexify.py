import os
import logging
import argparse
import json
import re
import unicodedata
import string
import pyalex
import bibtexparser
from fuzzywuzzy import fuzz
from requests.exceptions import HTTPError, RequestException
from pylatexenc.latex2text import LatexNodes2Text

# Configuration for pyalex
pyalex.config.email = "ayand@iisc.ac.in"
pyalex.config.max_retries = 5
pyalex.config.retry_backoff_factor = 0.3
pyalex.config.retry_http_codes = [429, 500, 503]

# Constants
TITLE_MATCH_THRESHOLD_HIGH = 90
TITLE_MATCH_THRESHOLD_LOW = 50
AUTHOR_MATCH_THRESHOLD = 80
LOGGING_FORMAT = "%(asctime)s - %(message)s"

# Logging configuration
logging.basicConfig(
    level=logging.INFO, format=LOGGING_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"
)

bib_writer = bibtexparser.bwriter.BibTexWriter()
bib_writer.indent = "  "
bib_writer.order_entries_by = None
openalex_cache = {}
latex_converter = LatexNodes2Text()

# Utility Functions


def find_bib_files(path, mode="original"):
    """
    Recursively find all .bib files in the given path.

    Parameters:
    - path (str): The path to search for .bib files.
    - mode (str): 'original' to find original .bib files,
                  'processed' to find files with '-oa.bib' suffix.
    """
    bib_files = []
    if os.path.isfile(path):
        if (
            mode == "original"
            and path.endswith(".bib")
            and not path.endswith("-oa.bib")
        ):
            bib_files.append(path)
        elif mode == "processed" and path.endswith("-oa.bib"):
            bib_files.append(path)
    elif os.path.isdir(path):
        for root, _, files in os.walk(path):
            if "books" in root:
                continue  # Skip the books directory
            for file in files:
                if (
                    mode == "original"
                    and file.endswith(".bib")
                    and not file.endswith("-oa.bib")
                ):
                    bib_files.append(os.path.join(root, file))
                elif mode == "processed" and file.endswith("-oa.bib"):
                    bib_files.append(os.path.join(root, file))
    else:
        logging.error(f"Path {path} is neither a file nor a directory.")
    return bib_files


def extract_year_from_filename(filename):
    """Extracts a four-digit year from the filename if present."""
    match = re.search(r"(\d{4})", filename)
    if match:
        return int(match.group(1))
    return None


def sort_bib_files_by_year(bib_files):
    """Sorts the list of bib files based on the year extracted from their filenames."""
    bib_files_with_years = []
    bib_files_without_years = []
    for file in bib_files:
        filename = os.path.basename(file)
        year = extract_year_from_filename(filename)
        if year:
            bib_files_with_years.append((year, file))
        else:
            bib_files_without_years.append(file)
    # Sort files with years
    bib_files_with_years.sort(key=lambda x: x[0])
    # Extract sorted files
    sorted_files = [file for _, file in bib_files_with_years]
    # Append files without years at the end
    sorted_files.extend(bib_files_without_years)
    return sorted_files


def load_bib_file(bib_file_path):
    """Loads and parses a .bib file."""
    try:
        parser = bibtexparser.bparser.BibTexParser(common_strings=True)
        with open(bib_file_path, "r") as bib_file:
            return bibtexparser.load(bib_file, parser)
    except Exception as e:
        logging.error(f"Failed to load {bib_file_path}: {e}")
        return None


def save_bib_file(bib_file_path, bib_database):
    """Saves the updated .bib file."""
    try:
        with open(bib_file_path, "w") as bib_file:
            bib_file.write(bib_writer.write(bib_database))
    except Exception as e:
        logging.error(f"Failed to save {bib_file_path}: {e}")

def clean_bibtex_entry(entry):
    """Cleans newlines, leading/trailing spaces, and converts LaTeX to Unicode in all fields."""
    for field in entry:
        if isinstance(entry[field], str):
            # Convert LaTeX to Unicode
            entry[field] = latex_converter.latex_to_text(entry[field])
            # Clean up any newlines and leading/trailing spaces
            entry[field] = " ".join(entry[field].splitlines()).strip()
    return entry

def normalize_text(text):
    """Normalize text by removing accents and converting to lowercase."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ASCII", "ignore").decode()
    text = re.sub(
        r"\s+", " ", text
    )  # Replace multiple spaces with single space
    return text.lower().strip()


def fuzzy_match_titles(title1, title2):
    """Check if two titles match using fuzzy matching."""
    try:
        # Handle NoneType cases by returning 0 for non-existent titles
        if not title1 or not title2:
            return 0
        title1_norm = normalize_text(title1)
        title2_norm = normalize_text(title2)
        # Use token_set_ratio for better handling of word order
        return fuzz.token_set_ratio(title1_norm, title2_norm)
    except Exception as e:
        logging.error(f"Error during title fuzzy matching: {e}")
        return 0


def normalize_name(name):
    """Normalize names by removing accents, punctuation, and converting to lowercase."""
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ASCII", "ignore").decode()
    name = name.translate(str.maketrans("", "", string.punctuation))
    name = re.sub(
        r"\s+", " ", name
    )  # Replace multiple spaces with single space
    return name.lower().strip()


def fuzzy_match_authors(bibtex_authors, openalex_authors):
    """Fuzzy match author names and return the percentage of matched authors."""
    try:
        if not bibtex_authors or not openalex_authors:
            return 0
        normalized_bibtex_authors = [
            normalize_name(name) for name in bibtex_authors
        ]
        normalized_openalex_authors = [
            normalize_name(name) for name in openalex_authors
        ]
        matches = 0
        for bib_author in normalized_bibtex_authors:
            # Find the best match in openalex authors
            match_scores = [
                fuzz.partial_ratio(bib_author, oa_author)
                for oa_author in normalized_openalex_authors
            ]
            if match_scores and max(match_scores) >= AUTHOR_MATCH_THRESHOLD:
                matches += 1
        if len(normalized_bibtex_authors) == 0:
            return 0
        return (matches / len(normalized_bibtex_authors)) * 100
    except Exception as e:
        logging.error(f"Error during author fuzzy matching: {e}")
        return 0


def fetch_openalex_works(title):
    """Fetch OpenAlex works for a given title."""
    if title in openalex_cache:
        return openalex_cache[title]
    try:
        # Use search with per_page to get more results
        results = pyalex.Works().search(title).get(per_page=25)
        openalex_cache[title] = results
        return results
    except (HTTPError, RequestException) as e:
        logging.error(f"Error searching OpenAlex for title '{title}': {e}")
    return []


def parse_bibtex_authors(bibtex_authors):
    """Parse BibTeX authors handling various formats."""
    if not bibtex_authors:
        return []
    authors_list = []
    authors = re.split(r"\s+and\s+", bibtex_authors)
    for author in authors:
        author = author.strip()
        if "," in author:
            parts = [part.strip() for part in author.split(",", 1)]
            if len(parts) == 2:
                authors_list.append(f"{parts[1]} {parts[0]}")
            else:
                authors_list.append(" ".join(parts))
        else:
            authors_list.append(author)
    return authors_list


def format_authors_bibtex(authorships):
    """Format OpenAlex authorships into a BibTeX-compliant author field."""
    authors = []
    for authorship in authorships:
        display_name = authorship["author"]["display_name"]
        name_parts = display_name.strip().split()
        if len(name_parts) >= 2:
            last_name = name_parts[-1]
            first_middle = " ".join(name_parts[:-1])
            authors.append(f"{last_name}, {first_middle}")
        else:
            authors.append(display_name)
    return " and ".join(authors)


def process_bib_entry(entry, bib_file_path, user_interaction):
    """Process a single BibTeX entry."""
    modified = False
    matched = False
    entry = clean_bibtex_entry(entry)

    # If the entry already has an OpenAlex ID, skip processing
    if "openalex" in entry:
        logging.info(
            f'"{entry.get("title", "No Title")}" already has OpenAlex ID: {entry["openalex"]}. Skipping entry.'
        )
        return modified, True  # Counted as matched

    title = entry.get("title")
    if not title:
        logging.warning(f"Skipping entry with missing title in {bib_file_path}")
        return modified, matched

    bibtex_authors = entry.get("author", "")
    bibtex_authors_list = parse_bibtex_authors(bibtex_authors)

    results = fetch_openalex_works(title)
    candidates = []

    if results:
        for work in results:
            openalex_title = work.get("title", "")
            openalex_authors_list = [
                authorship["author"]["display_name"]
                for authorship in work.get("authorships", [])
            ]
            title_match_score = fuzzy_match_titles(title, openalex_title)

            if title_match_score >= TITLE_MATCH_THRESHOLD_LOW:
                author_match_score = fuzzy_match_authors(
                    bibtex_authors_list, openalex_authors_list
                )
                # Store candidates with their scores
                candidates.append(
                    {
                        "work": work,
                        "title_match_score": title_match_score,
                        "author_match_score": author_match_score,
                    }
                )

    # Sort candidates by title match score and author match score
    candidates.sort(
        key=lambda x: (x["title_match_score"], x["author_match_score"]),
        reverse=True,
    )

    best_match = None
    for candidate in candidates:
        if (
            candidate["title_match_score"] >= TITLE_MATCH_THRESHOLD_HIGH
            and candidate["author_match_score"] >= AUTHOR_MATCH_THRESHOLD
        ):
            best_match = candidate
            break

    if best_match:
        work = best_match["work"]
        entry["openalex"] = work["id"].split("/")[-1]
        logging.info(
            f'Matched "{title}" with OpenAlex ID {entry["openalex"]} (Title match: {best_match["title_match_score"]}%, Author match: {best_match["author_match_score"]}%)'
        )
        work_full = pyalex.Works()[entry["openalex"]]
        modified = True
        matched = True
    else:
        # If user interaction is enabled, present options
        if user_interaction and candidates:
            for candidate in candidates[:5]:  # Limit to top 5 candidates
                accept = show_low_confidence_match(
                    entry, candidate["work"], candidate
                )
                if accept:
                    work = candidate["work"]
                    entry["openalex"] = work["id"].split("/")[-1]
                    logging.info(
                        f'User accepted match for "{title}" with OpenAlex ID {entry["openalex"]}'
                    )
                    work_full = pyalex.Works()[entry["openalex"]]
                    modified = True
                    matched = True
                    break
        else:
            logging.info(f'No high-confidence match found for "{title}"')
    return modified, matched


def show_low_confidence_match(entry, work, scores):
    """Present a low-confidence match for user interaction."""
    print("\nLow-confidence match found:")
    print(f"BibTeX Title: {entry.get('title', 'N/A')}")
    print(f"OpenAlex Title: {work.get('title', 'N/A')}")
    print(f"Title Match Score: {scores['title_match_score']}%")
    print(f"Author Match Score: {scores['author_match_score']}%")
    print(f"OpenAlex ID: {work['id']}")
    user_input = input("Accept this match? (y/n): ").lower()
    return user_input == "y"


# Main Subcommand Handlers


def handle_process(bib_file, user_interaction, force):
    """Handle processing of a single BibTeX file."""
    # First, check if the '-oa.bib' file already exists
    new_bib_file = os.path.splitext(bib_file)[0] + "-oa.bib"
    if os.path.exists(new_bib_file) and not force:
        logging.info(
            f"Processed file already exists: {new_bib_file}. Skipping."
        )
        return

    bib_database = load_bib_file(bib_file)
    if not bib_database:
        return
    logging.info(f"Processing file: {bib_file}")
    total_entries = len(bib_database.entries)
    modified = False
    successful_matches = 0
    skipped_entries = 0

    for entry in bib_database.entries:
        entry_modified, matched = process_bib_entry(
            entry, bib_file, user_interaction
        )
        modified |= entry_modified
        if matched:
            successful_matches += 1
        else:
            skipped_entries += 1

    if modified:
        # Save to the '-oa.bib' file
        save_bib_file(new_bib_file, bib_database)
        logging.info(f"Saved updated BibTeX file to {new_bib_file}")
    else:
        logging.info("No changes made to the BibTeX file.")

    logging.info(
        f"Processed {total_entries} entries, {successful_matches} matches, {skipped_entries} unmatched"
    )


def handle_fetch(bib_file, output_dir, force):
    """Handle fetching OpenAlex Works for entries with IDs."""
    bib_database = load_bib_file(bib_file)
    if not bib_database:
        return
    logging.info(f"Fetching JSONs for file: {bib_file}")
    total_entries = len(bib_database.entries)
    fetched_entries = 0

    for entry in bib_database.entries:
        if "openalex" in entry:
            if fetch_and_save_work(
                entry["openalex"], bib_file, output_dir, force=force
            ):
                fetched_entries += 1

    logging.info(f"Fetched JSONs for {fetched_entries}/{total_entries} entries")


def prepare_output_dir(bib_file, work_id, output_dir):
    """Prepares output directory structure based on the input path and work ID."""
    relative_path = os.path.relpath(bib_file)
    relative_dir = os.path.dirname(relative_path)
    output_path = os.path.join(output_dir, relative_dir)
    os.makedirs(output_path, exist_ok=True)
    return os.path.join(output_path, f"{work_id}.json")


def fetch_and_save_work(work_id, bib_file, output_dir, force=False):
    """Fetch and save OpenAlex work details as JSON."""
    try:
        output_file = prepare_output_dir(bib_file, work_id, output_dir)
        if os.path.exists(output_file) and not force:
            logging.info(f"Work {work_id} already exists, skipping fetch.")
            return False

        work = pyalex.Works()[work_id]
        if not work:
            logging.warning(f"No OpenAlex Work found for ID {work_id}")
            return False

        with open(output_file, "w") as json_file:
            json.dump(work, json_file, indent=4)

        logging.info(f"Saved OpenAlex Work to {output_file}")
        return True
    except Exception as e:
        logging.error(f"Error fetching or saving OpenAlex Work {work_id}: {e}")
        return False


def handle_missing(bib_file):
    """Handle listing entries without OpenAlex IDs."""
    bib_database = load_bib_file(bib_file)
    if not bib_database:
        return
    missing_entries = 0

    for entry in bib_database.entries:
        if "openalex" not in entry:
            print(f"File: {bib_file}, Title: {entry.get('title', 'Unknown')}")
            missing_entries += 1

    logging.info(f"Total entries without OpenAlex ID: {missing_entries}")


# CLI Setup


def main():
    parser = argparse.ArgumentParser(
        description="Process BibTeX files with OpenAlex data and manage OpenAlex Work details.",
        epilog="""
EXAMPLES:

1. Process BibTeX files to add OpenAlex IDs:
   python script.py process /path/to/bib/file_or_directory --interactive

2. Fetch OpenAlex Works for entries with OpenAlex IDs:
   python script.py fetch /path/to/bib/file_or_directory --output-dir /path/to/output/dir

3. List BibTeX entries without OpenAlex IDs:
   python script.py missing /path/to/bib/file_or_directory
""",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # Process subcommand
    process_parser = subparsers.add_parser(
        "process",
        help="Process BibTeX files to add OpenAlex IDs based on title and author matching.",
    )
    process_parser.add_argument(
        "path", help="Path to a .bib file or directory containing .bib files."
    )
    process_parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactive mode for low-confidence matches.",
    )
    process_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force processing and overwrite existing '-oa.bib' files.",
    )

    # Fetch subcommand
    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Fetch OpenAlex Works for entries with OpenAlex IDs and save JSON files.",
    )
    fetch_parser.add_argument(
        "path", help="Path to a .bib file or directory containing .bib files."
    )
    fetch_parser.add_argument(
        "--output-dir",
        "-o",
        required=True,
        help="Directory to store the fetched Work JSON files.",
    )
    fetch_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force refetching even if the JSON file exists.",
    )

    # Missing subcommand
    missing_parser = subparsers.add_parser(
        "missing", help="List BibTeX entries that do not have OpenAlex IDs."
    )
    missing_parser.add_argument(
        "path", help="Path to a .bib file or directory containing .bib files."
    )

    args = parser.parse_args()

    if args.subcommand == "process":
        bib_files = find_bib_files(args.path, mode="original")
        sorted_bib_files = sort_bib_files_by_year(bib_files)
        for bib_file in sorted_bib_files:
            handle_process(bib_file, args.interactive, args.force)
    elif args.subcommand == "fetch":
        bib_files = find_bib_files(args.path, mode="processed")
        sorted_bib_files = sort_bib_files_by_year(bib_files)
        for bib_file in sorted_bib_files:
            handle_fetch(bib_file, args.output_dir, args.force)
    elif args.subcommand == "missing":
        bib_files = find_bib_files(args.path, mode="processed")
        sorted_bib_files = sort_bib_files_by_year(bib_files)
        for bib_file in sorted_bib_files:
            handle_missing(bib_file)


if __name__ == "__main__":
    main()
