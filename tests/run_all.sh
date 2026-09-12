# Convenience runner: execute the whole ingestion test-suite.
#
#   bash tests/run_all.sh        (from the project root)
cd "$(dirname "$0")/.." || exit 1
PYTHONPATH=. .venv/Scripts/python.exe -m unittest discover -s tests -t . -v