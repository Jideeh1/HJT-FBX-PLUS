# Known limitations

Most limits come from what Blender can represent, from what Blender's FBX importers support, or from the archive formats themselves. Where a workaround exists, it is listed.

## FBX data

- **Blender's importers do the low-level import.** HJT FBX+ prepares the file, drives the importer and post-processes the result. FBX features neither importer supports are not imported: NURBS and patch surfaces, constraints and IK setups, HumanIK character definitions, point-cache (MC/PC2) animation, and most camera/light details beyond the basics.
- **Animation layers** are imported the way Blender's importer handles them; FBX layer weights and blend modes (additive/override) are not reproduced.
- **Tangents**: Blender always computes its own tangents for normal mapping. The FBX tangents and binormals are kept as `fbx_tangent` / `fbx_binormal` face-corner attributes for export or custom shaders, but Blender's renderers do not use them.
- **In-between shape keys** are rebuilt as separate shape keys driven from their channel. The blending matches FBX's piecewise-linear interpolation; shape-key editing tools treat them as ordinary keys.
- **FBX 6.x (pre-2011) files** need Blender 4.5+ (only the C++ importer reads them). They are imported as they are, without HJT FBX+'s pre-import repairs, and objects are matched to FBX data by name.
- **Bone length fitting** changes how the rig looks in Blender only. Bone *orientation* options (Python importer) change bone axes, which can break animations retargeted later. Leave them off for game rigs.
- **C++ importer**: ignores the bone-axis, force-connect, pre/post-rotation toggle and manual-orientation options (listed in the report when set).
- **Importer only**: HJT FBX+ does not export FBX.

## UEModel

- Only LOD0 is imported. Sockets, virtual bones and collision shapes in the file are not created.
- `.ueanim` and `.uepose` files are not imported; use the UEFormat add-on for those.
- ZSTD-compressed files need the `zstandard` Python package, which Blender ships.

## Materials and textures

- **Game shaders are approximated.** Toon materials are a two-tone cel shader. Outlines, rim lights, SDF face shadows, light-map driven shading and ramps from proprietary game shaders are not recreated. Their textures are loaded and labeled in the node tree so you can build on them. Everything approximated is labeled as such (see [PROFILES.md](PROFILES.md)).
- **Name-based texture matching is a guess.** When an FBX has no texture links and there is no material JSON, textures are matched by naming rules. Guesses are labeled *inferred*, ambiguous choices are flagged, and any slot can be reassigned (*Assign Texture to Slot…*).
- **Materials are created per model.** Two models sharing a material by name get one material each (`Body`, `Body.001`); identical *images* are shared.
- **Keep FBX Materials** only repairs image paths. It does not fix the importer's interpretation of FBX material properties.
- **Image formats** are whatever Blender can load. DDS files in BC6H/BC7 and some exotic DDS variants depend on Blender's support; `.tga`, `.png`, `.jpg`, `.exr`, `.hdr`, `.tif`, `.webp`, `.bmp` and common DDS formats work.

## Archives

- **RAR needs an external extractor** (7-Zip, UnRAR/WinRAR, The Unarchiver or bsdtar). bsdtar (the built-in `tar` of Windows 10/11 and macOS) cannot open password-protected or multi-volume RAR files. See [DEPENDENCIES.md](DEPENDENCIES.md).
- **7Z methods PPMd, BCJ2, Deflate64 and Zstandard** need 7-Zip, The Unarchiver (except Zstandard) or the optional py7zr package. The built-in reader reports clearly when this is the case.
- **Encrypted archives without pycryptodomex** decrypt at about 20 MB/s in Blender's Python. Install the optional package for about 400 MB/s.
- **Solid archives**: reaching a file means decoding everything before it in the same solid block. HJT FBX+ does this at most once per batch, but importing one small model that sits at the end of a huge solid block still takes as long as decoding the block up to it.
- **Archives inside archives** are not opened. Extract the inner archive first (*Archive Management › Extract Selected…*) and add it.
- **Self-extracting archives** (`.exe`) and other formats (tar, gzip, Unity bundles, game packages) are not supported. Use the game's ripping tool to export FBX first.
- **Corrupt archives**: entries that fail their CRC check are reported and never used; HJT FBX+ does not attempt repairs.

## Workflow

- **Cancelling**: Esc stops a batch after the current step. Blender's importer call itself cannot be interrupted, so a single huge model finishes its import step first.
- **Undo**: a batch import is one undo step. Very large imports use as much undo memory as Blender's own importer would.
- **Drag and drop** needs Blender 4.1 or newer.
- **Blender 3.6–4.1** are verified by static API checks, not by the automated runtime tests (see [COMPATIBILITY.md](COMPATIBILITY.md)).
- **Windows and macOS**: tested through the same code paths as Linux. Only external-tool discovery and opening folders differ between platforms.
