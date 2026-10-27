# Templates: reviewed code that runs models added from a link

A template is reviewed code that runs a kind of model the app knows (`orchestrator/link_recognise.py`; C3 of
`docs/design/2026-10-13-capabilities-for-everyone.md`). When you paste a link of that kind, the app writes your entry with
the template's `adapter.py` and `env.lock` copied byte for byte, and only the model's own files from the link, each
pinned by its hash. The runner is reviewed; the model isn't (a TorchScript model is its makers' code), so it runs inside
macOS's sandbox (`orchestrator/sandbox.py`), and a changed adapter is said to be code you added
(`link_recognise.reviewed_template`). Its hashes say which model it is, never that it is safe.

Each template folder holds:
- `template.toml`: what it runs, its runner, its adapter, its environment, the operation it implements;
- `adapter.py`: the reviewed runner;
- `requirements.in` and `env.lock`: its Python, every package pinned by its hash (`uv pip compile requirements.in
  --generate-hashes --only-binary :all: --python-version 3.13 --python-platform aarch64-apple-darwin -o env.lock`, `MACOSX_DEPLOYMENT_TARGET=14.0`).

| Template | Runs |
|---|---|
| `bioimageio` | A 2D bioimage.io model with TorchScript or ONNX weights, from the BioImage Model Zoo or a GitHub repository (its files at a pinned commit, or a release's zip): its own test first, then the picture; its output as instance labels. An output whose meaning its names don't say needs an exact binding in `output-semantics.json` (its rdf.yaml's and weights' sha256, with the makers' evidence): affable-shark 1.2, and InstanSeg's brightfield_nuclei 0.1.1 |
| `hf-segmentation` | A Hugging Face transformers segmentation model (SegFormer, UperNet, DPT, BEiT, MobileViT, Mask2Former, MaskFormer) with safetensors weights, transformers' own classes only (never its makers' code); semantic classes become connected regions; a class named background is left out, every other class (class 0 included) kept; a model whose classes are only numbers takes an optional `classes.json` in its entry (`notThings`, with its `source`), checked against the model's classes |

A change to a template is reviewed like any code; entries made from an earlier version keep theirs until their owner
adds them again, and are still said to run the reviewed runner while their files match an earlier reviewed version
(`earlier` in its `template.toml`, by sha256; `link_recognise.reviewed_template`).
