# Publishing to PyPI

The package metadata is PyPI-ready (MIT license, classifiers, URLs, bundled `data/*.json`).
Two things to decide before a real release.

## ⚠️ Prerequisite: the import package name

The distribution currently exposes a **top-level `src` package** (`[tool.setuptools.packages.find]
include = ["src*"]`, and all imports are `import src.migration...`). Publishing as-is would put a
generic `src` package on every user's machine — a namespace collision waiting to happen.

Before a clean PyPI release, rename the import package to something unique, e.g. `qiskit_migration`:
- move/rename so the package imports as `qiskit_migration.*` (or remap with
  `[tool.setuptools.package-dir]`), and update imports + `package-data` + test paths accordingly;
- add a CLI entry point:
  ```toml
  [project.scripts]
  qiskit-migrate = "qiskit_migration.cli:main"
  ```

Until that rename, prefer **install-from-GitHub** (`pip install git+https://github.com/Ziadt160/qiskit-migration-assistant.git`),
which is what the hosted demo uses — it works without claiming the `src` name on PyPI.

## Build & check

```bash
pip install build twine
python -m build                # builds sdist + wheel into dist/
twine check dist/*             # validates metadata + long description renders
unzip -l dist/*.whl | grep data/   # confirm the JSON knowledge files are bundled
```

## Test on TestPyPI first

```bash
twine upload -r testpypi dist/*
pip install -i https://test.pypi.org/simple/ qiskit-migration-assistant   # in a clean venv
```

## Publish

```bash
twine upload dist/*            # needs a PyPI API token (~/.pypirc or TWINE_PASSWORD)
```

## Release hygiene
- Bump `version` in `pyproject.toml` (currently `0.1.0`); tag the release: `git tag v0.1.0 && git push --tags`.
- Cut a GitHub Release with notes + the demo GIF.
- Note: PyPI uploads are immutable — you can't overwrite a version. Test on TestPyPI first.
