# Troubleshooting and getting help

## First-run setup stops

Read the named failing step and use **Show log** before retrying. Check network
access and available disk space. Preserve the log and the app version; do not
delete a working profile or saved projects to fix an installation problem.

For source installations, the
[clean-start checklist](https://github.com/wpulier/HookeUI/blob/dev/docs/clean-start.md#recover-from-setup-problems)
covers missing prerequisites, unsupported Node versions, interrupted installation
and occupied ports. Keep the committed dependency lockfiles.

## A model is unavailable

Open **Models** and follow the requirement reported for that method. A missing
download, dependency, credential or unsupported input needs a specific fix.
Do not treat a successful app launch as evidence that a model ran.

## No camera image

Try a local picture first to distinguish the image workflow from camera setup.
Then use the selected camera's checklist. The
[camera guide](https://github.com/wpulier/HookeUI/blob/dev/experiments/iphone-camera-feed/README.md#connecting-a-camera)
describes supported sources. A successful simulated-input test does not verify
your physical phone, network or microscope.

## A result looks wrong

Return to the original pixels. Check the selected plane/channel, calibration,
method and settings. State what looks wrong and include a small example you have
permission to share. A screenshot alone may not contain enough information to
reproduce a reader or analysis issue.

## Ask a useful question

Use [Hooke Community](https://hookeui.discourse.group) for workflow questions and
[GitHub issues](https://github.com/wpulier/HookeUI/issues) for reproducible bugs.
Include the app version/channel, macOS and Mac model, steps, expected versus
observed behavior, and relevant log excerpts. Remove credentials, private paths
and sensitive data before posting. Public discussions are public.

For a security concern, follow the repository's
[security policy](https://github.com/wpulier/HookeUI/blob/dev/SECURITY.md) rather
than posting credentials or exploit details in an ordinary help thread.
