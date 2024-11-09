(use-modules (guix ci))

(specifications->manifest
  '("python"
    "python-requests"
    "python-fuzzywuzzy"
    "python-pyalex"
    "python-bibtexparser-1"
    "python-pylatexenc"))
