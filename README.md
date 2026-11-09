# HJT FBX+

**Advanced FBX & Game Asset Importer for Blender**, by HJT Studio

HJT FBX+ imports FBX and UEModel (`.uemodel`) files, and ZIP, 7Z and RAR archives full of them, into Blender, with textures found and wired up automatically. It works out each game asset's materials, keeps as much of the FBX data as Blender can hold, and reports exactly what it did, what it guessed and what is missing.

It is a standalone add-on. It drives Blender's own FBX importers and needs no other FBX add-on.

| | |
|---|---|
| Blender | 3.6 LTS – 5.2+ (legacy add-on on 3.6–4.1, extension on 4.2+) |
| Platforms | Windows, macOS, Linux |
| License | GPL-3.0-or-later |
| Version | 1.2.0 |

---

## Features

**Archives without unpacking**
- Open `.zip`, `.7z` and `.rar` directly. Browse the folder tree, pick models, import. Only the files a model needs are extracted, into a temporary cache that is cleaned up automatically.
- ZIP (incl. WinZip AES and legacy ZipCrypto passwords, Shift-JIS/GBK file names) and 7Z (LZMA/LZMA2/BCJ/Delta/Deflate/BZip2, AES-256, encrypted headers, solid blocks, multi-volume) are read by built-in Python code.
- RAR4/RAR5 and exotic 7Z methods use any extractor already on your system (7-Zip, UnRAR/WinRAR, The Unarchiver, bsdtar / Windows `tar.exe` / macOS `tar`).
- Clear messages for wrong passwords, corrupt or truncated archives and unsupported methods.

**FBX fidelity**
- Normals, tangents/binormals (kept as `fbx_tangent` / `fbx_binormal` attributes), all UV sets, vertex colors, materials, hierarchy, armatures, bind poses, skin weights, shape keys, **in-between shape keys** (rebuilt with drivers; Blender's importers normally reject or drop them), animation clips, LOD groups, collision meshes and custom properties.
- Exact FBX-to-Blender object mapping through hidden IDs, so materials and post-processing never confuse objects with the same name.
- A pre-import pass repairs files that crash or confuse Blender's importers (e.g. Unity/game exporter quirks), and converts ASCII FBX 7.x to binary.
- Choose Blender's Python importer or the fast C++ importer (Blender 4.5+), or let HJT FBX+ decide.

**Textures and materials**
- Textures are discovered from FBX links, embedded media, Unity material JSON (AssetStudio-style exports), game naming conventions and file-name matching — in that order of trust.
- Duplicate file names (every character has a `Body_D.png`) are resolved by folder proximity; ambiguous picks are flagged.
- Correct color spaces per slot (sRGB vs Non-Color), packed channel maps (ORM, MRA, RMA, metallic-smoothness, HDRP mask), normal-map formats (RGB/RG/AG, OpenGL/DirectX).
- Materials rebuilt as **PBR** (Principled BSDF), **Toon** (two-tone cel shading for EEVEE with a Cycles fallback), **Flat** (unlit), **Keep** (importer materials, repaired) or from **your own template material**.
- Alpha (clip/hashed/blend), emission, double-sided/backface culling and vertex colors handled per material.
- Missing textures are listed with suggestions. Assign or swap any texture slot later without re-importing.

**Game profiles**
- Built-in profiles: **Generic**, **Genshin Impact**, **Honkai: Star Rail**, **Zenless Zone Zero**, **Honkai Impact 3rd**, **AFK Journey**.
- Profiles are auto-detected per model and describe naming conventions, Unity material slots, material rules, LOD/collision patterns and rig fixes.
- Everything a profile adds is labeled: *faithful* (read from the file) vs *inferred* (guessed from names) vs *approximation* (shading that imitates a proprietary game shader).
- Create, edit, duplicate, export and import profiles as JSON, in the UI.

**Workflow**
- Import preview: counts, issues, texture plan and the archive tree, before anything touches the scene.
- Batch import with a queue, drag-and-drop (Blender 4.1+), whole folders, presets, collection organization per model or archive.
- Background threads for listing, extraction and analysis; Blender stays responsive. Progress and cancel at every stage.
- Import reports (per model: what was imported, timings, warnings, texture provenance) saved to a history you can reopen or copy.

---

## Quick start

1. Download `hjt_fbx_plus-1.2.0.zip` from the [Releases](https://github.com/Jideeh1/HJT-FBX-PLUS/releases) page (do not unzip it).
2. Blender 4.2+: *Edit › Preferences › Get Extensions › ⌄ › Install from Disk…*  
   Blender 3.6–4.1: *Edit › Preferences › Add-ons › Install…*  
   Pick the ZIP and enable **HJT FBX+**.
3. *File › Import › HJT FBX+ (.fbx, .uemodel, .zip, .7z, .rar)*, or drag a file into the 3D View (4.1+).
4. Review the models in the **HJT FBX+** sidebar tab (press `N` in the 3D View), then press **Import Selected**.

Full instructions: [docs/INSTALL.md](docs/INSTALL.md) · [docs/USAGE.md](docs/USAGE.md)

---

## Documentation

| Document | Contents |
|---|---|
| [Installation](docs/INSTALL.md) | Installing as an extension or legacy add-on, updating, uninstalling |
| [Usage](docs/USAGE.md) | The six sidebar sections, every option, batch import, reports |
| [Game profiles](docs/PROFILES.md) | Built-in profiles, what is faithful vs approximated, writing your own |
| [Dependencies](docs/DEPENDENCIES.md) | What is built in, optional extractors and Python packages |
| [Compatibility](docs/COMPATIBILITY.md) | Blender versions, importer engines, platforms |
| [Performance](docs/PERFORMANCE.md) | Benchmarks and tips for large assets |
| [Known limitations](docs/LIMITATIONS.md) | What Blender or the formats cannot do, and workarounds |
| [Development](docs/DEVELOPMENT.md) | Repository layout, tests, building the ZIP |
| [Changelog](CHANGELOG.md) | Release history |

---

## Notes

- HJT FBX+ is an independent project. It is not affiliated with or endorsed by Autodesk, the Blender Foundation, or any game developer or publisher. Game titles are trademarks of their respective owners. The game profiles contain naming rules only. No game data ships with the add-on.
- Only import assets you have the right to use.

## License

Copyright © 2026 HJT Studio.

HJT FBX+ is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. See [LICENSE](LICENSE).

HJT FBX+ calls Blender's bundled FBX importers (`io_scene_fbx`, GPL-2.0-or-later, and the C++ importer built on [ufbx](https://github.com/ufbx/ufbx), MIT/Unlicense) at run time; it does not include their code.
