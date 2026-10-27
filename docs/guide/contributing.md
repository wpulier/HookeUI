# Develop with HookeUI

[HookeUI on GitHub](https://github.com/wpulier/HookeUI) is the public app repository.
It contains the local scientific workspace and desktop code. The official Hooke
website and hosted account platform are maintained separately; no Hooke account
is required to develop or run the core.

## Make a focused contribution

1. Read [CONTRIBUTING.md](https://github.com/wpulier/HookeUI/blob/dev/CONTRIBUTING.md)
   and the repository/component instructions for the part you will change.
2. Fork the repository and create a branch from **`dev`**.
3. Make a focused change with a reproducible example. Run affected checks;
   `make check-public` is a starting point with generated inputs and no private data.
   The full integration gate has additional documented requirements.
4. Sign off your commits with `git commit -s` under the Developer Certificate of Origin.
5. Open a PR targeting **`wpulier/HookeUI:dev`**. Describe the result, how you checked
   it and any limitations. Appointed maintainers review contributions.

Documentation corrections, small examples, reproducible bug reports and scientific
review are useful contributions too. See the
[capability guide](https://github.com/wpulier/HookeUI/blob/dev/docs/contributing/README.md)
to contribute a method or adapter. Preserve upstream attribution and terms.

## Development and releases

`dev` supplies explicit Beta releases. Will accepts an installed Beta and promotes
its exact source to `main` for Stable. Stable publication additionally requires his
GitHub approval. A merge, tag, successful CI run or documentation build does not
publish an installer. Official signing and publication are separate from ordinary
maintainer access; see the [public release policy](https://github.com/wpulier/HookeUI/blob/dev/packaging/macos/RELEASING.md).

## Improve this documentation

Edit Markdown files in `docs/guide/`. The toctree in `index.md` controls the sidebar;
add a new page there when it belongs in the guide. The documentation build installs
only its pinned documentation dependencies, without starting HookeUI or importing
its scientific services.

From the repository root:

```sh
uv run --no-project --python 3.13 --with-requirements docs/guide/requirements.txt sphinx-build -W --keep-going -b html docs/guide docs/guide/_build/html
```

Open the generated `index.html` or serve that directory locally. Do not commit
`_build/`. Read the Docs builds `.readthedocs.yaml` from the project's configured
branch. Its **latest** documentation follows `dev`; a Stable-documentation version
should only be added when the matching source has a working configuration.

Be respectful and specific in reviews and discussions. Follow the
[code of conduct](https://github.com/wpulier/HookeUI/blob/dev/CODE_OF_CONDUCT.md).
