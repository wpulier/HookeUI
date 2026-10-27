# Local test assets

No personal recordings are distributed. `manifest.json` starts with an empty real
asset list and no backup destination. Tests use generated fixtures or the cleared
example under `examples/`. Add your own recordings only to an ignored local asset
store; the source repository is not a user-data backup.

`MICROMIND_ASSETS` selects the store. Existing asset-management commands operate
on that local store. Optional corpus downloads retain their separate source terms.
