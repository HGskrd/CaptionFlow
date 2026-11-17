"""Install CaptionFlow's private Python runtime and desktop entry on Arch/EndeavourOS."""
import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
from linux_launcher import environment_directory


def desktop_quote(value):
    # Desktop Exec has its own quoting rules; percent signs introduce field codes.
    value = str(value).replace("%", "%%")
    for character in ("\\", '"', "`", "$"):
        value = value.replace(character, "\\" + character)
    return '"' + value.replace("\\", "\\\\") + '"'


def setup(skip_system_packages=False, offline=False):
    if platform.system() != "Linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        raise SystemExit("This preview requires x86_64 Linux (Arch or EndeavourOS).")
    if os.geteuid() == 0:
        raise SystemExit("Run setup as your desktop user; only pacman uses sudo.")
    if not skip_system_packages:
        packages = ["python", "python-pip", "libpulse", "libxcb", "libxkbcommon-x11",
                    "xcb-util-cursor", "xcb-util-keysyms", "xcb-util-wm", "libx11", "wayland",
                    "libglvnd", "fontconfig", "glib2", "dbus", "gcc-libs", "openssl",
                    "vulkan-icd-loader", "vulkan-tools", "libxrandr", "libxi", "alsa-lib"]
        # Keep an existing PulseAudio installation; don't replace the user's audio stack.
        pulse = any(subprocess.run(["pacman", "-Q", name], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0
                    for name in ("pipewire-pulse", "pulseaudio"))
        if not pulse:
            packages += ["pipewire-pulse", "wireplumber"]
        if offline:
            raise SystemExit("Offline setup requires system dependencies already installed; add --skip-system-packages.")
        subprocess.run(["sudo", "pacman", "-Syu", "--needed", *packages], check=True)
        # A full Arch upgrade can replace Python. Re-exec before creating the venv.
        os.execv("/usr/bin/python", ["/usr/bin/python", str(Path(__file__).resolve()), "--skip-system-packages"])

    environment = environment_directory()
    venv.EnvBuilder(with_pip=True, clear=not (environment / ".ready").exists()).create(environment)
    command = [str(environment / "bin/python"), "-m", "pip", "install", "--only-binary=:all:",
               "--find-links", str(ROOT / "wheels"), "-r", str(ROOT / "portable/requirements-linux.txt")]
    if offline:
        command.append("--no-index")
    subprocess.run(command, check=True)
    subprocess.run([str(environment / "bin/python"), "-c",
                    "import numpy, cffi, psutil, rapidfuzz, cryptography; from PySide6.QtWidgets import QApplication"], check=True)
    (environment / ".ready").write_text("CaptionFlow Linux runtime\n")

    applications = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    (applications / "captionflow.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=CaptionFlow\n"
        "Comment=Private live subtitles and translation\n"
        f"Exec={desktop_quote(ROOT / 'start-captionflow.sh')}\n"
        "Terminal=false\nCategories=AudioVideo;Accessibility;\nStartupWMClass=CaptionFlow\n", encoding="utf-8")
    print("Setup complete. Open CaptionFlow from your application menu or run ./start-captionflow.sh.\n"
          "Keep this extracted folder in place. If audio services were just installed, log out and in first.\n"
          "For GPU mode install your vendor's Vulkan driver; vulkaninfo --summary checks availability.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-system-packages", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    setup(args.skip_system_packages, args.offline)
