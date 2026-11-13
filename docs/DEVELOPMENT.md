# Development

## Repository layout

```
hjt_fbx_plus/              the add-on (this folder is what gets installed)
  __init__.py              bl_info + register/unregister (legacy add-on entry point)
  blender_manifest.toml    extension manifest (Blender 4.2+)
  registration.py          class registration order
  state.py                 the add-on's global state: profiles, cache, task pool, queue, history
  core/                    options table (single source of truth for settings), cache, tasks, logging
  fbx/                     FBX reader (binary + ASCII), writer, document model, inspection, pre-import repair
  archives/                ZIP, native 7z, external tool readers, AES, archive index
  textures/                texture slots, Unity material JSON, image headers, discovery and planning
  profiles/                profile schema, registry, detection, built-in profiles (JSON)
  blender/                 everything that touches bpy: engines, pipeline, materials, post-processing, images
  ui/                      properties, panels, lists, menus, preferences, operators
tests/
  unit/                    stdlib-only tests (no Blender needed)
  blender/                 tests that run inside Blender or the bpy module
  make_test_assets.py      generates the synthetic test assets
  build_test_archives.py   packs them into every archive flavor
  make_bench_assets.py     generates the benchmark data
tools/
  build.py                 builds the installable ZIP
  api_compat_check.py      checks API use against older Blender versions' stubs
docs/                      user documentation
```

## How an import works

1. **Session** (`blender/session.py`): files and archives are added to the queue. Archives are listed and models analyzed in worker threads (`core/tasks.py`). Workers never touch `bpy`. Results are delivered on the main thread by a `bpy.app.timers` callback.
2. **prepare_job** (`blender/pipeline.py`, worker thread): extract the FBX, parse it, detect the profile, plan the materials and textures (`textures/discovery.py`), extract the textures, and write a repaired copy of the FBX with hidden object IDs (`fbx/prepare.py`).
3. **import_job** (main thread): run Blender's importer on the repaired copy into a fresh collection, then map every new datablock back to its FBX object through the hidden IDs (`blender/mapping.py`).
4. **finish_job** (main thread): tangents, in-between shape keys, LOD/collision categories, skinning verification, bone fitting (`blender/postprocess.py`), materials (`blender/materials.py`), texture storage (`blender/images.py`) and the report.

The batch operator (`ui/operators/import_ops.py`) runs these steps per model as a modal operator, or synchronously when Blender has no window (background mode, scripts, tests).

## Running the tests

### Unit tests (any Python 3.10+, no Blender)

```bash
python -m unittest discover -s tests/unit -v
```

The 7z/RAR tests use `7z` and `rar` when installed and skip otherwise.

### Blender tests

The Blender tests run in a Blender executable (`blender -b --factory-startup --python <script> -- <args>`) or with the `bpy` module from PyPI in a matching Python (e.g. `pip install bpy==4.2.*` on Python 3.11). With the `bpy` module, replace `blender -b --factory-startup --python` with `python`.

1. Generate the synthetic assets and the archives built from them:

   ```bash
   blender -b --factory-startup --python tests/make_test_assets.py -- . /tmp/hjt-assets
   ```

   ```bash
   python tests/build_test_archives.py /tmp/hjt-assets /tmp/hjt-archives
   ```

2. The import matrix: every asset through every archive format with both import engines, with assertions on the imported data:

   ```bash
   blender -b --factory-startup --python tests/blender/test_matrix.py -- . /tmp/hjt-assets /tmp/hjt-archives
   ```

3. The add-on test: install, register, draw every panel, add and analyze archives, batch import, material tools, profile management, presets, unregister and re-enable:

   ```bash
   blender -b --factory-startup --python tests/blender/test_addon.py -- . /tmp/hjt-archives/assets.7z /tmp/hjt-archives/assets.rar
   ```

4. The install test, on the built ZIP: install as an extension and as a legacy add-on, import, uninstall:

   ```bash
   python tools/build.py
   ```

   ```bash
   blender -b --factory-startup --python tests/blender/test_install.py -- dist/hjt_fbx_plus-1.3.0.zip /tmp/hjt-archives/assets.zip
   ```

Each script prints `== PASS` or `== FAIL (n failures)` and exits non-zero on failure.

Run them with `BLENDER_USER_EXTENSIONS`, `BLENDER_USER_CONFIG` and `BLENDER_USER_SCRIPTS` set to an empty scratch folder. `--factory-startup` still uses your real extensions folder, and installing or removing an extension there makes Blender resync bundled Python wheels for factory settings, which deletes the wheels of your other extensions.

The tests and generators only use generated data. Never commit game assets to the repository.

### Benchmarks

See [PERFORMANCE.md](PERFORMANCE.md#running-the-benchmarks).

### API compatibility with older Blender versions

```bash
pip install --target /tmp/stubs36 fake-bpy-module-3.6
```

```bash
pip install --target /tmp/stubs-latest fake-bpy-module
```

```bash
python tools/api_compat_check.py /tmp/stubs36 /tmp/stubs-latest .
```

It lists the Blender types, operators and node types the add-on uses that do not exist in the older version, and attribute names that only exist in the newer API. Everything it lists must be behind a feature check (see `blender/compat.py`).

## Building and releasing

```bash
python tools/build.py
```

This writes `dist/hjt_fbx_plus-<version>.zip` and its `.sha256`. The script checks that the versions in `bl_info` and `blender_manifest.toml` match, that every module compiles and that no stray large files are included. The ZIP is reproducible: the same sources give the same checksum.

To release:

1. Bump the version in `hjt_fbx_plus/__init__.py` (`bl_info["version"]`) and `hjt_fbx_plus/blender_manifest.toml`.
2. Add the changes to `CHANGELOG.md`.
3. Run the unit tests and the Blender tests on the oldest and newest supported Blender.
4. Build the ZIP, tag the commit (`v1.2.3`) and attach the ZIP to the release.

## Conventions

- Workers (`prepare_job`, analysis, extraction) must never call `bpy`. Pass anything they need (like importer availability) from the main thread.
- New Blender APIs go behind feature checks in `blender/compat.py`, not version comparisons, wherever possible.
- New settings are added to `core/options.py`. The scene properties, presets and the settings snapshot in reports are generated from that table. Settings a profile may override are listed in `profiles/schema.py` (`IMPORT_KEYS`).
- Relative imports only: the package is loaded as `hjt_fbx_plus` (legacy) or `bl_ext.<repository>.hjt_fbx_plus` (extension).
- User-facing text (labels, descriptions, messages, docs) is written in clear, professional English.
