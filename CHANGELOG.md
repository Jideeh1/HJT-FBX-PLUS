# Changelog

All notable changes to HJT FBX+ are documented here. The project follows [Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-11-10

### Added
- UEModel import (`.uemodel`, UEFormat versions 0-10 as exported by FModel), loose or inside ZIP/7Z/RAR, uncompressed, GZIP or ZSTD. Meshes, all UV sets, vertex colors, custom normals, material sections, weights, morph targets and the skeleton, read on the worker thread.
- Characters split into several `.uemodel` files (body, face, hair...) get an *all parts* entry in the archive picker that imports every part onto one shared armature.
- FModel material JSON (`Textures` / `Parameters`) is read as a faithful material source, with UE parameter names mapped to texture slots and engine placeholder textures skipped.

### Changed
- The updater checks every release on github.com/Jideeh1/HJT-FBX-PLUS and offers the highest version, instead of only the release GitHub flags as Latest.
- The import button is now *Import FBX/UEMODEL*, and the sidebar links to hjtstudio.com for more models.
- Extracted models are always deleted when an import finishes and the archive's model list is cleared; the *Clean Up* keep options are gone.

### Fixed
- Clicking import after undoing an import re-imported the previous model instead of opening the file browser.
- Bone chain alignment no longer turns half of a rig vertical. Bones with twist, pad or corrective children now pick the real chain child (on the bone's own axis, largest subtree), and forks and leaves follow their own local axis instead of copying the parent's world direction.
- Re-aligning an aligned rig no longer shrinks root bones each time.

## [1.0.0] - 2026-11-04

First release.

### Import
- Import FBX files (binary 7.x, ASCII 6.x/7.x, FBX 6.x on Blender 4.5+) and ZIP, 7Z and RAR archives containing them, from *File › Import*, the sidebar, a folder scan or drag and drop (Blender 4.1+).
- Import queue with a preview per model: counts, FBX details, detected profile, texture plan (faithful / inferred / missing) and issues.
- Batch import with progress and cancel, one collection per model or per archive, LOD and collision handling, metadata on the collections.
- Both of Blender's importers: the Python importer (`io_scene_fbx`) and the C++ importer (Blender 4.5+), selected automatically or by choice.
- Pre-import repair: exact object mapping through hidden IDs, normalization of exporter quirks, empty animation takes (which make Blender's C++ importer fail), missing sections, ASCII to binary conversion.
- Kept from the FBX: custom normals, tangents/binormals (as attributes), all UV sets, vertex colors, hierarchy, armatures, bind poses, weights, shape keys, in-between shape keys (with drivers), animation clips, LOD groups, custom properties.
- Skinning verification and repair; optional bone-length fitting for game rigs.

### Archives
- Built-in ZIP reader with WinZip AES, ZipCrypto and Shift-JIS/GBK file-name recovery.
- Built-in 7z reader: LZMA, LZMA2, BCJ filters, Delta, Deflate, BZip2, AES-256, encrypted headers, solid blocks, multi-volume.
- RAR4/RAR5 and exotic 7z methods through 7-Zip, UnRAR/WinRAR, The Unarchiver or bsdtar, detected automatically.
- Password handling with clear wrong-password and corruption messages; archive tree browser; extraction to a folder; temporary cache with cleanup policies and a size limit.
- Single-pass extraction from solid archives and external tools for whole batches.

### Textures and materials
- Texture discovery from FBX links and embedded media, Unity material JSON (AssetStudio/AnimeStudio-style exports), profile naming templates and file-name matching, with duplicate-name resolution by folder proximity.
- Color spaces per slot, packed channel maps (ORM, MRA, RMA, metallic-smoothness, HDRP mask), normal-map formats and conventions, tiling/offset.
- Material templates: PBR, Toon, Unlit, Keep FBX Materials and user template materials. Alpha modes, emission, backface culling and vertex colors.
- Rebuild materials, find missing textures and assign textures to slots after import.
- Texture storage: pack, copy to a folder or reference; identical textures shared as one image.

### Game profiles
- Built-in profiles: Generic FBX, Genshin Impact, Honkai: Star Rail, Zenless Zone Zero, Honkai Impact 3rd, AFK Journey.
- Automatic profile detection; profile editor, JSON editing with validation, import/export.
- Every texture and material labeled as faithful, inferred or approximation.

### Diagnostics
- Per-run reports (engine, fixes, timings, materials and texture origins, warnings) with history, clipboard copy and FBX structure dump.

### Compatibility
- Blender 3.6 LTS to 5.2 LTS; one ZIP for the legacy add-on system and the extension platform.
