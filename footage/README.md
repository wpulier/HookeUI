# Test footage has moved

Test footage, the public test corpus and bench inputs now live in one store outside every
checkout, `~/MicroscopeMind` (`MICROMIND_ASSETS` moves it), so every checkout and worktree sees
the same files. See [`assets/README.md`](../assets/README.md).

    make assets                 what is there, missing or not backed up
    make assets-migrate         move this checkout's old footage/ and bench data into the store
    make assets-add FILE= SPECIMEN=   add a real microscope recording

This folder is kept only for this note; videos left here still play from an old checkout's
bridge until they are migrated.
