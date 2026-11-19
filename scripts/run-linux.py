"""Run the Linux GPUI app from a checkout using the active Python environment."""

import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root / "gpui")
    environment = {
        **os.environ,
        "CAPTIONFLOW_SOURCE_ROOT": str(root),
        "CAPTIONFLOW_PYTHON": sys.executable,
    }
    os.execvpe("cargo", ["cargo", "run", "--locked", "--", *sys.argv[1:]], environment)


if __name__ == "__main__":
    main()
