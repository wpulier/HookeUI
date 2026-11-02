# Hooke Project format v2: remotes, publications and starting from a Project

Status: **open specification, version 2** (ADR 0028, accepted 2026-11-02). Version 2 adds remote layout to the portable Project
folder of ADR 0027 (`project.json` format 1, `storage: hooke-project-v1`), which is unchanged. Anyone may implement
it; nothing here needs Hooke Cloud or GitHub.

## 1. The sealed snapshot is the unit

Every remote operation starts from a sealed snapshot made by `project_folder_copy` (`copy_snapshot` /
`copy_owned_snapshot`). Its `snapshot.json` (`hooke-project-folder-snapshot-v1`) lists every directory and every file
of `project.json`, `README.md` and the five sections with `path`, `bytes` and `sha256`. A remote never carries a file
that the snapshot does not list, and never omits one it lists (bulk objects may be *absent locally*, see 6).

## 2. Two layers

| Layer | Members | Travels to |
|---|---|---|
| Bulk | `artifacts/records/p<32 hex>/(files|arrays)/<64 hex>.bin`, and any other member larger than 1 MiB | object storage |
| Record | every other member | the record remote (a folder, a Git host) |

Record members larger than 5 MiB are refused by Git-host backends (they belong in bulk; the 1 MiB rule already sends
them there). Classification is by path and size only, never by guessed meaning.

## 3. Remote tree

A record remote stores *commits*. Each commit is a tree of paths to blobs:

```
README.md                    a short notice, or the publication README (8)
LICENSE, CITATION.cff        publications only
ro-crate-metadata.json       publications only
.hooke/snapshot.json         the exact bytes of the sealed snapshot manifest
.hooke/bulk.json             hooke-bulk-v1 (4)
project/<snapshot path>      every record-layer member, at its snapshot path
```

The commit message ends with trailers:

```
Hooke-Snapshot: <sha256 of .hooke/snapshot.json bytes>
Hooke-Origin: <project origin>
Hooke-Format: 2
```

A commit whose trailer is missing, or whose `.hooke/snapshot.json` does not hash to its trailer, or whose record
blobs do not match the snapshot's hashes, is reported as **changed outside Hooke** and is never imported.

## 4. `hooke-bulk.json`

```json
{"format": "hooke-bulk-v1",
 "origin": "<32 hex>",
 "snapshotSha256": "<64 hex>",
 "entries": [{"path": "artifacts/records/p…/files/<sha>.bin", "bytes": 1048576, "sha256": "<sha>"}],
 "stores": [{"kind": "folder|s3|hooke-cloud|zenodo", "base": "<location>", "visibility": "public|private"}]}
```

`entries` lists exactly the bulk-layer members of the snapshot, sorted by path. The object key is the SHA-256; one
object serves every path with the same content. `stores` is advisory: a reader may fetch an object from any store
and must verify its SHA-256 and size before placing it.

## 5. Object stores

A folder or S3-compatible store keeps objects at `objects/<sha[0:2]>/<sha256>`. Hooke Cloud keeps them at
`projects/<owner>/<origin>/objects/<sha256>` behind signed links. Objects are write-once; an existing object with the
same name is success, never an overwrite.

## 6. Pushing

1. Seal a snapshot under the writer lease; `verify_records` passes.
2. Classify; scan the record layer for secrets (a finding stops the push and names the file).
3. Upload every bulk object the store lacks; verify each upload.
4. Write one commit (parent = the last commit this host pushed there, or none for a new remote).
5. Move the remote's pointer by compare-and-swap from the expected old commit. If the remote moved on elsewhere,
   refuse: "This project changed in two places." Never force.

## 7. Fetching

A reader validates the commit before writing anything: every path is relative, under `project/` or one of the root
names above, has no `..`, NUL or control characters, and is not a link or special file; counts, sizes and JSON depth
are bounded. It writes the record members and fetches the bulk members into a new folder marked incomplete, writes
`snapshot.json`, verifies the whole folder (`verify_snapshot`, which re-runs `verify_records`), then clears the
marker. The result is a sealed snapshot, which `restore_snapshot` turns into a working copy.

## 8. Publications

A publication is a filtered sealed snapshot of the Project:

- `project.json`, the Project's README, every accepted document under `artifacts/records/`, and the chosen artifact
  sections (by default all except `artifacts/research`, which holds research transcripts).
- The Project's own session journal is replaced by its origin-only form (`ownership.journal_bytes`), which keeps the
  identity verifiable and carries no conversation. Connector operations and restoration receipts are left out.
- It keeps the Project's origin and every revision digest.

Before sending, the owner reviews the exact members and their sizes. A secret finding blocks; a home-directory path
or metadata finding is shown for the owner to resolve. The owner signs off on rights, the licence (CC BY 4.0
preselected) and the absence of identifiable human data; the review digest (SHA-256 over the canonical review) binds
what is sent. The first publication is a parentless commit in its own destination; later publications of the same
Project chain onto it. Publishing never changes the visibility of a backup.

## 9. Starting from a publication

Starting from fetches a publication (7), restores a working copy (`restore_snapshot`), and appends one journal event:

```json
{"type": "project-started-from", "origin": "<publication origin>", "snapshotSha256": "<64 hex>",
 "source": {"kind": "folder|github", "location": "<url or path>", "commit": "<commit id>"}}
```

It inherits no grants or registry entries. **Update** fetches a newer publication and brings each document in
through the owner-aware import: a document is added when absent or extended when the local history is a prefix of
the published one; anything else is refused with nothing changed. A fully independent copy (its own origin) is the
next stage (ADR 0028, decision 6).

## 10. Reproduction levels

Receipts record device, model version and seed. A re-run is reported at three levels: inputs byte-identical;
deterministic CPU steps reproduce their hashes; GPU model results match within a stated tolerance.
