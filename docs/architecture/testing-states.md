# Testing states: test footage and the blind test

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

Two testing states sit on top of the live app and are independent of each other:
- **Test footage** (a developer setting in the studio's gear): the camera bridge plays a recording into the
  live feed instead of the phone. Everything downstream must behave exactly as on the
  microscope, so judgments made on footage carry over to live use. Footage, the public corpus
  and bench inputs live in one assets store outside every checkout (`~/MicroscopeMind`,
  `MICROMIND_ASSETS`), listed in `assets/manifest.json` and `services/bench/corpus/corpus.json`
  and documented in `assets/README.md`: real recordings are one of a kind (checksummed, backed up
  to the private GitHub release `test-assets`, added with `make assets-add`), the corpus is rebuilt
  from its manifest, and code reaches all of it only through `orchestrator.assets.store()`.
  `make assets` says what is there, missing or not backed up. The studio's Test footage page (`#footage`) shows them
  all as thumbnails (and adds new ones); the test footage menu shows each row's thumbnail. Media work
  never takes time from the live view: the bridge's poll stays small, the library is its own
  cached request, thumbnails and lengths are made one at a time at background priority
  (`footage_media.py`), and the sim scenario `media-load` fails if frame rate, delay or clicks get
  worse while thumbnails, the library and an upload run. While a recording plays, its name is the camera control and a
  player beside the tools has play/pause (`POST /feed/pause`: one picture held while frames keep
  coming, a slide at rest, so the app stays live) and a playhead (`POST /feed/seek`); a switch or seek takes about 0.5 s and
  never dims the view (sim `footage-switch`). Recordings carry their optics (objective, eyepiece,
  scale) in the manifest as data for tests; the app is never told them by the footage, and the
  player's "Use its optics" sets the objective as the objective menu does.
- **Blind A/B/C test** (the switch in the gear): several options answer the same click or
  question, unnamed, and you vote. It works on whatever feed is on and never assumes
  what is in view.
