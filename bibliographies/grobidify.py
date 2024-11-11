import os
import argparse
from grobid_client.grobid_client import GrobidClient

INPUT_PATH = "/home/b/documents/store"
OUTPUT_PATH = INPUT_PATH
CONFIG_PATH = "/home/b/documents/slipbox/bibliographies/config.json"


def main():
    parser = argparse.ArgumentParser(
        description="Process PDFs with GROBID full-text extraction."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force reprocessing of PDFs, overwriting existing .tei.xml files.",
    )
    args = parser.parse_args()

    client = GrobidClient(config_path=CONFIG_PATH)

    pdf_files = [
        os.path.join(INPUT_PATH, f)
        for f in os.listdir(INPUT_PATH)
        if f.lower().endswith(".pdf")
    ]

    if not args.force:
        pdf_files = [
            pdf
            for pdf in pdf_files
            if not os.path.isfile(
                os.path.join(
                    OUTPUT_PATH,
                    os.path.splitext(os.path.basename(pdf))[0] + ".tei.xml",
                )
            )
        ]

    if not pdf_files:
        print("All PDFs are already processed.")
        return

    client.process(
        service="processFulltextDocument",
        input_path=INPUT_PATH,
        output=OUTPUT_PATH,
        n=10,
        generateIDs=True,
        consolidate_header=True,
        consolidate_citations=True,
        include_raw_citations=True,
        include_raw_affiliations=True,
        tei_coordinates=True,
        segment_sentences=True,
        force=args.force,
        verbose=True,
    )
    print("Processing complete.")


if __name__ == "__main__":
    main()
