# Standalone core and optional service extensions

HookeUI owns the local scientific workspace, camera and file readers, scientific
arrays, measurements, capability execution, Projects and saved Study documents.
It does not authenticate users to Hooke. Optional user-configured third-party model
clients remain available under their own terms and explicit local data controls.

Official Hooke web content, account service, branding and managed research backend
are separate. The standalone source contains no managed provider deployment,
service IAM, tenant authentication or official artwork.

Studies retain questions, evidence and workflow references locally. Connected agents
can drive admitted local catalog workflows through the agent door; an available
local researcher can attach to the same Study under the user's configured limits.
Managed research provider execution remains unavailable without an installed
extension. Its readiness is explicit, and external task receipts or held allowances
are never silently settled. Local scientific execution does not grant paid provider
access or subscribed coding-agent launch.

A trusted operator may set `HOOKEUI_RESEARCH_EXTENSION` to an installed Python
module. Its `register(app, built, studio_origin, header)` installs service routes
and `app.state.research_lifecycle`, with `start()` and asynchronous `stop()`.
Configuration errors fail startup. The extension is executable application code,
not a user upload or an account setting. No HTTP request or document may set it.

Generic desktop packaging accepts a supplied icon via `ICNS`, and the studio build a
supplied mark for its menu button via `HOOKEUI_MARK_DARK` and `HOOKEUI_MARK_LIGHT`;
the core otherwise uses its neutral icon and mark. Existing update-feed URLs and public verification keys are
compatibility information, not access to official signing. Official release
orchestration and signing credentials are maintained outside this repository.
Forks must use their own release identity, keys and feed before distributing updates.
