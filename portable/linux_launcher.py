"""Launch the private Linux environment without modifying system Python."""
import hashlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def environment_directory():
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "captionflow"
    identity = hashlib.sha256((ROOT / "portable/requirements-linux.txt").read_bytes()).hexdigest()[:12]
    return data / "python" / f"{sys.version_info.major}.{sys.version_info.minor}-{identity}"


def main():
    python = environment_directory() / "bin/python"
    if not (python.parent.parent / ".ready").exists() or not python.exists():
        raise SystemExit("CaptionFlow needs setup. Run ./setup-arch.sh from the extracted folder first.")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["CAPTIONFLOW_PYTHON"] = str(python)
    executable = ROOT / "CaptionFlowGPUI"
    os.execv(str(executable), [str(executable), *sys.argv[1:]])


if __name__ == "__main__":
    main()
