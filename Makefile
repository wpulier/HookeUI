CAMERA_HOST ?= 127.0.0.1
CAMERA_PORT ?= 8765
CAMERA_TARGET ?= iPhoneWild Camera
CAMERA_URL := http://$(CAMERA_HOST):$(CAMERA_PORT)
CV_HOST ?= 127.0.0.1
CV_PORT ?= 8766
CV_DIR := experiments/iphone-cv-consumer
CV_URL := http://$(CV_HOST):$(CV_PORT)
CV_VENV := $(CV_DIR)/.venv
CV_PYTHON := $(CV_VENV)/bin/python
CV_DEPS_STAMP := $(CV_VENV)/.deps-installed
CV_OUTPUT_DIR := $(CV_DIR)/output
CAMERA_BRIDGE_LOG := $(CV_OUTPUT_DIR)/camera-bridge.log

# A port counts as ours only when its /api/health identifies the service. Another app
# listening on the same port is reported, never reused or killed.
IS_CAMERA_BRIDGE = curl -s -m 2 "$(CAMERA_URL)/api/health" 2>/dev/null | grep -q '"targetDevice"'
IS_CV_SERVICE = curl -s -m 2 "$(CV_URL)/api/health" 2>/dev/null | grep -q '"service": "iphone-cv-consumer"'

.DEFAULT_GOAL := help

# Recording the end-to-end scenarios play as the camera.
# Empty: the harness takes the leaf epidermis recording from the assets store (orchestrator.assets), which
# every checkout and worktree reaches; set it to a path from the repo root to play another recording.
SIM_VIDEO ?=
# Optional absolute path to private test metadata, kept outside the distributed source.
SIM_MANIFEST ?=
# CAMERA_SOURCE=srt://0.0.0.0:9710?mode=listener starts on Blackmagic with that listener; empty: the camera's last
# choice (camera-source.json in the assets store), else No camera (a first run: pictures and videos only).
CAMERA_SOURCE ?=
STUDIO := CAMERA_PORT=$(CAMERA_PORT) CAMERA_TARGET="$(CAMERA_TARGET)" $(if $(CAMERA_SOURCE),MICROMIND_CAMERA_SOURCE="$(CAMERA_SOURCE)") ./scripts/studio.sh

.PHONY: release app-build doctor catalog-install catalog-update channel-add catalog-ci fiji-inventory model-add catalog-check catalog-pr catalog-download catalog-adopt catalog-pin catalog-proof capability-check capability-check-cloud object-tracking-bench tracking-bench demo-start demo-stop demo-status licences export-corrections bundle replay-bench finetune-cells naming-fetch naming-bench acting-bench corpus corpus-tours assets assets-fetch assets-add assets-adopt assets-set assets-backup assets-migrate dataset-import dataset-check saves saves-move logs-prune voice-report day-report help setup start dev stop status save-logs new-session clear-session test test-timing check sim-test keys-check sam3-setup trackastra-setup arena-report \
	camera camera-page camera-sim camera-open camera-stop camera-health camera-preflight camera-reconnect camera-source \
	camera-controls camera-record-start camera-record-status camera-record-stop \
	cv-setup cv cv-open cv-stop cv-health cv-smoke camera-cv camera-cv-stop

help:
	@echo "HookeUI"
	@echo "  make setup         Once: install the Python services and the web app"
	@echo "  make start         Start the live microscope app: http://127.0.0.1:8770/"
	@echo "                     (stops any HookeUI already running first: one copy only)"
	@echo "                     (test footage from footage/ is picked in the studio's camera menu)"
	@echo "  make dev           The same, plus the web app with hot reload at http://127.0.0.1:5180/"
	@echo "  make stop          Stop everything of HookeUI, on any port (also Ctrl-C or closing"
	@echo "                     the terminal that ran make start)"
	@echo "  make status        Show what is running and where"
	@echo "  make save-logs     Save today's logs into one folder (NOTE=\"what went wrong\")"
	@echo "  make new-session   Start a new session while the app runs (NAME= names it; clear-session is its old name)"
	@echo "  make test-python PY_TESTS=tests/test_name.py   Focused Python regressions (selector required)"
	@echo "  make test-studio STUDIO_TESTS=src/name.test.ts Focused Studio regressions (selector required)"
	@echo "  make typecheck     Studio type check"
	@echo "  make test          Full candidate Python/Studio/native stage; Python on every core"
	@echo "                     (JOBS=8 for 8 workers, SERIAL=1 for one process)"
	@echo "  make test-timing   The tests' wall-clock bounds (\"returns at once\"), one test at a time; not in the gate"
	@echo "  make check-public  Contributor check with generated inputs; no private assets, keys or models"
	@echo "  make check         make test + all simulations: frozen integration/release gate"
	@echo "  make release       Show explicit dev/Beta and accepted main/Stable release instructions"
	@echo "  make app-build VERSION=0.1.3  Build the Mac app only (RELEASE_CHANNEL=beta: HookeUI Beta); publishes nothing"
	@echo "  make sim-test SIM_ONLY=actions  Affected simulated-camera scenarios; omit SIM_ONLY for all"
	@echo ""
	@echo "  make doctor        Every model in the catalog: ready, what it needs here, and how (nothing loaded or downloaded)"
	@echo "  make catalog-download ID=  Download a model's pinned files, checked, into ~/MicroscopeMind/catalog/models"
	@echo "  make catalog-adopt  Move what this Mac already downloaded into the models folder (clones; ID= for one)"
	@echo "  make catalog-pin ID=  Write a Hugging Face model's files (sizes, hashes) into its manifest (ONLY=\"*.pt\")"
	@echo "  make catalog-proof  Run every model's operations from its pinned files alone, offline: passed, failed or missing (ONLY=edgetam,trackastra; BESIDE_LIVE=1)"
	@echo "  make model-add FROM=  Add a model of your own (a Hugging Face link or a file's path); Turn it on in the app"
	@echo "  make catalog-check  Say every catalog entry that can't be used as written (yours too)"
	@echo "  make fiji-inventory FIJI_DB=  Write every Fiji extension listed on imagej.net, with how each installs, for review"
	@echo "  make catalog-pr ID=  Prepare your entry, proof and diff locally for review before a pull request"
	@echo "  make keys-check    Check every API key in ~/Desktop/micromind.txt (values never shown)"
	@echo "  make sam3-setup    Install SAM 3.1 (Meta's code + the Apple Silicon patch)"
	@echo "  make trackastra-setup  Install Trackastra into the app's own Python (older way: its card's Install makes its own Python)"
	@echo "  make arena-report  Write the blind A/B/C scores to docs/bench/arena.md"
	@echo "  make licences      Write what each component is licensed for to docs/licences.md"
	@echo "  make export-corrections  Relabels and corrections of segmented cells, with their pictures and masks, appended to logs/kept/corrections.jsonl"
	@echo "  make bundle OUT= (DATASET= | FROM= TO= | DAY=)   A session bundle: a dataset or window with its evidence,"
	@echo "                     executions, outcomes, corrections and journal, in a new folder (read-only on its sources)"
	@echo "  make replay-bench BUNDLE=dir[,dir] [IMPL=micro-sam-lm,...]   Run cell finders again on past sessions and"
	@echo "                     compare with what happened (docs/bench/replay.md)"
	@echo "  make finetune-cells [EPOCHS=] [HOLD_OUT=] [DRY=1]   Fine-tune Cellpose-SAM on your corrected cells (an explicit job)"
	@echo "                     Both wait for the app (make stop first): each puts another Cellpose-SAM (1.2 GB) on the GPU;"
	@echo "                     BESIDE_LIVE=1 runs them anyway, which may slow the live view's clicks"
	@echo "  make naming-fetch  List (FETCH=1: fetch) the labelled pictures for the naming bench"
	@echo "  make naming-bench  Name them through the app's prompts and score (cloud keys; OPTIONS=, LIMIT=, REPEATS=)"
	@echo "  make demo-start    Record the camera and the whole screen (with the mic) for a demo; demo-stop gathers logs and reports"
	@echo "  make capability-clip ID= FROM= START= DUR=   A preview for a capability's card (docs/capabilities/media/)"
	@echo "  make verdicts      Put your verdicts on capabilities into git (a PR from a temporary worktree)"
	@echo "  make capability-check   Run every model on this Mac once on a real frame; kept for the Capabilities page"
	@echo "                     (waits for the app; BESIDE_LIVE=1, DOWNLOAD=1, ONLY=role:option,...; exit 1 when one is broken)"
	@echo "  make capability-check-cloud   The same for the cloud models (keys; cost said at the end; VIDEO=1 adds Veo)"
	@echo "  make acting-bench  Does the app do what was asked, in order, and say so only when done? (CASE=)"
	@echo "  make tracking-bench  Do outlines stay on their thing? Eyepiece tours with truth: textured, sparse, swimmers"
	@echo "  make object-tracking-bench  Does an outline follow a cell that swims on its own? (option TRACK)"
	@echo "  make assets        Test assets in ~/MicroscopeMind: what is here, missing or changed (VERIFY=1, REMOTE=1)"
	@echo "  make assets-fetch  Get the real recordings back from GitHub (CORPUS=1: also download and render the corpus)"
	@echo "  make assets-add FILE= SPECIMEN=   Add a real microscope recording (TITLE=, NOTES=, NOT_MICROSCOPE=1)"
	@echo "  make assets-adopt  Move recordings added in the studio's Assets view into the manifest"
	@echo "  make assets-set ID= OBJECTIVE= EYEPIECE= FIELD= UM_PER_PX= MICROSCOPE=   What a recording was filmed with"
	@echo "  make assets-backup Upload real recordings the GitHub release test-assets lacks"
	@echo "  make dataset-check     Check every dataset against docs/datasets.md (or ID=ds-...)"
	@echo "  make saves         Where each kind of save is (datasets, logs, assets) and how big"
	@echo "  make saves-move    Move the saves into ~/MicroscopeMind's three homes (lists first; APPLY=1 moves)"
	@echo "  make logs-prune    Performance logs older than DAYS=14 (lists first; APPLY=1 removes)"
	@echo "  make dataset-import FROM= TO= [VIDEO= VIDEO_START= SCREEN= SCREEN_START= NAME= ARGS=]"
	@echo "                     Rebuild a dataset afterwards from the logs (marked reconstructed)"
	@echo "  make assets-migrate  Move an old checkout's footage/ and bench data into the store (FROM=, DRY=1)"
	@echo "  make corpus        List the test corpus (licences, sizes); FETCH=1 downloads it (ONLY=, INTERNAL=1)"
	@echo "  make corpus-tours  Render what was fetched into eyepiece footage with per-frame truth (ONLY=)"
	@echo ""
	@echo "Camera bridge (live hardware checks):"
	@echo "  make camera-reconnect    Hard reconnect: reset Continuity if needed, then fresh frames"
	@echo "  make camera-source KIND=continuity|blackmagic|other|none  Live source: the iPhone, Blackmagic (it picks the cable"
	@echo "                                                  or Wi-Fi), another camera, or none (pictures and videos only)"
	@echo "  make camera-open         Open the camera console"
	@echo "  make camera-health       Print bridge health"
	@echo "  make camera-preflight    Print the AVFoundation device scan"
	@echo "  make camera-controls     Probe focus/exposure/white-balance support"
	@echo "  make camera-record-start Start MP4 recording (camera-record-stop to stop)"
	@echo "  make camera              Start only the bridge"
	@echo "  make camera-stop         Stop only the bridge"
	@echo ""
	@echo "Original edge-detection pipeline (kept as a baseline):"
	@echo "  make camera-cv           Start the bridge if needed, then the edge dashboard on :8766"
	@echo "  make camera-cv-stop      Stop both (cv-setup, cv, cv-open, cv-health, cv-smoke, cv-stop)"

setup:
	python3 scripts/studio_setup.py
	cd services && uv sync --locked --python 3.13 --all-groups --inexact  # inexact: explicitly installed adapters stay
	cd apps/studio && npm ci

start:
	@$(STUDIO) start

dev:
	@$(STUDIO) start --dev

stop:
	@$(STUDIO) stop

status:
	@$(STUDIO) status

release:
	@test -n "$(VERSION)" || { echo "make release VERSION=0.1.1 (packaging/AGENTS.md)"; exit 2; }
	packaging/macos/release.sh $(VERSION)

# The Mac app built and NOT published (packaging/macos/README.md "Channels"): RELEASE_CHANNEL=beta builds HookeUI Beta, which
# installs beside HookeUI and keeps its own data, keys, downloads and ports. Nothing is uploaded or offered to anyone.
app-build:
	@test -n "$(VERSION)" || { echo "make app-build VERSION=0.1.3 [RELEASE_CHANNEL=beta] (packaging/macos/README.md)"; exit 2; }
	RELEASE_CHANNEL="$(or $(RELEASE_CHANNEL),stable)" packaging/macos/build.sh $(VERSION)

demo-start:
	@CAMERA_URL="$(CAMERA_URL)" ./scripts/demo.sh start

demo-stop:
	@CAMERA_URL="$(CAMERA_URL)" ./scripts/demo.sh stop

demo-status:
	@CAMERA_URL="$(CAMERA_URL)" ./scripts/demo.sh status

save-logs:
	@NOTE="$(NOTE)" ./scripts/save-logs.sh

# A new session while the app runs (ADR 0017: a session is the project): the live view is cleared and one empty
# conversation opens; the session left is kept as it is. NAME= names it. With the app stopped nothing is written:
# the app opens in the last session used.
new-session:
	@if $(STUDIO) status | grep -q "orchestrator    up"; then \
		curl -s -X POST -H "X-MicroscopeMind: new-session" -H "Content-Type: application/json" \
			-d "$$(NAME="$(NAME)" python3 -c 'import json,os; print(json.dumps({"name": os.environ.get("NAME", "")}))')" \
			"http://127.0.0.1:$${ORCH_PORT:-8770}/api/session/new" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(("New session " + (repr(r["name"]) + " " if r.get("name") else "") + "(" + r["session"] + "); the one left (" + str(r.get("previous")) + ") is kept") if r.get("ok") else "No new session: " + r.get("error", "?"))'; \
	else \
		echo "The app is not running: nothing was changed. Start it (make start), then make new-session or + in the studio's sessions rail."; \
	fi

# Its old name ("start today afresh"): a new session now.
clear-session: new-session

voice-report:
	cd services && .venv/bin/python -m bench.voice_report

day-report:
	cd services && .venv/bin/python -m bench.day_report

# Iteration checks require explicit test paths; an omitted selector must never start the full suite.
.PHONY: test-python test-studio typecheck
test-python:
	@test -n "$(strip $(PY_TESTS))" || (echo 'Usage: make test-python PY_TESTS="tests/test_name.py[::case] ..."'; exit 2)
	cd services && .venv/bin/python -m pytest -q -p no:xdist -m "not timing" $(PY_TESTS)

test-studio:
	@test -n "$(strip $(STUDIO_TESTS))" || (echo 'Usage: make test-studio STUDIO_TESTS="src/name.test.ts ..."'; exit 2)
	cd apps/studio && npx vitest run $(STUDIO_TESTS)

typecheck:
	cd apps/studio && npx tsc --noEmit -p .

# The Python tests run on every core (pytest-xdist): JOBS=8 sets how many workers, SERIAL=1 runs them in one process.
# What a loaded Mac breaks rather than the code is left out (tests/conftest.py: the `wall_clock` fixture's bounds and the
# tests marked `timing`): make test-timing runs those, one test at a time.
test:
	cd services && if [ "$(SERIAL)" = 1 ]; then jobs="-p no:xdist"; \
		elif .venv/bin/python -c "import xdist" 2>/dev/null; then jobs="-n $(or $(JOBS),auto)"; \
		else echo "pytest-xdist is not in services/.venv (make setup adds it): the tests run in one process"; jobs=""; fi; \
		.venv/bin/python -m pytest -q -m "not timing" $$jobs
	cd apps/studio && npx tsc --noEmit -p . && npx vitest run
	@if [ "$$(uname)" = Darwin ] && command -v swiftc >/dev/null 2>&1; then $(MAKE) --no-print-directory native-test; \
		else echo "native checks skipped: they need macOS and swiftc"; fi

# The Mac app's update restart choice and storage words (packaging/macos/tests/update-restart), without Sparkle; and
# its launch screen's plan, ticks and words (packaging/macos/tests/setup-words); and that a quit waiting for its answer
# quits however it began (packaging/macos/tests/termination, a real NSApplication).
.PHONY: native-test
native-test:
	@out="$$(mktemp -d)/update-restart" && \
		swiftc -swift-version 5 packaging/macos/HookeUI/UpdateWords.swift packaging/macos/tests/update-restart/main.swift \
			-o "$$out" && "$$out"
	@out="$$(mktemp -d)/setup-words" && \
		swiftc -swift-version 5 packaging/macos/HookeUI/SetupWords.swift packaging/macos/HookeUI/SetupView.swift \
			packaging/macos/tests/setup-words/main.swift -o "$$out" && "$$out"
	@out="$$(mktemp -d)/agent-links" && \
		swiftc -swift-version 5 packaging/macos/HookeUI/AgentShared.swift packaging/macos/tests/agent-links/main.swift \
			-o "$$out" && "$$out"
	@out="$$(mktemp -d)/hooke" && \
		swiftc -swift-version 5 packaging/macos/hooke/main.swift packaging/macos/HookeUI/AgentShared.swift -o "$$out" && \
		"$$out" version
	@out="$$(mktemp -d)/termination" && \
		swiftc -swift-version 5 packaging/macos/HookeUI/Termination.swift packaging/macos/tests/termination/main.swift \
			-o "$$out" && "$$out" nested && "$$out" deferred && "$$out" event && \
		{ "$$out" old; test $$? -eq 3; } && echo "ok: the 4695 answer still hangs here, so these checks can fail"

test-timing:
	cd services && .venv/bin/python -m pytest -q -p no:xdist -m "timing or wall_clock" --timing

.PHONY: check-public
check-public:
	@echo "Contributor check: generated inputs and production library behavior; no running Studio or model inference."
	cd services && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTEST_PLUGINS= PYTEST_ADDOPTS= .venv/bin/python -m pytest -q -ra \
		tests/test_public_check.py tests/test_starter_example.py tests/test_measure.py \
		tests/test_evidence.py tests/test_corpus.py tests/test_sim_faults.py

# The frozen integration/release gate (not the edit loop): unit tests (Python and studio), the type check, the golden
# prompts and wires (services/tests/golden), and the end-to-end scenarios that need no cloud.
check: test
	$(MAKE) --no-print-directory sim-test SIM_ONLY=

sim-test:
	cd services && .venv/bin/python -m sim.harness $(if $(SIM_MANIFEST),--asset-manifest "$(SIM_MANIFEST)") $(if $(SIM_VIDEO),--video "../$(SIM_VIDEO)") $(if $(SIM_ONLY),--only "$(SIM_ONLY)")

# Every model in the catalog (catalog/models): ready here, or what it needs and how (orchestrator/readiness.py). It reads
# files and the keys file only: it loads no model, downloads nothing and calls no provider.
doctor:
	cd services && .venv/bin/python -m orchestrator.readiness

# The models folder (orchestrator/downloads.py, catalog/README.md "The models folder"): a model's pinned revision,
# downloaded or adopted, checked against its manifest, published read-only; the loaders read nothing else.
catalog-download:
	@test -n "$(ID)" || (echo "Usage: make catalog-download ID=<model id> (make doctor lists them)"; exit 2)
	cd services && .venv/bin/python -m orchestrator.downloads get $(ID)

catalog-adopt:
	cd services && .venv/bin/python -m orchestrator.downloads adopt $(ID)

catalog-pin:
	@test -n "$(ID)" || (echo "Usage: make catalog-pin ID=<model id> [ONLY=\"*.pt,*.json\"]"; exit 2)
	cd services && .venv/bin/python -m bench.catalog_pin $(ID) $(if $(ONLY),--only "$(ONLY)",)

model-add:
	@test -n "$(FROM)" || (echo "Usage: make model-add FROM=<Hugging Face link or file path> [NAME=]"; exit 2)
	cd services && .venv/bin/python -m orchestrator.mine "$(FROM)" $(if $(NAME),--name "$(NAME)",)

# Adding a capability (the skill .agents/skills/add-a-capability; ADR 0024's one door): what stands in the way, a
# folder to fill in, its lock, your own version of one, and the test the app will hold it to (orchestrator/contribute.py,
# bench/capability_test.py). capability-test installs in a catalog of its own, never the live app's.
.PHONY: capability-preflight capability-new capability-lock capability-derive capability-test capability-package capability-build-source
capability-build-source:
	@test -n "$(FROM)" -a -n "$(SHA256)" -a -n "$(SUBDIR)" -a -n "$(BUILDER)" -a -n "$(INTO)" || (echo "Usage: make capability-build-source FROM=<absolute source.zip> SHA256=<digest> SUBDIR=<source directory> BUILDER=<absolute pinned builder folder> INTO=<new absolute private directory>"; exit 2)
	cd services && .venv/bin/python -m orchestrator.source_build "$(FROM)" --sha256 "$(SHA256)" --subdirectory "$(SUBDIR)" --builder "$(BUILDER)" --into "$(INTO)"

capability-preflight:
	@test -n "$(FROM)" || (echo "Usage: make capability-preflight FROM=<folder, entry id or link>"; exit 2)
	cd services && .venv/bin/python -m orchestrator.contribute preflight "$(FROM)" $(if $(WORKSPACE),--workspace "$(WORKSPACE)",)

capability-new:
	@test -n "$(ID)" || (echo "Usage: make capability-new ID=<id> [RUNNER=python-env|fiji] [OUTPUT=table.objects] [INPUT=image.2d.rgb8] [INTO=mine|catalog]"; exit 2)
	cd services && .venv/bin/python -m orchestrator.contribute new "$(ID)" $(if $(RUNNER),--runner $(RUNNER),) $(if $(OUTPUT),--output $(OUTPUT),) $(if $(INPUT),--input $(INPUT),) $(if $(INTO),--into $(INTO),) $(if $(WORKSPACE),--workspace "$(WORKSPACE)",)

capability-lock:
	@test -n "$(ID)" || (echo "Usage: make capability-lock ID=<id>"; exit 2)
	cd services && .venv/bin/python -m orchestrator.contribute lock "$(ID)" $(if $(WORKSPACE),--workspace "$(WORKSPACE)",)

capability-derive:
	@test -n "$(FROM)" -a -n "$(ID)" || (echo "Usage: make capability-derive FROM=<id> ID=<your version's id> [INTO=mine|catalog]"; exit 2)
	cd services && .venv/bin/python -m orchestrator.contribute derive "$(FROM)" "$(ID)" $(if $(INTO),--into $(INTO),) $(if $(WORKSPACE),--workspace "$(WORKSPACE)",)

capability-test:
	@test -n "$(ID)" || (echo "Usage: make capability-test ID=<id> [LAYERS=1,2,3] [CATALOG=folder]"; exit 2)
	cd services && nice -n 10 .venv/bin/python -m bench.capability_test "$(ID)" $(if $(LAYERS),--layers $(LAYERS),) $(if $(CATALOG),--catalog "$(CATALOG)",) $(if $(WORKSPACE),--workspace "$(WORKSPACE)",)

capability-package:
	@test -n "$(WORKSPACE)" || (echo "Usage: make capability-package WORKSPACE=<private handoff folder> [BY=<name>]"; exit 2)
	cd services && .venv/bin/python -m orchestrator.contribute package --workspace "$(WORKSPACE)" $(if $(BY),--by "$(BY)",)

catalog-check:
	cd services && .venv/bin/python -m orchestrator.catalog_view

catalog-pr:
	@test -n "$(ID)" || (echo "Usage: make catalog-pr ID=<your entry's id> [REMOTE=origin] [BASE_REMOTE=origin] [BASE=dev] [OUTPUT=folder] or PACKAGE=folder PUSH=1 SIGNOFF=1"; exit 2)
	cd services && .venv/bin/python -m orchestrator.publish "$(ID)" $(if $(filter 1,$(PUSH)),--push,) $(if $(filter 1,$(SIGNOFF)),--signoff,) $(if $(REMOTE),--remote "$(REMOTE)",) $(if $(BASE_REMOTE),--base-remote "$(BASE_REMOTE)",) $(if $(BASE),--base "$(BASE)",) $(if $(OUTPUT),--output "$(OUTPUT)",) $(if $(PACKAGE),--package "$(PACKAGE)",)

catalog-install:
	@test -n "$(ID)" || (echo "Usage: make catalog-install ID=<entry id> (what its Install button does)"; exit 2)
	cd services && .venv/bin/python -m orchestrator.envs $(ID)

catalog-update:
	cd services && .venv/bin/python -m orchestrator.channels update $(CHANNEL) $(if $(COMMIT),--commit $(COMMIT),)

channel-add:
	@test -n "$(URL)" || (echo "Usage: make channel-add URL=<git address> [NAME=] [COMMIT=]"; exit 2)
	cd services && .venv/bin/python -m orchestrator.channels add "$(URL)" $(if $(NAME),--name $(NAME),) $(if $(COMMIT),--commit $(COMMIT),)

catalog-ci:
	cd services && .venv/bin/python -m bench.catalog_ci $(if $(IDS),--ids $(IDS),) $(if $(BASE),--base $(BASE),) $(if $(STATIC),--static,)

fiji-inventory:
	cd services && .venv/bin/python -m bench.fiji_inventory

catalog-proof:
	cd services && nice -n 10 .venv/bin/python -m bench.catalog_proof $(if $(ONLY),--only $(ONLY),)

keys-check:
	cd services && .venv/bin/python -m bench.keys_check

sam3-setup:
	./scripts/setup-sam3.sh

# Trackastra (code BSD-3-Clause; non-commercial here: its weights' data terms are unknown, and it brings Gurobi), the
# link operation's implementation (operations.LINK; session knowledge K2). Pinned: 0.5.6's track() returns (graph, masks).
trackastra-setup:
	cd services && uv pip install "trackastra==0.5.6"

arena-report:
	cd services && .venv/bin/python -m bench.arena_report

licences:
	cd services && .venv/bin/python -m bench.licences_report

export-corrections:
	cd services && .venv/bin/python -m bench.export_corrections

# Session knowledge K4 (docs/design/2026-10-07-session-knowledge.md, contract 5): bundles, the replay bench and
# fine-tuning, all explicit jobs outside the app. Research is the shared default; preserve an explicit edition.
LOCAL_EDITION = MICROMIND_EDITION=$(or $(MICROMIND_EDITION),research)

bundle:
	cd services && .venv/bin/python -m bench.bundle --out "$(abspath $(OUT))" $(if $(DATASET),--dataset "$(DATASET)",) \
		$(if $(FROM),--from "$(FROM)" --to "$(TO)",) $(if $(DAY),--day "$(DAY)",) $(if $(NO_VIDEO),--no-video,)

capability-check:
	cd services && $(LOCAL_EDITION) nice -n 10 .venv/bin/python -m bench.capability_check $(if $(DOWNLOAD),--download,) \
		$(if $(BESIDE_LIVE),--beside-live,) $(if $(ONLY),--only "$(ONLY)",)

capability-check-cloud:
	cd services && $(LOCAL_EDITION) .venv/bin/python -m bench.capability_check --cloud $(if $(VIDEO),--video,) \
		$(if $(BESIDE_LIVE),--beside-live,) $(if $(ONLY),--only "$(ONLY)",)

replay-bench:
	cd services && $(LOCAL_EDITION) nice -n 10 .venv/bin/python -m bench.replay_bench --bundle "$(BUNDLE)" \
		$(if $(IMPL),--impl "$(IMPL)",) $(if $(POLICY),--policy $(POLICY),) $(if $(LIMIT),--limit $(LIMIT),) \
		$(if $(BESIDE_LIVE),--beside-live,)

finetune-cells:
	cd services && $(LOCAL_EDITION) nice -n 10 .venv/bin/python -m bench.finetune_cells $(if $(EPOCHS),--epochs $(EPOCHS),) \
		$(if $(HOLD_OUT),--hold-out $(HOLD_OUT),) $(if $(BUNDLE),--bundle "$(BUNDLE)",) $(if $(DRY),--dry-run,) \
		$(if $(BESIDE_LIVE),--beside-live,)

naming-fetch:
	cd services && .venv/bin/python -m bench.naming.fetch $(if $(FETCH),--yes,)

assets:
	cd services && .venv/bin/python -m orchestrator.assets status $(if $(VERIFY),--verify,) $(if $(REMOTE),--remote,)

assets-fetch:
	cd services && .venv/bin/python -m orchestrator.assets fetch $(if $(CORPUS),--corpus,)

assets-add:
	cd services && .venv/bin/python -m orchestrator.assets add "$(FILE)" --specimen "$(SPECIMEN)" $(if $(TITLE),--title "$(TITLE)",) $(if $(NOTES),--notes "$(NOTES)",) $(if $(NOT_MICROSCOPE),--not-microscope,)

assets-adopt:
	cd services && .venv/bin/python -m orchestrator.assets adopt

assets-set:
	cd services && .venv/bin/python -m orchestrator.assets set "$(ID)" $(if $(OBJECTIVE),--objective $(OBJECTIVE),) $(if $(EYEPIECE),--eyepiece $(EYEPIECE),) $(if $(FIELD),--field-number $(FIELD),) $(if $(UM_PER_PX),--um-per-px $(UM_PER_PX),) $(if $(MICROSCOPE),--microscope "$(MICROSCOPE)",) $(if $(CAMERA),--camera "$(CAMERA)",)

assets-backup:
	cd services && .venv/bin/python -m orchestrator.assets backup

# Datasets (docs/datasets.md): the live app's are in ~/MicroscopeMind/datasets (as scripts/studio.sh sets).
DATASETS_DIR ?= $(or $(MICROMIND_DATASETS_DIR),$(or $(MICROMIND_ASSETS),$(HOME)/MicroscopeMind)/datasets)

# The three homes of everything saved (docs/saves.md): moving them never deletes; pruning lists first.
saves:
	cd services && .venv/bin/python -m orchestrator.saves report

saves-move:
	cd services && .venv/bin/python -m orchestrator.saves move $(if $(APPLY),--apply)

logs-prune:
	cd services && .venv/bin/python -m orchestrator.saves prune --days $(or $(DAYS),14) $(if $(APPLY),--apply)

dataset-check:
	cd services && .venv/bin/python -m orchestrator.datasets.check $(if $(ID),"$(DATASETS_DIR)/$(ID)",$$(ls -d "$(DATASETS_DIR)"/ds-* 2>/dev/null))

dataset-import:
	cd services && MICROMIND_DATASETS_DIR="$(DATASETS_DIR)" .venv/bin/python -m orchestrator.datasets.importer \
		--from "$(FROM)" --to "$(TO)" $(if $(VIDEO),--video "$(abspath $(VIDEO))" --video-start "$(VIDEO_START)") \
		$(if $(SCREEN),--screen "$(abspath $(SCREEN))" --screen-start "$(SCREEN_START)") $(if $(NAME),--name "$(NAME)") $(ARGS)

assets-migrate:
	cd services && .venv/bin/python -m orchestrator.assets migrate $(if $(FROM),--from "$(FROM)",) $(if $(DRY),--dry-run,)

corpus:
	cd services && .venv/bin/python -m bench.corpus.fetch $(if $(FETCH),--yes,) $(if $(ONLY),--only $(ONLY),) $(if $(INTERNAL),--internal,)

verdicts:
	./scripts/verdicts.sh

capability-clip:
	ID="$(ID)" FROM="$(FROM)" START="$(START)" DUR="$(DUR)" $(if $(CROP),CROP='$(CROP)',) ./scripts/capability-clip.sh

corpus-tours:
	cd services && .venv/bin/python -m bench.corpus.build $(if $(ONLY),--only $(ONLY),)

tracking-bench:
	cd services && .venv/bin/python -m bench.tracking

object-tracking-bench:
	cd services && .venv/bin/python -m bench.object_tracking

acting-bench:
	cd services && .venv/bin/python -m bench.acting.run $(if $(CASE),--case $(CASE),) $(if $(VERBOSE),--verbose,)

naming-bench:
	cd services && .venv/bin/python -m bench.naming.run --options $(or $(OPTIONS),gemini-3.8-flash) \
		--repeats $(or $(REPEATS),1) $(if $(LIMIT),--limit $(LIMIT),)

camera: camera-page
	PORT=$(CAMERA_PORT) TARGET_DEVICE="$(CAMERA_TARGET)" ./experiments/iphone-camera-feed/scripts/serve-browser-check.sh

# The camera page the bridge serves at /camera/ (apps/studio/src/camera; make start builds it with the studio).
camera-page:
	cd apps/studio && npx vite build -c vite.camera.config.ts --logLevel warn

camera-open:
	open "$(CAMERA_URL)/camera/"

camera-stop:
	@pids="$$(lsof -tiTCP:$(CAMERA_PORT) -sTCP:LISTEN)"; \
	if [ -z "$$pids" ]; then \
		echo "No camera bridge listening on $(CAMERA_URL)"; \
	elif $(IS_CAMERA_BRIDGE); then \
		kill $$pids; \
		echo "Stopped camera bridge on $(CAMERA_URL)"; \
	else \
		echo "Port $(CAMERA_PORT) is used by another program (pid $$pids), not the camera bridge; left it running."; \
	fi

camera-health:
	curl -s "$(CAMERA_URL)/api/health" | python3 -m json.tool

camera-preflight:
	curl -s "$(CAMERA_URL)/api/preflight" | python3 -m json.tool

camera-reconnect:
	curl -s -X POST "$(CAMERA_URL)/api/restart" | python3 -m json.tool

# The camera console's Live Source choice from the command line (kept for the next start).
camera-source:
	@test -n "$(KIND)" || { echo "usage: make camera-source KIND=continuity|blackmagic|other|none"; exit 2; }
	curl -s -X POST -H "X-MicroscopeMind: camera-source" "$(CAMERA_URL)/api/source?kind=$(KIND)" \
		| python3 -c "import json,sys; d=json.load(sys.stdin); b=d.get('bridge') or {}; print(d.get('error') or (b.get('targetDevice'), b.get('stateLabel'), (b.get('diagnosis') or {}).get('text') or ''))"

camera-controls:
	./experiments/iphone-camera-feed/scripts/probe-camera-controls.sh "$(CAMERA_TARGET)"

camera-record-start:
	curl -s -X POST "$(CAMERA_URL)/api/record/start" | python3 -m json.tool

camera-record-status:
	curl -s "$(CAMERA_URL)/api/record/status" | python3 -m json.tool

camera-record-stop:
	curl -s -X POST "$(CAMERA_URL)/api/record/stop" | python3 -m json.tool

$(CV_PYTHON):
	python3 -m venv "$(CV_VENV)"

$(CV_DEPS_STAMP): $(CV_DIR)/requirements.txt | $(CV_PYTHON)
	"$(CV_PYTHON)" -m pip install --upgrade pip
	"$(CV_PYTHON)" -m pip install -r "$(CV_DIR)/requirements.txt"
	@touch "$@"

cv-setup: $(CV_DEPS_STAMP)

cv: cv-setup
	CV_HOST=$(CV_HOST) CV_PORT=$(CV_PORT) CAMERA_URL="$(CAMERA_URL)" CV_PYTHON="$(CV_PYTHON)" ./$(CV_DIR)/scripts/serve-cv.sh

cv-open:
	open "$(CV_URL)/"

cv-stop:
	@pids="$$(lsof -tiTCP:$(CV_PORT) -sTCP:LISTEN)"; \
	if [ -z "$$pids" ]; then \
		echo "No CV consumer listening on $(CV_URL)"; \
	elif $(IS_CV_SERVICE); then \
		kill $$pids; \
		echo "Stopped CV consumer on $(CV_URL)"; \
	else \
		echo "Port $(CV_PORT) is used by another program (pid $$pids), not the CV consumer; left it running."; \
	fi

cv-health:
	curl -s "$(CV_URL)/api/health" | python3 -m json.tool

cv-smoke: cv-setup
	"$(CV_PYTHON)" ./$(CV_DIR)/scripts/cv_smoke.py --base-url "$(CV_URL)" --bridge-url "$(CAMERA_URL)"

camera-cv: cv-setup
	@mkdir -p "$(CV_OUTPUT_DIR)"
	@if lsof -tiTCP:$(CAMERA_PORT) -sTCP:LISTEN >/dev/null; then \
		if $(IS_CAMERA_BRIDGE); then \
			echo "Camera bridge already running at $(CAMERA_URL)"; \
		else \
			echo "Port $(CAMERA_PORT) is used by another program (pid $$(lsof -tiTCP:$(CAMERA_PORT) -sTCP:LISTEN | tr '\n' ' '))."; \
			echo "Stop it, or start on another port: make start CAMERA_PORT=8775"; \
			exit 1; \
		fi; \
	else \
		echo "Starting camera bridge at $(CAMERA_URL)"; \
		PORT=$(CAMERA_PORT) TARGET_DEVICE="$(CAMERA_TARGET)" ./experiments/iphone-camera-feed/scripts/serve-browser-check.sh > "$(CAMERA_BRIDGE_LOG)" 2>&1 & \
		echo $$! > "$(CV_OUTPUT_DIR)/camera-bridge.pid"; \
		sleep 2; \
	fi
	@echo "Camera page: $(CAMERA_URL)/camera/"
	@echo "CV dashboard: $(CV_URL)/"
	CV_HOST=$(CV_HOST) CV_PORT=$(CV_PORT) CAMERA_URL="$(CAMERA_URL)" CV_PYTHON="$(CV_PYTHON)" ./$(CV_DIR)/scripts/serve-cv.sh

camera-cv-stop:
	@$(MAKE) cv-stop
	@$(MAKE) camera-stop

.PHONY: scientific-acceptance
# Every acceptance group and atomic child as pass, fail, missing or external, with evidence links
# (services/bench/scientific_acceptance.py; the inventory in services/bench/specification).
# OUT= new report directory (default reports/scientific-acceptance/<time>), NO_RUN=1 report without running the checks,
# FROM=<dir> reuse a retained run while its inputs are unchanged, ARCHIVE= the pinned napari zip that verifies the
# inventory, REGENERATE=1 rewrite atomic-cases.json from it first, COMPLETE=1 fail unless complete, CASE= one R0x component.
scientific-acceptance:
	cd services && .venv/bin/python -m bench.scientific_acceptance $(if $(CASE),--case '$(CASE)' --output '$(OUT)',$(if $(OUT),--output '$(OUT)',)) $(if $(NO_RUN),--no-run,) $(if $(FROM),--from '$(FROM)',) $(if $(ARCHIVE),--archive '$(ARCHIVE)',) $(if $(REGENERATE),--regenerate,) $(if $(COMPLETE),--require-complete,)
