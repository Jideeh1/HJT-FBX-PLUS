# Usage

HJT FBX+ lives in two places:

- **File › Import › HJT FBX+ (.fbx, .zip, .7z, .rar)**: pick one or more FBX files and/or archives.
- **3D View › Sidebar (`N`) › HJT FBX+**: the queue, preview, settings and tools, in six sections.

On Blender 4.1 and newer you can also **drag and drop** `.fbx`, `.zip`, `.7z` and `.rar` files onto the 3D View.

## The basic workflow

1. **Add** files: *File › Import › HJT FBX+*, the **Add FBX / Archive** button, the folder button (scans a folder and its subfolders), or drag and drop.
   - In the file browser, **Preview Before Import** (on by default) adds the models to the queue so you can check them first. Turn it off to import the picked files straight away (also when they are already in the queue).
   - Archives are read in the background; their FBX files appear in the queue as soon as the listing is done.
2. **Review** each model in the *Import* section: mesh, material, bone, animation and shape-key counts, the FBX version and creator, the detected game profile, how many textures were found (faithful / inferred / missing) and any issues.
3. **Import**: check the models you want and press **Import N Models**. Progress shows in the sidebar and the status bar. **Esc** cancels after the current step.
4. **Check the result** in *Import Diagnostics*: a summary, every message per model, and the full report.

By default each model goes into its own collection, with collision meshes and other detected categories in sub-collections, and with the source file, profile, engine and FBX details stored as custom properties on the collection.

## 1. Import

| Control | What it does |
|---|---|
| **Add FBX / Archive** | Opens the HJT FBX+ file browser (same as *File › Import*). |
| Folder button | Adds every FBX file and archive found in a folder (up to 6 levels deep). |
| **Profile** | *Auto Detect* (recommended) or a specific game profile for all imports. |
| Queue | One row per model. The checkbox includes it in the next import. The icon shows the status: pending, analyzing, ready, has warnings, error, imported. |
| **Select** | Check all, none, invert, only models without issues, or only models not imported yet. |
| Refresh | Analyze the checked models again (after changing the profile or texture settings). |
| Remove / Clear | Remove the active model, or everything, from the queue. Nothing is deleted from disk. |
| **Import N Models** | Imports every checked model, one after another. Imported models are unchecked afterwards; check one again to import it again. |

Model preview (the active model):

- Counts: meshes, materials, bones, animation clips, vertices, shape keys.
- FBX format and version, the application that wrote it, the detected profile.
- Textures: **faithful** (linked in the FBX or listed in a material JSON), **inferred** (found by naming rules), **missing**.
- Issues found while reading the file (e.g. in-between shapes, unsupported features, missing textures) and the first animation clips.

### UEModel files

`.uemodel` files (FModel / UEFormat exports) import like FBX: pick one in the file browser, drop it on the 3D View, or pick an archive that contains them. FModel material JSON files next to the model (or in a `Materials` folder) are used to wire textures. When a folder holds several parts of one character (body, face, hair...), the archive picker lists **<folder> (all N parts)** first; it imports every part onto one shared armature. Bone chains on UEModel rigs are always aligned when *Align Bone Chains* is set to *From Profile*, because raw Unreal bone axes point sideways in Blender.

If *Analyze on Add* is turned off in the preferences, models show **Not analyzed yet** with an **Analyze** button. They can still be imported without it.

## 2. Game Profiles

A profile tells HJT FBX+ how a game's exports are organized: texture naming, Unity material slots, material templates, LOD/collision naming and rig fixes. See [PROFILES.md](PROFILES.md).

| Control | What it does |
|---|---|
| **Use** | The profile used for imports (*Auto Detect* picks one per model). |
| List | Built-in and your own profiles. |
| Add / Duplicate / Delete | Create an empty profile, copy the selected one to customize it, delete a user profile. Deleting your edited copy of a built-in profile restores the original. |
| Import / Export | Load a profile from a `.json` file or save the selected one to share it. |
| Reload | Re-read all profile files from disk. |
| **Use for Imports** | Pin the selected profile instead of auto-detection. |
| **Edit** | Opens the **Profile Editor** sub-panel: name and description, default material template and options, slot rules, Unity slot names, material rules, LOD/collision patterns, rig corrections. **Edit JSON** opens the full profile in the Text Editor; **Apply JSON** validates and saves it. |

Editing a built-in profile saves your changes as a customized copy in your user folder. The original stays untouched.

## 3. Material Reconstruction

| Option | Default | Description |
|---|---|---|
| **Materials** | From Profile | *From Profile* uses the template the profile picks for each material. Or force one for everything: *PBR (Principled)*, *Toon (EEVEE)* (two-tone cel shading in EEVEE, Principled output for Cycles), *Unlit* (texture colors as they are), *Keep FBX Materials* (the FBX importer's materials, with broken image paths repaired), *Template Material* (copy your own material, see below). |
| Template Material | – | For *Template Material*: the name of a material in the current file. Its image nodes are filled by **label**: `base_color`, `normal`, `metallic`, `roughness`, `emission`, `ao`, `opacity`, … or the Unity property names (`_MainTex`, `_BumpMap`, …). |
| **FBX Texture Links** | On | Use texture files linked or embedded in the FBX. |
| **Material JSON** | On | Read Unity material JSON (`Materials/<material>.json`) exported by AssetStudio-style tools. These give exact texture assignments and shader parameters. |
| **Match by Name** | On | When the FBX has no links: find textures by material, mesh and profile naming conventions (e.g. `Body_D.png`, `Hair_N.png`). |
| Search Depth | 4 | Folder levels searched below the FBX. |
| Parent Levels | 1 | Parent folders of a loose FBX also searched (inside archives the whole archive is searched). |
| **Texture Storage** | Auto | *Auto*: pack textures from archives into the .blend, reference loose files where they are. *Pack into .blend*: always pack. *Copy to Folder*: copy into the Texture Folder (`//textures` = next to the saved .blend), one sub-folder per model, and link them there; files with the same name but different content are never overwritten. *Reference Originals*: keep the original paths (textures from archives are still packed, because the extraction cache is temporary). |
| Reuse Images | On | Reuse an image already in the file when it is the same texture (same archive entry or file), instead of loading duplicates. |

**Tools for selected objects** (work on materials created by HJT FBX+):

- **Rebuild Materials**: rebuild with another template without re-importing (e.g. switch a character from Toon to PBR).
- **Find Missing Textures**: search a folder recursively for missing image files and relink them by file name.
- **Assign Texture to Slot…**: put any image file into a slot (base color, normal, ORM, emission, …) of the active material and rebuild it.

The info box shows the active material's shading type, how many textures are faithful or inferred, the original game shader (when known) and notes about approximations.

### How texture slots are wired

| Slot | Color space | Connection |
|---|---|---|
| Base color | sRGB | Base Color (× tint × vertex color × AO when enabled) |
| Normal | Non-Color | Normal Map node. DirectX maps are flipped in green; 2-channel maps (RG / AG, e.g. BC5) are reconstructed |
| Metallic, Roughness, Specular, AO | Non-Color | Their sockets. Smoothness maps are inverted to roughness |
| ORM / MRA / RMA, metallic-smoothness, HDRP mask | Non-Color | Split into the right channels |
| Emission, emission mask | sRGB / Non-Color | Emission Color/Strength |
| Opacity, base color alpha | Non-Color | Alpha (clip, hashed or blend per material) |
| Light maps, ramps, face shadow / SDF maps, matcaps, detail, misc | per slot | Loaded and **labeled** in the node tree but not wired: they drive proprietary game shaders |

## 4. Advanced Settings

Import presets are in this section's header (the preset icon). They save every import setting, and four come built in: *Faithful (Default)*, *Fast Batch (C++ engine)*, *Keep FBX Materials* and *Textures to Folder*.

**General**

| Option | Default | Description |
|---|---|---|
| Import Engine | Auto | *Auto* uses Blender's Python importer (most options) and switches to the C++ importer (Blender 4.5+) for files only it can read (FBX 6.x). *Python (Legacy)* / *C++ (Fast)* force one. The C++ importer is several times faster on big meshes but ignores the bone-axis and manual-orientation options. |
| Scale | 1.0 | Extra scale on top of the FBX unit conversion. |
| Profile Import Settings | On | Let the game profile override import settings it cares about. |

**Geometry & Data**

| Option | Default | Description |
|---|---|---|
| Custom Normals | On | Import the FBX normals exactly (recommended for game assets). |
| Vertex Colors | sRGB | sRGB, Linear or none. |
| Keep Tangents | On | Store the FBX tangents/binormals as `fbx_tangent` / `fbx_binormal` face-corner attributes. Blender recomputes its own tangents for rendering. |
| Custom Properties | On | FBX user properties become custom properties. |
| Subdivision Data | Off | Import FBX subdivision settings as modifiers. |
| Keep In-between Shapes | On | In-between shape keys become their own shape keys (Blender's importers otherwise abort or drop them). |
| Rebuild In-between Blending | On | Drivers blend the in-betweens from the original channel the way FBX does. |

**Scene Organization**

| Option | Default | Description |
|---|---|---|
| Collections | Per Model | *Per Model*, *Per Archive* (an archive collection with model collections inside) or *Active Collection*. |
| Category Sub-collections | On | Collision meshes and other categories get their own sub-collections. |
| Handle LODs | On | Detect LOD meshes (`_LOD1`, `LOD_2`, …) and hide the lower levels. Nothing is deleted. |
| Handle Collision | On | Detect collision meshes (`UCX_`, `UBX_`, `_col`, `collider`, …): shown as wireframe and hidden in renders. |
| Store Source Info | On | Source file, profile, engine and FBX info as custom properties on the model collection. |
| Stop Batch on Error | Off | Stop at the first failed model instead of continuing. |

**Armature**

| Option | Default | Description |
|---|---|---|
| Fit Bone Lengths | From Profile | Resize bones so chains meet end to end (game exports often have tiny or huge bones). Cosmetic only: skinning, poses and animation are unchanged. |
| Verify Skinning | On | Check that every skinned mesh is bound to its armature (modifier, parent, vertex groups) and repair it if not. |
| Ignore Leaf Bones | Off | Skip FBX end bones. |
| Force Connect Children, Automatic Bone Orientation, Primary/Secondary Bone Axis, Use Pre/Post Rotation | Blender defaults | Passed to the Python importer. Changing bone orientation changes bone axes, which breaks game animations retargeted later; leave them alone unless you know you need them. |

**Animation**: import clips on/off, and the frame offset (default 1).

**Orientation**: manual axis override (forward/up) and *Apply Transform* (bake the axis conversion; experimental in Blender). Python importer only.

## 5. Archive Management

| Element | What it does |
|---|---|
| Sources list | Every added file or archive with its type, size and status (reading, ready, locked, error). |
| Details | The reader used (built-in or an external tool), encryption, solid archive, notes. |
| **Password** + **Unlock** | For encrypted archives (or encrypted file names). A wrong password is reported as such. |
| Archive tree | Browse folders with FBX/image counts. Filter by name or type, expand/collapse all. Clicking an FBX selects it in the queue. |
| **Extract Selected / Extract All** | Extract the selected folder or file, or the whole archive, to a folder of your choice. |
| Cache | Space used by extracted temporary files, **Clear Cache**, open the cache folder. |
| Formats | Which archive types this computer can read, and **Detect Tools** to look for 7-Zip, UnRAR, The Unarchiver and bsdtar again. |

## 6. Import Diagnostics

- The last run: models imported, warnings, errors.
- Messages, filterable by level, per model: what was fixed in the file, which engine was used, textures that were guessed or missing, approximations, skinning repairs.
- **Open Report**: the full report in the Text Editor (per model: source, profile, engine, timings, applied fixes, materials with the origin of every texture, statistics). **Copy** puts it on the clipboard for bug reports.
- **Show FBX Structure**: a readable dump of the active model's FBX node tree and summary, for troubleshooting.
- **History**: previous runs with their reports (the number kept is set in the preferences).
- **Open Log Folder**: the add-on's log files.

## Preferences

*Edit › Preferences › Add-ons › HJT FBX+*:

| Preference | Default | Description |
|---|---|---|
| Cache Folder | system temp | Where archive contents are extracted temporarily. |
| Size Limit (MB) | 0 (none) | The oldest extracted archives are removed when the cache grows past this. |
| Background Threads | 2 | Threads for archive reading, extraction and analysis. |
| Analyze on Add | On | Analyze models as soon as they are added. |
| 7-Zip / UnRAR / unar / bsdtar | auto | Paths to external tools, if they are not found automatically. |
| Optional packages | – | Install *pycryptodomex* (fast AES) or *py7zr* (extra 7z methods) with one click. |
| Debug Logging | Off | Detailed log file. |
| Reports to Keep | 50 | Size of the import history. |

## Scripting and command line

The import operators work without a window (`blender -b`). The batch then runs synchronously:

```python
import bpy
bpy.ops.hjt_fbx_plus.import_files(
    directory="/path/to/downloads/",
    files=[{"name": "Character.zip"}, {"name": "Props.7z"}],
    preview=False,           # import straight away
)
```

Settings are read from `bpy.context.scene.hjt_fbx_plus` (the same properties as in the sidebar), for example `scene.hjt_fbx_plus.material_mode = "PBR"`. For an encrypted archive pass `password="..."`.

## Tips

- **Characters from Unity games** (AssetStudio, AnimeStudio and similar exports): keep the `Materials/` JSON files next to the FBX. They are the most accurate texture source.
- **Large batches from one archive**: everything a batch needs from a solid 7z or RAR is extracted in a single pass, so importing 40 models takes about as long as importing one big one.
- **Re-importing the same character** reuses its images (*Reuse Images*), so the .blend does not fill up with duplicates.
- **Shared texture names** across characters (`Body_D.png` in every folder) are resolved by folder: the texture closest to the FBX wins, and a warning is shown when the choice is ambiguous.
