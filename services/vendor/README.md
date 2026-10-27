# Vendored models

## SAM 3.1 (`facebook/sam3.1`)

Meta's code (https://github.com/facebookresearch/sam3) targets CUDA. `sam3-apple-silicon.patch`
makes it run on Apple Silicon (MPS) and CPU without changing CUDA behaviour:

- `sam3/_device.py`: picks CUDA, else MPS, else CPU (override with `SAM3_DEVICE`).
- Hard-coded `"cuda"` devices, `.cuda()` calls and `autocast(device_type="cuda")` use it.
- Triton (CUDA-only) is optional; the distance transform falls back to OpenCV.
- The fused bf16 matmul+activation kernel falls back to the unfused equivalent.
- RoPE tables follow the input's device; SAM 3.1's `init_state` gets only arguments it accepts.
- The tokenizer's vocabulary is read beside the module, not through `pkg_resources` (gone from setuptools 81 and later,
  which `services/uv.lock` holds), so `make setup` after `make sam3-setup` leaves it working (2026-10-19).
- The checkpoint is read once, mapped (2026-10-11). The predictor builder loaded the 3.5 GB file into the bare tracker
  first, where none of its `detector.`/`tracker.` keys matched (931 "missing keys" printed), then again into the whole
  model. On the leaf frame: built in 8.8-9.2 s instead of 10.5-12.0 s, peak footprint 5.8 GB instead of 9.1 GB, the
  same mask (`docs/design/2026-10-11-model-runtime.md`, R0).

Pinned commit: `2345a4ad109ac29c569da749c91d84f10dc08c40`. Set up with `make sam3-setup` (clones, applies the patch,
installs into `services/.venv`). The checkpoint (3.5 GB) downloads on first use with
`HF_TOKEN` from the keys file; access to `facebook/sam3.1` must be granted on Hugging Face.

Validation (`services/bench/sam31_*.py`, results in `services/bench/results/sam31/`): the final
load reports 0 missing / 0 unexpected tensors (all 1,623); "chair" on a room photo is
segmented and tracked; a stoma box exemplar gives score 0.96 locally and 0.96 on fal's
hosted SAM 3.1, so the port matches the official build.
