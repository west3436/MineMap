"""PyInstaller entry point. The package entry lives in minemap/__main__.py and uses
relative imports, which only resolve when the package is imported, so the frozen
build starts here and hands over to it."""
import multiprocessing
import sys

from minemap.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
