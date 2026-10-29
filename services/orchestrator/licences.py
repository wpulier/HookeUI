"""Recorded component terms and the application's edition policy (ADR 0005).

Each component has one availability tier: open, terms or noncommercial. The commercial
edition permits open and terms; the research edition permits all three. Tiers are policy
groups, not licence grants or findings that a use is legally suitable. Integration source,
fetched software, trained weights, data and services retain their separate applicable terms.

MICROMIND_EDITION selects research when unset or empty, including direct app and GPU launches.
Explicit commercial and invalid nonempty values use the commercial gate. The value is read
from the environment, never from settings, and inherited by GPU workers (the user's decision
of 2026-10-08, ADR 0005).

Options declare their components (Option.uses, orchestrator/options.py); the edition permits
an option only when it permits every component's tier. Optional adapters in
services/noncommercial/ are reached through adapter() here. Their folder's NOTICE explains
the source/weights/data distinction; its name does not impose a blanket non-commercial licence.
"""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass

TIERS = ("open", "terms", "noncommercial")  # edition-policy order, not a licence classification
EDITIONS = {"commercial": {"open", "terms"}, "research": set(TIERS)}
DEFAULT_EDITION = "research"
# Bundled browser components; independent of GPU operations and model options.
STUDIO_COMPONENTS = {"react": ("react", "react-dom"), "react-flow": ("@xyflow/react",)}


@dataclass(frozen=True)
class Component:
    id: str
    what: str
    licence: str
    url: str
    tier: str
    kind: str  # "weights", "code" or "service"
    adapter: str = ""  # module outside the core that runs it ("noncommercial.cellpose_cells")
    derived_from: str = ""  # for code we wrote from someone else's paper or code, or weights made from another component's


COMPONENTS = {
    c.id: c
    for c in [
        Component("react", "React and React DOM (isolated workflow editor)", "MIT", "https://github.com/facebook/react", "open", "code"),
        Component("react-flow", "React Flow (workflow connections and step canvas)", "MIT", "https://github.com/xyflow/xyflow", "open", "code"),
        Component("pint", "Pint 0.26.1 (scientific unit-name identity; no implicit conversion)",
                  "BSD-3-Clause", "https://github.com/hgrecco/pint", "open", "code"),
        Component("gemini-api", "Google Gemini API (vision, Sight, fast pass, research, pictures)",
                  "Gemini API terms of service", "https://ai.google.dev/gemini-api/terms", "terms", "service"),
        Component("openai-api", "OpenAI API (GPT-Live voice, GPT vision, research, GPT Image)",
                  "OpenAI services agreement", "https://openai.com/policies/services-agreement", "terms", "service"),
        Component("anthropic-api", "Anthropic API (Claude writes lessons; Claude vision, brain and fast pass when chosen)",
                  "Anthropic commercial terms", "https://www.anthropic.com/legal/commercial-terms", "terms", "service"),
        Component("fal-api", "fal.ai hosted inference (SAM 3.1 in the cloud)",
                  "fal terms of service", "https://fal.ai/terms", "terms", "service"),
        Component("wikimedia-commons", "Wikimedia Commons search API (a catalogue searched beside the web for reference photos)",
                  "Wikimedia API terms of use", "https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use", "open", "service"),
        Component("edgetam", "EdgeTAM outline model (code and weights)",
                  "Apache-2.0", "https://github.com/facebookresearch/EdgeTAM", "open", "weights"),
        Component("sam2.1", "SAM 2.1 outline model (code and weights)",
                  "Apache-2.0", "https://github.com/facebookresearch/sam2", "open", "weights"),
        Component("sam3.1", "SAM 3.1 outline model (code and weights)",
                  "SAM License (commercial use allowed, with conditions)",
                  "https://github.com/facebookresearch/sam3/blob/main/LICENSE", "terms", "weights"),
        Component("dinov2-small", "DINOv2 small image embeddings (code and weights; labels the user teaches)",
                  "Apache-2.0", "https://huggingface.co/facebook/dinov2-small", "open", "weights"),
        Component("bioclip-2", "BioCLIP 2 image embeddings (open_clip code and weights; option EMBED)",
                  "MIT", "https://huggingface.co/imageomics/bioclip-2", "open", "weights"),
        Component("dinov3-small", "DINOv3 ViT-S/16 image embeddings (weights; option EMBED)",
                  "DINOv3 License", "https://huggingface.co/facebook/dinov3-vits16-pretrain-lvd1689m", "terms", "weights"),
        # Non-commercial until the terms of its weights' training data are known (upstream states none; Cellpose's
        # were non-commercial), and Gurobi, which its install brings, is proprietary (the M3.5 review).
        Component("trackastra", "Trackastra cell linking (code and its general_2d weights; operation link)",
                  "code BSD-3-Clause; the general_2d weights' training data terms are not stated upstream",
                  "https://github.com/weigertlab/trackastra", "noncommercial", "weights",
                  adapter="noncommercial.trackastra_link"),
        Component("brainreg", "brainreg (BrainGlobe's registration of whole brains to an atlas; in its own Python)",
                  "BSD-3-Clause", "https://github.com/brainglobe/brainreg", "open", "code"),
        Component("niftyreg", "NiftyReg (the registration programs brainreg runs; conda-forge's Apple Silicon build)",
                  "BSD-3-Clause; its bundled nifticlib (reg-io/nifti) public domain", "https://github.com/KCL-BMEIS/niftyreg",
                  "open", "code"),
        # The libraries NiftyReg's build loads, pinned beside it in brainreg's native part; each package's own licence
        # files and provenance are kept in catalog/notices/conda-forge/ (component accounting, not a legal clearance).
        Component("libpng", "libpng (the PNG library NiftyReg loads; conda-forge's Apple Silicon build)",
                  "zlib-acknowledgement (PNG Reference Library License version 2)",
                  "https://www.libpng.org/pub/png/libpng.html", "open", "code"),
        Component("zlib", "zlib (the compression library NiftyReg and libpng load; conda-forge's Apple Silicon build)",
                  "Zlib", "https://zlib.net/", "open", "code"),
        Component("libcxx", "libc++ (LLVM's C++ standard library NiftyReg loads; conda-forge's Apple Silicon build)",
                  "Apache-2.0 WITH LLVM-exception", "https://github.com/llvm/llvm-project", "open", "code"),
        Component("allen-mouse-atlas", "Allen Mouse Brain Atlas (as BrainGlobe publishes it)",
                  "Allen Institute Terms of Use: research and other noncommercial use, with citation",
                  "https://alleninstitute.org/terms-of-use/", "noncommercial", "weights"),
        Component("basic-bright-objects", "Bright objects, the basic local method (the first example's: Gaussian smoothing, "
                  "Otsu's threshold and connected regions through OpenCV, in the app's own process; no model weights)",
                  "Apache-2.0 (OpenCV); the method's own code follows the application's licence",
                  "https://github.com/opencv/opencv/blob/4.x/LICENSE", "open", "code"),
        Component("fiji", "Fiji (ImageJ with its plugins and a Zulu JDK 21; the app's managed copy, run headless through "
                  "reviewed bindings)", "Mixed component terms: Fiji wrapper/lib declare GPL-3.0-or-later; inspected ImageJ2/SciJava "
                  "modules declare BSD-2-Clause; ImageJ 1.x is declared public domain; plugins have individual "
                  "BSD/GPL and other terms; the bundled Zulu JDK has GPL-2.0, component-specific "
                  "Classpath/assembly exceptions and additional notices", "https://fiji.sc", "open", "code"),
        Component("gurobipy", "Gurobi optimizer (installed with Trackastra through motile[gurobi]; never called here: "
                  "linking is greedy)", "Gurobi EULA (proprietary; the pip package carries a size-limited licence)",
                  "https://www.gurobi.com/", "noncommercial", "code"),
        Component("medsiglip", "MedSigLIP 448 medical image-text model (weights; zero-shot names when naming a click)",
                  "Health AI Developer Foundations terms of use (gated download; terms accepted on Hugging Face)",
                  "https://developers.google.com/health-ai-developer-foundations/terms", "terms", "weights"),
        Component("molmo2-4b", "Molmo 2 4B pointing model (code and weights; pointing \"molmo\")",
                  "Apache-2.0", "https://huggingface.co/allenai/Molmo2-4B", "open", "weights"),
        Component("cellpose-sam", "Cellpose-SAM cell segmentation (weights)",
                  "code BSD-3-Clause; weights trained on data licensed for non-commercial use",
                  "https://github.com/MouseLand/cellpose", "noncommercial", "weights",
                  adapter="noncommercial.cellpose_cells"),
        # Weights fine-tuned from Cellpose-SAM's on the user's own corrected cells (K4, bench/finetune_cells.py,
        # orchestrator/finetuned.py): the app keeps the same policy tier; this does not settle derived-weight rights.
        Component("cellpose-sam-tuned", "Cellpose-SAM fine-tuned on your corrected cells (weights made here)",
                  "code BSD-3-Clause; weights fine-tuned from Cellpose-SAM's, which were trained on data licensed for "
                  "non-commercial use", "https://github.com/MouseLand/cellpose", "noncommercial", "weights",
                  adapter="noncommercial.cellpose_cells", derived_from="cellpose-sam"),
        # A Cellpose model file the user added (orchestrator/mine.py; P5 of the open catalog): Cellpose's own code runs it,
        # and its weights are its maker's, under the licence they give (kept on its entry as `licence_note`). Fine-tunes
        # of Cellpose-SAM remain in the same application policy tier; the maker's terms and base-weight rights are separate.
        Component("cellpose-sam-yours", "A Cellpose model you added (its weights are its maker's)",
                  "code BSD-3-Clause; the weights under their maker's licence (on the model's card), most fine-tuned from "
                  "Cellpose-SAM's, which were trained on data licensed for non-commercial use",
                  "https://github.com/MouseLand/cellpose", "noncommercial", "weights",
                  adapter="noncommercial.cellpose_cells", derived_from="cellpose-sam"),
        # The weights' own licence is CC-BY-4.0 (bioimage.io "diplomatic-bug" 1.2), but they were
        # trained on LIVECell (CC BY-NC 4.0), TissueNet and DynamicNuclearNet (non-commercial) and
        # Cellpose's dataset (non-commercial) among 14 sets (micro-sam doc/datasets/lm_v4.md). The app keeps
        # this in the same policy tier as Cellpose-SAM without determining those declarations' legal effect on weights.
        Component("micro-sam-lm", "micro-sam light-microscopy SAM, vit_b_lm (cells and click outlines)",
                  "code MIT; weights CC-BY-4.0, trained on data licensed for non-commercial use "
                  "(LIVECell CC BY-NC 4.0, TissueNet, Cellpose's dataset)",
                  "https://github.com/computational-cell-analytics/micro-sam", "noncommercial", "weights",
                  adapter="noncommercial.microsam_cells"),
    ]
}


class NotInEdition(RuntimeError):
    """A component this edition may not use, or whose code is not installed."""


def edition() -> str:
    value = os.environ.get("MICROMIND_EDITION", "")
    if not value:
        return DEFAULT_EDITION
    value = value.strip().lower()
    return value if value in EDITIONS else "commercial"  # an invalid override never grants research components


def component(cid: str, environ=os.environ) -> Component:
    """A component by id: the app's table, else one a catalog entry declares (`component` in its manifest: a
    contributed model's own licence, catalog/README.md)."""
    found = COMPONENTS.get(cid)
    if found is not None:
        return found
    from gpu_worker import manifests as catalog

    m = catalog.get(cid) or next((x for x in catalog.mine(environ).values() if x.id == cid), None)
    if m is not None and m.runner == "python-env":
        from .env_runner import operational

        m = operational(m, environ)  # the active copy's rights, never an uninstalled update's declaration
    if m is not None and m.component:
        return Component(cid, m.component["what"], m.component["licence"], m.component["url"], m.component["tier"], "code")
    raise KeyError(cid)


def tier(uses) -> str:
    """The strictest tier among `uses` (component ids); "open" for none."""
    tiers = [component(c).tier for c in uses]
    return max(tiers, key=TIERS.index) if tiers else "open"


def model_uses(m) -> tuple[str, ...]:
    """A model's components include the catalog data it reads, even after that data has been downloaded."""
    from gpu_worker import manifests as catalog

    components = list(m.licence)
    for need in m.needs_to_run:
        kind, _, key = need.partition(":")
        data = catalog.get(key) if kind == "data" else None
        if data is not None:
            components.extend(data.licence)
    return tuple(dict.fromkeys(components))


def permitted(uses, in_edition: str | None = None, *, environ=os.environ) -> bool:
    allowed = EDITIONS[in_edition or edition()]
    return all(component(c, environ).tier in allowed for c in uses)


def why_not(uses, in_edition: str | None = None, *, environ=os.environ) -> str:
    """Why `uses` is not permitted, in words for the person ("" when it is)."""
    allowed = EDITIONS[in_edition or edition()]
    blocked = [component(c, environ) for c in uses if component(c, environ).tier not in allowed]
    if not blocked:
        return ""
    names = ", ".join(c.what.split(" (")[0] for c in blocked)
    return f"not in the {in_edition or edition()} edition: non-commercial component policy excludes {names}"


def licence_text(uses) -> str:
    return "; ".join(f"{component(c).what.split(' (')[0]}: {component(c).licence}" for c in uses)


def adapter(component: str):
    """The module that runs a component kept outside the core. The only way core code
    reaches services/noncommercial/ (or a commercial arm); raises NotInEdition when this
    edition may not use it or its code is not installed (a copy without that folder)."""
    entry = COMPONENTS[component]
    if not permitted([component]):
        raise NotInEdition(why_not([component]))
    if not entry.adapter:
        raise NotInEdition(f"{component} has no adapter")
    try:
        return importlib.import_module(entry.adapter)
    except ModuleNotFoundError as exc:
        raise NotInEdition(f"{entry.what.split(' (')[0]} is not installed in this copy ({exc.name})") from exc
