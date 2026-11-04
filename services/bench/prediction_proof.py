"""Exercise conditional count generation on a synthetic unseen target in the pinned matrix runtime."""
import json
import sys
import tempfile
from pathlib import Path

import anndata
import numpy as np
import pandas as pd
from scipy import sparse

from scientific_compute.engine import predict


def prove(root, params):
    genes = ["g1", "g2"]
    inputs = {key: root / filename for key, filename in
              (("controls", "controls.h5ad"), ("panel", "panel.json"), ("model", "model.npz"))}
    controls = anndata.AnnData(sparse.csr_matrix([[100, 100]] * 4, dtype=np.float32),
                               obs=pd.DataFrame({"context": ["x"] * 4, "target_gene": ["non-targeting"] * 4},
                                                index=["c1", "c2", "c3", "c4"]),
                               var=pd.DataFrame(index=genes))
    controls.write_h5ad(inputs["controls"])
    inputs["panel"].write_text(json.dumps(["unseen"]))
    np.savez_compressed(inputs["model"], format="response-model-v1", genes=genes, targets=["a", "b"],
                        effects=[[3., -3.], [1., -1.]], metadata="{}")

    totals = []
    for prior in ("zero-effect", "mean-fitted-target-effect"):
        folder = root / prior
        folder.mkdir()
        output, summary = predict(inputs, {**params, "unseen_target_prior": prior}, folder, lambda: None)
        data = anndata.read_h5ad(output)
        if (data.shape != (params["cells_per_target"], 2) or list(data.var_names) != genes
                or set(data.obs["target_gene"]) != {"unseen"} or summary["unseenTargets"] != ["unseen"]
                or summary["unseenTargetPrior"] != prior or not sparse.issparse(data.X)):
            raise ValueError("Prediction axes, sparse format or unseen-target attribution changed")
        counts = data.X.data
        if not (np.isfinite(counts) & (counts >= 0) & (counts == np.floor(counts))).all():
            raise ValueError("Prediction contains invalid counts")
        totals.append(data.X[:, 0].sum())
    if totals[1] <= totals[0]:
        raise ValueError("The shared fitted effect did not increase the first gene's count on the fixed fixture")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temp:
        prove(Path(temp), json.loads(sys.argv[1]))
    print("Both unseen-target priors emitted sparse counts with the expected directional difference on a synthetic panel.")
