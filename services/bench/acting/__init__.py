"""The acting bench: does the app do what was asked, in order, and say so only when it is so?

A case is a starting board, what the user says or types (with the gaps between the pieces),
how the models answer, and what the board must look like. `world.World` runs the real brain,
spoken-request handling and tools over a stand-in camera, GPU and models; `run.run_case`
scores it: the board at the end, every time the user was told something was done against the
board at that moment, how soon each thing happened, and duplicates. The deterministic cases
run in `make check` (tests/test_acting.py); `python -m bench.acting.run` prints the table.
"""
