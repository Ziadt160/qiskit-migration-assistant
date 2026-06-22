# Publishing to PyPI

The package metadata is PyPI-ready: MIT license, classifiers, URLs, bundled `data/*.json`,
the **`qiskit_migration`** import package, and a `qiskit-migrate` CLI entry point.

## Package layout

```toml
[project.scripts]
qiskit-migrate = "qiskit_migration.migration.cli:main"
```

After `pip install`, users get a clean `qiskit_migration` package
(e.g. `import qiskit_migration.migration.transform`) and a `qiskit-migrate` command. The PyPI
distribution name is `qiskit-migration-assistant`.

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
