# Installation

HJT FBX+ ships as one ZIP file, `hjt_fbx_plus-<version>.zip`, that installs on every supported Blender version. Do not unzip it.

## Blender 4.2 and newer (extension)

1. *Edit › Preferences › Get Extensions*.
2. Open the **⌄** menu at the top right and choose **Install from Disk…**
3. Select `hjt_fbx_plus-1.2.0.zip`.
4. The extension is enabled automatically. You will find it under *Add-ons* as **HJT FBX+**.

## Blender 3.6 – 4.1 (legacy add-on)

1. *Edit › Preferences › Add-ons › Install…*
2. Select `hjt_fbx_plus-1.2.0.zip` and click **Install Add-on**.
3. Tick the checkbox next to **Import-Export: HJT FBX+**.

## Check that it works

- *File › Import* has an entry **HJT FBX+ (.fbx, .zip, .7z, .rar)**.
- The 3D View sidebar (`N`) has an **HJT FBX+** tab with six sections: Import, Game Profiles, Material Reconstruction, Advanced Settings, Archive Management and Import Diagnostics.
- *Preferences › Add-ons › HJT FBX+* shows the cache settings and which archive extractors were found.

HJT FBX+ uses Blender's FBX importer. The bundled **FBX format** add-on (`io_scene_fbx`) must be enabled, which is the default. On Blender 4.5+ the built-in C++ importer is used automatically when the Python one is disabled.

## Optional extras

Nothing else is required for FBX, ZIP and most 7Z files. For RAR archives, 7Z files using PPMd/BCJ2/Zstandard and faster decryption, see [DEPENDENCIES.md](DEPENDENCIES.md).

## Updating

Install the new ZIP the same way. Blender replaces the previous version. Restart Blender afterwards so every module is reloaded.

Your game profiles, presets, import history and logs live outside the add-on folder (see below) and survive updates.

## Uninstalling

- Extension (4.2+): *Preferences › Add-ons › HJT FBX+ › ⌄ › Uninstall*.
- Legacy add-on: *Preferences › Add-ons › HJT FBX+ › Remove*.

Then delete the user folder if you no longer need your profiles and history.

## Where things are stored

| What | Location |
|---|---|
| User profiles, import history, logs | Blender 4.2+ extension: the extension's user folder (`.../extensions/.user/<repository>/hjt_fbx_plus/`). Legacy add-on: `<Blender config folder>/hjt_fbx_plus/` |
| Import presets | `<Blender user scripts>/presets/hjt_fbx_plus/import/` |
| Temporary extraction cache | `<system temp folder>/hjt_fbx_plus_cache/session-*`, or the *Cache Folder* set in the preferences |
| Optional Python packages | `<user folder>/lib/py3XX/`, never Blender's own `site-packages` |

Extracted files are deleted as soon as each import finishes (textures are packed into the .blend first); HJT FBX+ never keeps models on disk. Leftovers from a crashed Blender are removed the next time the add-on starts.
