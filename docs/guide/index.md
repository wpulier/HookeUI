# Scientific imaging, on your own computer

HookeUI is an open-source workspace for exploring images and recordings, connecting
a microscope camera, measuring regions, and running scientific analysis tools.
The local app requires no Hooke account. A microscope is optional: start with a picture.

**[Install HookeUI](install.md)** · **[Try your first image](first-image.md)** ·
**[Ask the community](https://hookeui.discourse.group)** ·
**[Explore the source](https://github.com/wpulier/HookeUI)**

```{figure} ../images/hookeui-workspace.webp
:alt: HookeUI displaying a microscopy image with colored bright-region outlines and a table of pixel measurements.

Actual output from the local guided example. Image: David Root, CC0-1.0.
[Image credit](https://github.com/wpulier/HookeUI/blob/dev/assets/examples/NOTICE.md)
and [capture details](https://github.com/wpulier/HookeUI/blob/dev/docs/images/README.md).
Bright regions are not verified cell identities.
```

## Choose a starting point

- **New to HookeUI?** Install the Mac app, then work through the guided image example.
- **Choosing an analysis method?** Understand model setup, inputs, units and data handling.
- **Something went wrong?** Use the troubleshooting checklist and share a reproducible report.
- **Want to contribute?** Start with a focused change against `dev` or help improve a guide.
- **Using HookeUI in research?** Record the exact version, methods and parameters, and cite the software and underlying methods.

```{note}
This documentation follows the public `dev` branch. It is a development guide,
not a claim that every described feature is present in the current Stable app.
Check the [Stable](https://github.com/wpulier/hookeui.org/releases/latest) or
[Beta](https://github.com/wpulier/hookeui-beta/releases/latest) release notes for
your installed version. A documentation update never publishes an app update.
```

```{toctree}
:maxdepth: 2
:caption: Using HookeUI

install
first-image
models-and-data
troubleshooting
```

```{toctree}
:maxdepth: 1
:caption: The project

contributing
citing-and-license
```
