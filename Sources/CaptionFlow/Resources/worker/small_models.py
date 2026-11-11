"""Pinned compact speech checkpoints; no upstream download or telemetry clients."""
from pathlib import Path

NEMOTRON = "nvidia/nemotron-3.5-asr-streaming-0.6b"
SPEECH_MODELS = {
    "redux": {
        "repo": "moondream/parakeet-redux",
        "revision": "2bf128600aac4b16946f7ed8372e56117fe5e23b",
        "files": {
            "config.json": "503c653b2e3bb788adbcb04f5abdee532d958686564081baeed133ff10143f6e",
            "ternary.json": "1221c6d3ce901ffe09c089da758a8db8b76189f80cff41c5afc244fc61e2051d",
            "tokenizer.json": "bd321b096832a3f270bd3b2a88823957920f1a5c5ada71114a26ea729d0cbe91",
            "model.safetensors": "78ec25733ee0d0c1586d1346fc86db9d0c2e436e3a8ab1d32a82d1bb8f848d21",
        },
    },
    "phonon": {
        "repo": "FermionResearch/Phonon-2.5",
        "revision": "2af4bb8ccceb66a8ce06c4081bb968f1802c1e58",
        "files": {
            "config.json": "b89828e6b711f6b324eb99142929dff84bd673051cbc82b4dedcea1f47a6b37f",
            "phonon-2.5.bps.tar.zst": "1faa04053a472cabab3bc31d07f30c23f22906f71044ca10eb1f0de36d1f19a9",
        },
    },
}


def install_model(identity, cache, progress=print, allow_network=False):
    from runtime_assets import download
    spec = SPEECH_MODELS[identity]
    root = Path(cache) / (identity + "-" + spec["revision"])
    for name, checksum in spec["files"].items():
        download(f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{name}",
                 root / name, checksum, progress, allow_network)
    return str(root)


def cpu_runtime_check():
    import torch
    if torch.version.cuda is not None:
        raise RuntimeError("CaptionFlow requires CPU-only PyTorch for compact models; CUDA builds are not supported.")
    return {"torch": torch.__version__, "cuda_compiled": False}


class CompactCPU:
    """In-memory Phonon CPU inference using the Apache-licensed runtime."""
    def __init__(self, identity, path):
        import importlib.metadata
        from runtime_assets import sha256
        cpu_runtime_check()
        self.identity = identity
        if identity == "phonon":
            from fermion._speech.fetch import _unpack
            from fermion._speech.engine_phonon2_cpu import load
            root = Path(path)
            unpacked = root / "cpu"
            container = unpacked / "model.fermion"
            expected = "17cb62d08e751a41819eb779db089aa7ac4eb2bab433ba5c2991d8cbd3cfefb9"
            if not container.is_file() or sha256(container) != expected or not (unpacked / "config.json").is_file():
                _unpack(root / "phonon-2.5.bps.tar.zst", unpacked)
            if sha256(container) != expected:
                raise ValueError("Phonon container checksum mismatch")
            self.model = load(unpacked, profile="five-value-25", backend="phonon2-five-value", quiet=True)
            self.version = "fermion-research " + importlib.metadata.version("fermion-research")
            description = self.model.describe()
            self.details = {key: description.get(key) for key in
                            ("runtime", "activations", "threads", "c_tdt_loop", "c_encoder")}
        else:
            raise ValueError("Unknown compact CPU model: " + identity)

    def transcribe(self, samples):
        return self.model.transcribe_array(samples)[0]
