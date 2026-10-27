# Standalone core and optional service extensions

HookeUI owns the local scientific workspace, camera and file readers, scientific
arrays, measurements, capability execution, Projects and saved Study documents.
It does not authenticate users to Hooke. Optional user-configured third-party model
clients remain available under their own terms and explicit local data controls.

Official Hooke web content, account service, branding and managed research backend
are separate. The standalone source contains no managed provider deployment,
service IAM, tenant authentication or official artwork.

The Study HTTP adapter saves and reopens local documents without managed execution.
Readiness returns an empty provider list when no extension is installed. Attempts
to execute unavailable work return an explicit refusal; existing external task
receipts and held allowances are never silently settled. Local tasks, the Assistant
and user-configured scientific providers retain their own execution paths.

A trusted operator may set `HOOKEUI_RESEARCH_EXTENSION` to an installed Python
module. Its `register(app, built, studio_origin, header)` installs service routes
and `app.state.research_lifecycle`, with `start()` and asynchronous `stop()`.
Configuration errors fail startup. The extension is executable application code,
not a user upload or an account setting. No HTTP request or document may set it.

Generic desktop packaging accepts a supplied icon via `ICNS`; the core otherwise
uses its neutral icon. Existing update-feed URLs and public verification keys are
compatibility information, not access to official signing. Official release
orchestration and signing credentials are maintained outside this repository.
Forks must use their own release identity, keys and feed before distributing updates.
