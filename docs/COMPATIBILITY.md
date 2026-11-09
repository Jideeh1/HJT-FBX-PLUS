# Compatibility

## Blender versions

One ZIP installs on every supported version: it carries both a `bl_info` block (legacy add-on system, Blender 3.6–4.1) and a `blender_manifest.toml` (extension system, Blender 4.2+). Features depending on newer Blender APIs are detected at run time and degrade gracefully on older versions.

| Blender | Python | Install as | Status | Differences |
|---|---|---|---|---|
| 3.6 LTS | 3.10 | Legacy add-on | Supported, statically verified ¹ | No drag-and-drop; Python importer only; node groups use the pre-4.0 socket API; Principled BSDF v1 socket names |
| 4.0 | 3.10 | Legacy add-on | Supported, statically verified ¹ | No drag-and-drop; Python importer only |
| 4.1 | 3.11 | Legacy add-on | Supported, statically verified ¹ | Drag-and-drop available; Python importer only |
| 4.2 LTS | 3.11 | Extension | **Tested** (4.2.23) | EEVEE Next render methods (`DITHERED`/`BLENDED`) used for transparency |
| 4.3, 4.4 | 3.11 | Extension | Supported (between tested versions) | Slotted actions (4.4+) handled |
| 4.5 LTS | 3.11 | Extension | **Tested** (4.5.15) | C++ importer available |
| 5.0 | 3.11 | Extension | **Tested** (5.0.1) | Blender's default FBX importer is the C++ one; the Python one is labeled *Legacy*. Removed APIs (`shadow_method`, `ShaderNodeSeparateRGB`, `use_nodes`) avoided |
| 5.1 | 3.13 | Extension | **Tested** (5.1.2) | |
| 5.2 LTS | 3.13 | Extension | **Tested** (5.2.3) | |
| newer | | Extension | Expected to work | APIs are feature-detected rather than version-checked |

**Tested** means the full automated suite passed on that version: install/enable/disable, every panel drawn, the import operator flow, profile management and presets (`tests/blender/test_addon.py`), and the import matrix with assertions on skinning, UVs, vertex colors, tangents, custom properties, shape keys, in-betweens, animations, textures, color spaces, LODs, collisions, embedded media and every archive format with both engines (`tests/blender/test_matrix.py`).

¹ Blender 3.6, 4.0 and 4.1 builds were not available in the test environment. For those versions the code was checked against the official API stubs of each version (`tools/api_compat_check.py`): every Blender type, operator and node type the add-on uses exists there, and every newer API is behind a feature check. The package also compiles with Python 3.10. Please report any issue on these versions.

## Import engines

| | Python importer (`io_scene_fbx`) | C++ importer (`wm.fbx_import`) |
|---|---|---|
| Blender | all | 4.5+ |
| Speed on big meshes | baseline | 4–6× faster |
| FBX 7.x binary | ✓ | ✓ |
| FBX 7.x ASCII | ✓ (converted to binary by HJT FBX+) | ✓ |
| FBX 6.x (pre-2011) | – | ✓ (objects matched by name) |
| Bone orientation options, manual axes, pre/post rotation toggle | ✓ | ignored |
| In-between shape keys | kept by HJT FBX+ (the importer itself rejects them) | kept by HJT FBX+ (the importer itself drops them) |
| Files with empty animation takes (common in game exports) | ✓ | ✓ after HJT FBX+ repairs them (the importer fails on its own) |

With *Import Engine: Auto*, the Python importer is used unless the file needs the C++ importer (FBX 6.x) or the Python importer is disabled. Options an engine does not have are listed in the import report instead of failing.

## FBX sources

Tested with FBX files written by Blender, real AFK Journey character exports (Trident exporter, with Unity material JSON), and generated files covering binary FBX 7.x with 32- and 64-bit headers, ASCII 6.x and 7.x, embedded media, LOD groups, collision meshes, in-between shapes and Unity material JSON in the AssetStudio and AnimeStudio layouts. Files from other exporters that Blender's importers can read are expected to work. If one does not, *Copy Report* and *Show FBX Structure* in *Import Diagnostics* give everything needed for a bug report.

## Archives

| Format | Variants |
|---|---|
| ZIP | Store, Deflate, BZip2, LZMA; ZipCrypto and WinZip AES-128/192/256; ZIP64; UTF-8, CP437, Shift-JIS and GBK file names |
| 7Z | LZMA, LZMA2, PPMd ², BCJ, BCJ2 ², Delta, Deflate, BZip2, Zstandard ²; AES-256; encrypted headers; solid; multi-volume |
| RAR | RAR4 and RAR5 ², solid, encrypted (names and/or data), multi-volume (`.part1.rar`, `.r00`) |

² Through an external extractor or the optional py7zr package, see [DEPENDENCIES.md](DEPENDENCIES.md).

## Operating systems

HJT FBX+ is pure Python. It contains platform-specific code only for finding external extractors (Program Files and `tar.exe` on Windows; Homebrew, MacPorts and the system `tar` on macOS; standard paths on Linux) and for opening folders in the system file browser.

The automated tests ran on Linux. The Windows and macOS code paths follow the same logic and only differ in tool locations.

## Other add-ons

- No other FBX add-on is required, and HJT FBX+ does not replace or patch Blender's importers. The stock *File › Import › FBX* entry keeps working as before.
- HJT FBX+ registers its own operators (`hjt_fbx_plus.*`), properties (`Scene.hjt_fbx_plus`, `WindowManager.hjt_fbx_plus`) and a sidebar tab, and removes them completely when disabled.
