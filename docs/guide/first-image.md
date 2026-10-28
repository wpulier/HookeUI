# Your first image

Start with the bundled microscopy example. This route needs no microscope camera
or external-provider key. The example is a real image of human cells undergoing
mitosis, provided by David Root under CC0-1.0; its
[source and rights notice](https://github.com/wpulier/HookeUI/blob/dev/assets/examples/NOTICE.md)
are kept with the repository.

## Find and measure bright regions

1. Open HookeUI and choose **Try the guided example** in the welcome experience.
2. Choose **Find bright objects**. Wait for the local analysis to complete.
3. Choose **See measurements** to inspect the table alongside the image.
4. Compare the outlines to the original pixels. Look at regions that touch,
   faint structures and background: a numerical result can still be a poor
   description of the specimen.
5. Keep the input, method and settings with any result you want to revisit. Use
   the app's Projects and saved results to return to your work; check that the
   result you need is present before closing the workspace.

The labels above describe the development version used for the documented
[workspace capture](https://github.com/wpulier/HookeUI/blob/dev/docs/images/README.md).
If your version differs, check its release notes and ask in the community.

## Understand what the numbers mean

The bright-object method finds regions based on image intensity. An outline is
not proof that a region is one cell, nor that every cell has been detected.
Overlapping cells and image artifacts can produce misleading regions.

No physical scale is provided for this example. Measurements are in **pixels** or
**square pixels**, not micrometres. Do not infer a calibration from magnification
alone. For your own data, retain the source's calibration and check which units
the selected method actually uses.

## Open your own data

Start with **Add a recording or picture** and a small image whose content and
origin you understand. Inspect the original, choose a method suited to that input,
and compare its output to the pixels before moving to a larger collection.

For a live source, follow the repository's
[camera setup guide](https://github.com/wpulier/HookeUI/blob/dev/experiments/iphone-camera-feed/README.md#connecting-a-camera).
Opening an image does not require camera permissions.

Before sharing results, record the app version, input identity, method/model
version, parameters and units. See [models and data](models-and-data.md).

## Discuss what you found

Try this question: **Which bright regions look plausible, and where does the
method merge objects or pick up background?** Share a permitted crop, your method
and settings, and what you expected in
[Hooke Community](https://hookeui.discourse.group). Do not upload private or
sensitive specimens just to get help.
