"""Compatibility entrypoint for the independent NeonDB/R2 importer.

Usage: python -m scripts.import_local --owner-email account@example.com --label 2025/1 \\
  --morning morning.pdf --afternoon afternoon.pdf --key answers.pdf

This command writes to the owner's private edition, not to a web API session.
"""
from import_udesc import main

if __name__ == '__main__':
    raise SystemExit(main())
