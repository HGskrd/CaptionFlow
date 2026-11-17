"""Assemble an Arch/EndeavourOS x86_64 archive, using a native Linux GPUI build.

Includes pinned CUDA-free CPU/Vulkan runtimes and CPython wheels. This builder
does not establish Linux hardware compatibility; run the target acceptance checks.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Sources/CaptionFlow/Resources/worker"))
from runtime_assets import NEMO_HASHES, NEMO_VERSION, LLAMA_ASSETS, LLAMA_TAG, MODELS, download, sha256


def build(output, offline_models=False, python_version="3.14", gpui_binary=None):
    downloads = ROOT / "dist/linux-downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="linux-staging-", dir=ROOT / "dist") as temporary:
        staging = Path(temporary) / "CaptionFlow"
        for source in ("portable", "Sources/CaptionFlow/Resources/worker", "docs/fixtures"):
            shutil.copytree(ROOT / source, staging / source,
                            ignore=shutil.ignore_patterns("__pycache__", "data", "*.pyc", "caption_audio.*", "results"))
        binary = Path(gpui_binary or ROOT / "gpui/target/release/CaptionFlowGPUI")
        if not binary.is_file() or binary.read_bytes()[:4] != b"\x7fELF":
            raise ValueError("Build the GPUI executable on Linux first, or supply --gpui-binary")
        shutil.copy2(binary, staging / "CaptionFlowGPUI")
        (staging / "CaptionFlowGPUI").chmod(0o755)
        subprocess.run([sys.executable, str(ROOT / "scripts/build-audio.py"),
                        "--output", str(staging / "portable/caption_audio.so")], check=True)
        if (staging / "portable/caption_audio.so").read_bytes()[:4] != b"\x7fELF":
            raise ValueError("Audio library must be built on Linux")
        (staging / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/setup-linux.py", staging / "scripts/setup-linux.py")
        shutil.copy2(ROOT / "docs/linux.md", staging / "README.md")
        (staging / "setup-arch.sh").write_text(
            '#!/bin/sh\nset -eu\napp_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            'exec python3 "$app_dir/scripts/setup-linux.py" "$@"\n', encoding="utf-8")
        (staging / "start-captionflow.sh").write_text(
            '#!/bin/sh\nset -eu\napp_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            'data_dir="${XDG_DATA_HOME:-$HOME/.local/share}/captionflow"\n'
            'mkdir -p "$data_dir"\n'
            'exec python3 "$app_dir/portable/linux_launcher.py" "$@" >> "$data_dir/launcher.log" 2>&1\n', encoding="utf-8")
        for launcher in ("setup-arch.sh", "start-captionflow.sh"):
            (staging / launcher).chmod(0o755)
        wheels = staging / "wheels"
        subprocess.run([sys.executable, "-m", "pip", "download", "-r", str(ROOT / "portable/requirements-linux.txt"),
                        "--dest", str(wheels), "--only-binary=:all:",
                        "--platform", "manylinux_2_34_x86_64", "--platform", "manylinux_2_28_x86_64",
                        "--platform", "manylinux_2_27_x86_64", "--platform", "manylinux2014_x86_64",
                        "--python-version", python_version, "--implementation", "cp"], check=True)
        cache = staging / "portable/data/downloads"
        cache.mkdir(parents=True)
        for backend in ("cpu", "vulkan"):
            key = "linux-x86_64-" + backend
            name = f"nemo-speech-{NEMO_VERSION}-{key}.tar.gz"
            download(f"https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v{NEMO_VERSION}/{name}",
                     downloads / name, NEMO_HASHES[key])
            shutil.copy2(downloads / name, cache / name)
            suffix, digest = LLAMA_ASSETS[key]
            name = f"llama-{LLAMA_TAG}-bin-{suffix}"
            download(f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{name}",
                     downloads / name, digest)
            shutil.copy2(downloads / name, cache / name)
        if offline_models:
            for model in MODELS.values():
                source = ROOT / "dist/portable-data/models" / model["file"]
                if not source.exists() or sha256(source) != model["sha256"]:
                    raise ValueError("Missing or invalid offline model: " + str(source))
                target = staging / "portable/data/models" / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        manifest = {str(p.relative_to(staging)): sha256(p) for p in sorted(staging.rglob("*")) if p.is_file()}
        (staging / "dependency-sha256.json").write_text(json.dumps(manifest, indent=2) + "\n")
        with tarfile.open(output, "w:gz") as archive:
            archive.add(staging, arcname="CaptionFlow")
    print(json.dumps({"artifact": str(output), "sha256": sha256(output), "size": output.stat().st_size,
                      "python_wheels": python_version, "hardware_verified": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "dist/CaptionFlow-Linux-x86_64.tar.gz"))
    parser.add_argument("--offline-models", action="store_true")
    parser.add_argument("--gpui-binary", help="GPUI ELF executable built from this candidate on Linux")
    parser.add_argument("--python-version", default="3.14", help="CPython minor version on the target Arch installation")
    args = parser.parse_args()
    build(args.output, args.offline_models, args.python_version, args.gpui_binary)
