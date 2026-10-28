# Local test assets

No personal recordings are distributed. `manifest.json` starts with an empty real
asset list and no backup destination. Tests use generated fixtures or the cleared
example under `examples/`. Add your own recordings only to an ignored local asset
store; the source repository is not a user-data backup.

`MICROMIND_ASSETS` selects the store (default `~/MicroscopeMind`). Existing asset-management commands operate
on that local store. Optional corpus downloads retain their separate source terms.

Your own recordings are listed in the store's own `manifest.json` (`$MICROMIND_ASSETS/manifest.json`, never in git),
in the same format as this one: each recording's `id`, `file` (`footage/real/...`), `title`, `specimen`, `date`,
`seconds`, `bytes`, `sha256`, `microscope` and `optics`, and your `backup` destination if you have one. Everything
that reads this manifest reads that one over it: the camera bridge's footage list (its titles, groups and the
player's "Use its optics"), the app's library, `make assets` and the sim harness. A file listed in neither plays
under "Other", titled by its file name, with no optics. `make assets-add`, `make assets-adopt` and `make assets-set`
write only there.
