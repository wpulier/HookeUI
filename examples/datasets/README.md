# Published datasets, as manifests

A `dataset-manifest-v1` file (`contract/agent/dataset-manifest.schema.json`) names a published dataset for
`dataset.acquire` (`services/orchestrator/door/AGENTS.md`, "Data in"): its licence, where each file is, its mirror,
and the sizes and checksums its publisher states. Hooke checks the licence and free space, downloads (resuming when
retried), verifies and keeps each file in the Project with where it came from.

`vcc-2026/` holds the three reference sources of the VCC 2026 final entry (5 Nov 2026). Each file's `sha256` is pinned
from the copy downloaded that night, so acquiring them again proves the same bytes:

| Manifest | Source | Files | Licence | Notes |
|---|---|---|---|---|
| `nadig-2025-gse264667.json` | GEO FTP (https) | HepG2 and Jurkat single-cell h5ad, 15.0 GB | NCBI-public | GEO states no checksum: the size and the night's sha256 are pinned |
| `replogle-2022-k562-essential.json` | figshare+ article 20029387 | K562 essential single-cell h5ad, 10.7 GB | CC-BY-4.0 | figshare states size, md5 and licence |
| `depmap-24q4.json` | depmap.org, mirrored by figshare+ article 27993248 | four 24Q4 CSVs, 0.94 GB | CC-BY-4.0 | depmap.org answers automated clients with a verification page, so each file comes from its figshare+ mirror and the receipt says why |

On 6 Nov 2026 all three were acquired again from the real servers through `dataset_acquire.Acquisition`: every file's
sha256 matched the night's (`docs/architecture/scientific-matrices.md` records the checks run on them).
