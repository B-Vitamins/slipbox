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

# Directory to store JSON and PDF files in the user's home directory
CACHE_DIR = Path.home() / "store"
CACHE_DIR.mkdir(exist_ok=True)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

ARXIV_BASE_URL = "http://export.arxiv.org/api/query"
ARXIV_RATE_LIMIT = 3  # seconds between arXiv API requests

def set_file_read_only(file_path):
    """Set file permissions to read-only."""
    try:
        file_path.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        logger.debug(f"{file_path}: Set to read-only.")
    except Exception as e:
        logger.warning(f"{file_path}: {e}")

def load_bib_file(file_path):
    """Load and parse a .bib file."""
    try:
        with open(file_path, "r") as bib_file:
            bib_database = bibtexparser.load(bib_file)
        return bib_database.entries
    except Exception as e:
        logger.error(f"{file_path}: {e}")
        return []

def fetch_work_object(work_id, title):
    """Fetch the Work object using PyAlex and cache it."""
    try:
        work_data = Works()[work_id]
        json_path = CACHE_DIR / f"{work_id}.json"

        # Write JSON data to file and set to read-only if it doesn’t already exist
        if not json_path.exists():
            with open(json_path, "w") as json_file:
                json.dump(work_data, json_file)
            set_file_read_only(json_path)
            logger.info(f"{title} {work_id} (json): Downloaded.")
        return work_data
    except Exception as e:
        logger.error(f"{title} {work_id} (json): {e}")
        return None

def download_from_arxiv(arxiv_id: str) -> str:
    """Get the PDF URL for an arXiv article using the arXiv API."""
    time.sleep(ARXIV_RATE_LIMIT)  # Respect rate limit
    params = {"id_list": arxiv_id}
    response = requests.get(ARXIV_BASE_URL, params=params)
    response.raise_for_status()

    # Construct the PDF URL
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"

def download_pdf(pdf_url, work_id, title):
    """Download PDF from the given URL, using the arXiv API if necessary."""
    pdf_path = CACHE_DIR / f"{work_id}.pdf"
    try:
        # Check if the URL is an arXiv URL
        if "arxiv.org" in pdf_url:
            logger.info(f"{title} {work_id} (pdf): arXiv URL detected. Using arXiv API.")
            arxiv_id = pdf_url.split("/")[-1].replace(".pdf", "")
            pdf_url = download_from_arxiv(arxiv_id)

        # Download the PDF
        response = requests.get(pdf_url, stream=True, timeout=10)
        response.raise_for_status()
        with open(pdf_path, "wb") as pdf_file:
            for chunk in response.iter_content(chunk_size=8192):
                pdf_file.write(chunk)
        set_file_read_only(pdf_path)
        logger.info(f"{title} {work_id} (pdf): Downloaded.")
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.error(f"{title} {work_id} (pdf): {e}")

def process_bib_file(file_path):
    """Process a .bib file, fetch Work objects, and download PDFs if available."""
    entries = load_bib_file(file_path)
    if not entries:
        logger.warning(f"No entries found in {file_path}")
        return

    for entry in entries:
        work_id = entry.get("openalex")
        title = entry.get("title", "Unnamed entry")

        if not work_id:
            logger.warning(f"{title}: OpenAlex ID unavailable.")
            continue

        json_path = CACHE_DIR / f"{work_id}.json"

        # Load cached JSON if it exists
        if json_path.exists():
            logger.info(f"{title} {work_id} (json): Cached.")
            with open(json_path, "r") as json_file:
                work_data = json.load(json_file)
        else:
            work_data = fetch_work_object(work_id, title)
            if work_data is None:
                logger.error(f"{title} {work_id} (json): Failed.")
                continue

        # Extract and validate best open-access location
        best_oa_location = work_data.get("best_oa_location", None)
        if best_oa_location:
            pdf_url = best_oa_location.get("pdf_url", None)
            if pdf_url:
                pdf_path = CACHE_DIR / f"{work_id}.pdf"
                if not pdf_path.exists():
                    download_pdf(pdf_url, work_id, title)
                else:
                    logger.info(f"{title} {work_id} (pdf): Cached.")
            else:
                logger.info(f"{title} {work_id} (pdf): Unavailable.")

def main(inputs):
    """Main function to process each file or directory input."""
    for input_path in inputs:
        path = Path(input_path)
        if path.is_file() and path.suffix == "oa.bib":
            logger.info(f"Processing file: {path}")
            process_bib_file(path)
        elif path.is_dir():
            logger.info(f"Processing directory: {path}")
            for bib_file in path.rglob("*oa.bib"):
                logger.info(f"Processing file: {bib_file}")
                process_bib_file(bib_file)
        else:
            logger.warning(f"Invalid input: {path}. Not a .bib file or directory.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch OpenAlex Work objects and PDFs from .bib files.")
    parser.add_argument("inputs", metavar="INPUT", type=str, nargs="+", help="Path(s) to .bib file(s) or directory(ies)")
    args = parser.parse_args()
    main(args.inputs)
