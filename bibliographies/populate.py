import os
import json
import bibtexparser
from pathlib import Path
import argparse
import logging
import requests
import stat
import time
from pyalex import Works, config

# Set up the polite pool
config.email = "ayand@iisc.ac.in"
config.max_retries = 3
config.retry_backoff_factor = 0.5
config.retry_http_codes = [429, 500, 503]

# Cache directory setup
CACHE_DIR = Path.home() / "documents/store"
CACHE_DIR.mkdir(exist_ok=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # Max items per batch request to OpenAlex API


def extract_openalex_ids_from_bib(file_path):
    """Extract OpenAlex Work IDs from a .bib file."""
    try:
        with open(file_path, "r") as bib_file:
            bib_database = bibtexparser.load(bib_file)
        return list(
            {
                entry.get("openalex")
                for entry in bib_database.entries
                if entry.get("openalex")
            }
        )
    except Exception as e:
        logger.error(f"Failed to extract OpenAlex IDs from {file_path}: {e}")
        return []


def fetch_work_objects_by_ids(work_ids):
    """Fetch Work objects for a list of OpenAlex Work IDs in batches."""
    work_objects = {}
    for i in range(0, len(work_ids), BATCH_SIZE):
        batch = work_ids[i : i + BATCH_SIZE]
        pipe_separated_ids = "|".join(batch)
        url = f"https://api.openalex.org/works?filter=openalex:{pipe_separated_ids}&per-page={BATCH_SIZE}"
        try:
            response = requests.get(url)
            response.raise_for_status()
            results = response.json().get("results", [])
            for work in results:
                work_id = work.get("id").split("/")[-1]
                work_objects[work_id] = work
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching data for batch: {batch}. Error: {e}")
    return work_objects


def populate_works(file_paths):
    """Populate cache with Work JSON objects from OpenAlex for given .bib files."""
    all_work_ids = set()
    for file_path in file_paths:
        all_work_ids.update(extract_openalex_ids_from_bib(file_path))

    # Filter out Work IDs that are already cached
    ids_needing_fetch = [
        work_id
        for work_id in all_work_ids
        if not (CACHE_DIR / f"{work_id}.json").exists()
    ]

    # Batch fetch and save JSON data only for IDs not in cache
    if ids_needing_fetch:
        work_data_map = fetch_work_objects_by_ids(ids_needing_fetch)
        for work_id, work_data in work_data_map.items():
            json_path = CACHE_DIR / f"{work_id}.json"
            if not json_path.exists():
                with open(json_path, "w") as json_file:
                    json.dump(work_data, json_file)
                logger.info(f"{work_id} (json): Saved to cache.")
    else:
        logger.info("All Work JSONs already cached.")


def populate_pdfs(file_paths):
    """Populate cache with PDFs by fetching missing JSON data and PDFs from available URLs."""
    all_work_ids = set()
    for file_path in file_paths:
        all_work_ids.update(extract_openalex_ids_from_bib(file_path))

    # Filter Work IDs to fetch only those missing PDFs in cache
    ids_needing_pdfs = [
        work_id
        for work_id in all_work_ids
        if not (CACHE_DIR / f"{work_id}.pdf").exists()
    ]

    # Batch-fetch JSON data for Work IDs missing PDFs, if absent in cache
    work_data_map = {}
    ids_needing_jsons = [
        work_id
        for work_id in ids_needing_pdfs
        if not (CACHE_DIR / f"{work_id}.json").exists()
    ]
    if ids_needing_jsons:
        work_data_map.update(fetch_work_objects_by_ids(ids_needing_jsons))
        # Save fetched JSON to cache
        for work_id, work_data in work_data_map.items():
            json_path = CACHE_DIR / f"{work_id}.json"
            if not json_path.exists():
                with open(json_path, "w") as json_file:
                    json.dump(work_data, json_file)
                logger.info(f"{work_id} (json): Saved to cache.")

    # Fetch PDFs based on available URLs in cached Work data
    for work_id in ids_needing_pdfs:
        pdf_path = CACHE_DIR / f"{work_id}.pdf"
        if pdf_path.exists():
            logger.info(f"{work_id} (pdf): Already cached.")
            continue

        # Load Work data from cache and get PDF URL
        json_path = CACHE_DIR / f"{work_id}.json"
        if json_path.exists():
            with open(json_path, "r") as json_file:
                work_data = json.load(json_file)
            pdf_url = work_data.get("best_oa_location", {}).get("pdf_url")
            if pdf_url:
                download_pdf(pdf_url, work_id)
        else:
            logger.warning(
                f"{work_id} (json): Missing. Cannot fetch PDF without JSON data."
            )


def download_pdf(pdf_url, work_id):
    """Download PDF from the given URL and save to cache."""
    pdf_path = CACHE_DIR / f"{work_id}.pdf"
    try:
        response = requests.get(pdf_url, stream=True, timeout=10)
        response.raise_for_status()
        with open(pdf_path, "wb") as pdf_file:
            for chunk in response.iter_content(chunk_size=8192):
                pdf_file.write(chunk)
        logger.info(f"{work_id} (pdf): Downloaded from URL {pdf_url}")
    except requests.exceptions.RequestException as e:
        logger.error(f"{work_id} (pdf): Failed to download. Error: {e}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Populate OpenAlex Work objects and PDFs cache."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Populate works subcommand
    works_parser = subparsers.add_parser(
        "works", help="Populate cache with Work JSONs."
    )
    works_parser.add_argument(
        "inputs",
        metavar="INPUT",
        type=str,
        nargs="+",
        help="Paths to .bib files or directories",
    )

    # Populate pdfs subcommand
    pdfs_parser = subparsers.add_parser(
        "pdfs", help="Populate cache with PDFs."
    )
    pdfs_parser.add_argument(
        "inputs",
        metavar="INPUT",
        type=str,
        nargs="+",
        help="Paths to .bib files or directories",
    )

    args = parser.parse_args()

    # Resolve input paths for each subcommand
    file_paths = []
    for input_path in args.inputs:
        path = Path(input_path)
        if path.is_file() and path.suffix == ".bib":
            file_paths.append(path)
        elif path.is_dir():
            file_paths.extend(path.rglob("*.bib"))
        else:
            logger.warning(
                f"Invalid input: {input_path} is not a .bib file or directory."
            )

    # Execute the appropriate subcommand
    if args.command == "works":
        populate_works(file_paths)
    elif args.command == "pdfs":
        populate_pdfs(file_paths)


if __name__ == "__main__":
    main()
