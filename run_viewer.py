"""Start from source, or serve as the PyInstaller entry point."""

import sys
import multiprocessing
from pathlib import Path

if __name__ == "__main__":
    multiprocessing.freeze_support()

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from ts2psp.app import main

if __name__ == "__main__":
    main()
