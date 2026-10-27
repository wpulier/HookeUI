# Models, measurements and your data

## Check readiness before running

The **Models** page describes the methods available to your installation and
their missing requirements. Some need downloaded weights, a separate software
environment, a provider credential, or an explicitly activated adapter.
Opening the workspace does not mean every model is installed or ready.

Choose a method for the input it supports. Segmentation, image enhancement,
classification and measurement answer different questions. Read the method's
description, required inputs, outputs and license information before using it.
Preserve the original image when comparing results.

## Local does not mean every operation stays local

HookeUI itself runs without a Hooke account. Local methods and the guided
bright-object example can run without an external-provider key.

Optional AI assistance and remote methods may send selected data to a provider
you configure. Provider credentials, charges, retention rules and terms are
separate from HookeUI's source-code license. Check the selected method's execution
location and provider before sending confidential or sensitive material.

## Keep enough information to repeat the work

For a result you intend to use, retain:

- The original input and a stable identity, such as its filename and checksum.
- The exact HookeUI version and whether it was Stable, Beta or a source checkout.
- The method or model version, weights and important parameters.
- The selected image plane, region, channel or time point.
- Calibration and units, plus any preprocessing or manual corrections.
- The result and the limitations you observed.

A mask or region outline is not a biological identification. Agreement with
another implementation can check consistency, but does not establish accuracy
on your specimens. Validate a method against appropriate reference data for your
question and report failures as well as successes.

## Projects, originals and backups

Use Projects to organize work and return to saved inputs and results. Preserve
your original files and keep independent backups of important work. Stable and
Beta use separate workspaces; moving between channels is a deliberate operation.

The repository's [storage reference](https://github.com/wpulier/HookeUI/blob/dev/docs/saves.md)
describes saved assets, datasets and temporary output. Do not delete application
folders or reset your profile as a first troubleshooting step.
