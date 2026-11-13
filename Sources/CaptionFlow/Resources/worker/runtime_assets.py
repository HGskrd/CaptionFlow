"""Pinned CUDA-free native runtimes and models. Downloads are explicit and hashed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile

NEMO_VERSION = "0.3.0"
LLAMA_TAG = "b12110"  # Release v0.9.0's published nightly tag.
NEMO_HASHES = {
    "macos-aarch64-metal": "2b972ed8b5dba07492212acf8cb75cbf34ecb141198ffa1997febe84b7c4d9ed",
    "macos-aarch64-cpu": "2c3a7a719b778daeb14f147e011891f16e4b2ac62388d8b8d8b365cc2eebdf15",
    "macos-x86_64-cpu": "97e98aa5eb82dffa4f513f26336b5301d792c9fb43f720ac8f9520a0e92ffe3f",
    "windows-x86_64-cpu": "4ed9740e361e4653f56c3dce7d1e81883a1c8b831c1bd7d01567fff1130b5b05",
    "windows-x86_64-vulkan": "72b3b8b41bfe2be0320eff4e9d310594e4f99deefcde4a157799a039656963ce",
    "linux-x86_64-cpu": "255f12b722ab15b2a38a51cf125d56e3fa97058b528f7e895b39d6a473deb06e",
    "linux-x86_64-vulkan": "2a8b2ca4610b6ea5d659ba10794526f6e5a77fb1623d52c35272a9fd1f74abac",
}
LLAMA_ASSETS = {
    "macos-x86_64-cpu": ("macos-x64.tar.gz", "c8094268b6bf62f4998f0e45656fd9f5e2d4afb6b1cd0044d761db64be1dd47d"),
    "macos-aarch64": ("macos-arm64.tar.gz", "4f5b8de12e0b9516658ca6a5764c6ffb3d7c659c3dd538556b1413c7b4050662"),
    "windows-x86_64-cpu": ("win-cpu-x64.zip", "98901e327c252875accaf2d1bf364e10e868843b0b63468baad1aa945cdc5da2"),
    "windows-x86_64-vulkan": ("win-vulkan-x64.zip", "9732a5e48887ca54d0bfe4af4324dc85fd56cd8cda19b52df3542fa6ebe6874f"),
}
MODELS = {
    "nemotron": {
        "repo": "nvidia/nemotron-3.5-asr-streaming-0.6b",
        "revision": "ea30d66debe3740a08b573244286791d423d6b3e",
        "file": "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf",
        "sha256": "3fc991d3badad7277c11030a7519832cddaf2057aafed6d4b25147e953a070b1",
    },
    "hy-mt2": {
        "repo": "tencent/Hy-MT2-1.8B-GGUF",
        "revision": "a0c709d9fac510f2c807aa3af52872340dc37a4a",
        "file": "Hy-MT2-1.8B-Q4_K_M.gguf",
        "sha256": "dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699",
    },
}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def download(url, path, expected, progress=print, allow_network=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and sha256(path) == expected:
        return path
    if not allow_network:
        raise PermissionError(f"Local-only mode: supply {path.name} offline, or enable online participation")
    partial = path.with_suffix(path.suffix + ".partial")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "CaptionFlow"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    progress(f"Downloading {path.name}…")
    with urllib.request.urlopen(request, timeout=60) as response:
        resume = offset and response.status == 206
        with partial.open("ab" if resume else "wb") as handle:
            last_report = 0
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                handle.write(chunk)
                if handle.tell() - last_report >= 32 * 1024 * 1024:
                    last_report = handle.tell()
                    progress(f"{path.name}: {last_report / 1e6:.0f} MB")
    if sha256(partial) != expected:
        partial.unlink(missing_ok=True)
        raise ValueError(f"Checksum mismatch for {path.name}; nothing was installed.")
    partial.replace(path)
    return path


def extract(archive, destination):
    destination = Path(destination)
    if (destination / ".complete").exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        root = Path(temporary)
        if str(archive).endswith(".zip"):
            with zipfile.ZipFile(archive) as handle:
                for item in handle.infolist():
                    if not (root / item.filename).resolve().is_relative_to(root.resolve()):
                        raise ValueError("Unsafe archive path")
                handle.extractall(root)
        else:
            with tarfile.open(archive) as handle:
                handle.extractall(root, filter="data")
        directories = list(root.iterdir())
        payload = directories[0] if len(directories) == 1 and directories[0].is_dir() else root
        if destination.exists():
            shutil.rmtree(destination)  # Only this setup-owned incomplete install.
        shutil.copytree(payload, destination, symlinks=True)
        (destination / ".complete").write_text(Path(archive).name)
    return destination


def platform_key():
    system = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}[platform.system()]
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64" if machine in ("amd64", "x86_64") else machine
    return f"{system}-{arch}"


def install(root, backend, translation=True, progress=print, allow_network=True, speech="nemotron"):
    if speech not in ("nemotron", "phonon"):
        raise ValueError("Unknown speech model: " + speech)
    root = Path(root).expanduser().resolve()
    key = f"{platform_key()}-{backend}"
    if key not in NEMO_HASHES:
        raise ValueError(f"No pinned CUDA-free NeMo runtime for {key}")
    extension = "zip" if key.startswith("windows") else "tar.gz"
    name = f"nemo-speech-{NEMO_VERSION}-{key}.{extension}"
    archive = download(f"https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v{NEMO_VERSION}/{name}",
                       root / "downloads" / name, NEMO_HASHES[key], progress, allow_network)
    sdk = extract(archive, root / "runtimes" / f"nemo-{key}")
    selected_models = {}
    for identity in ((["nemotron"] if speech == "nemotron" else []) + (["hy-mt2"] if translation else [])):
        spec = MODELS[identity]
        path = download(f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{spec['file']}",
                        root / "models" / spec["file"], spec["sha256"], progress, allow_network)
        selected_models[identity] = str(path)
    if speech != "nemotron":
        from small_models import install_model
        selected_models[speech] = install_model(speech, root / "models", progress, allow_network)
    config = {"type": "configure", "speech_backend": "nemo" if speech == "nemotron" else speech + "_cpu", "native_backend": backend,
              "native_sdk": str(sdk), "speech_model": selected_models[speech],
              "gpu": -1 if backend == "cpu" else 0, "nemotron_right_context": 3,
              "source_language": "fr", "targets": ["English"] if translation else [],
              "translation_backend": "llama", "translation_model_type": "hunyuan_v1_dense",
              "allow_downloads": False, "cache_dir": str(root / "models"), "silence_threshold": 0.008}
    config["speech_identity"] = speech
    if translation:
        llama_key = platform_key() if platform_key() == "macos-aarch64" else key
        if llama_key not in LLAMA_ASSETS:
            raise ValueError(f"No pinned translation runtime for {llama_key}; ASR-only setup is available")
        suffix, checksum = LLAMA_ASSETS[llama_key]
        name = f"llama-{LLAMA_TAG}-bin-{suffix}"
        archive = download(f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{name}",
                           root / "downloads" / name, checksum, progress, allow_network)
        llama = extract(archive, root / "runtimes" / f"llama-{llama_key}")
        binaries = list(llama.rglob("llama-server.exe" if os.name == "nt" else "llama-server"))
        if not binaries:
            raise RuntimeError("Pinned llama release contains no llama-server")
        if os.name == "nt":
            # llama's ZIP expects the MSVC runtime. Reuse the redistributables
            # bundled by the pinned NeMo SDK, beside the executable for DLL lookup.
            for pattern in ("msvcp140*.dll", "vcruntime140*.dll", "concrt140.dll", "vcomp140.dll"):
                for runtime in (sdk / "bin").glob(pattern):
                    shutil.copy2(runtime, binaries[0].parent / runtime.name)
        config.update(translation_model=selected_models["hy-mt2"], llama_server=str(binaries[0]))
    (root / "configuration.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    progress(f"Ready: {root / 'configuration.json'}")
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--backend", required=True, choices=["cpu", "metal", "vulkan"])
    parser.add_argument("--asr-only", action="store_true")
    parser.add_argument("--speech", choices=["nemotron", "phonon"], default="nemotron")
    args = parser.parse_args()
    install(args.root, args.backend, not args.asr_only, speech=args.speech)
