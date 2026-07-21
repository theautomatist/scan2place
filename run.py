"""Standalone entry point for scan2place.

Used both by ``python run.py`` and as the PyInstaller bundle entry point, so the
app runs without Docker as a single executable. Configuration is via environment
variables (see app.main): PORT, HOST, USE_HTTPS, SCAN2PLACE_DATA.
"""
from app.main import main

if __name__ == "__main__":
    main()
