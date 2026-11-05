# Game profiles

A game profile describes how a game's exported assets are organized, so HJT FBX+ can find the right textures, build suitable materials and tidy up the scene. Profiles are plain JSON files that you can edit in the sidebar (*Game Profiles › Edit*), or in any text editor, and share.

## Faithful, inferred and approximated

HJT FBX+ never presents a guess as fact. Every texture and material is labeled in the material info box, the import report and the material's `hjt_fbx` custom property:

| Label | Meaning | Examples |
|---|---|---|
| **Faithful** | Taken from data in the export | Textures linked or embedded in the FBX; texture slots and parameters from the original Unity material JSON; normals, UVs, weights, shape keys |
| **Inferred** | Derived from naming rules | `Hair_N.png` used as the normal map of material `Hair`; a "Dress" part falling back to the body texture |
| **Approximation** | Shading that imitates a proprietary game shader | Two-tone toon shading driven by the game's shadow color and threshold; light maps, ramps and SDF face maps attached but not wired |

Game shaders (cel shading, face shadows, rim lights, outlines) cannot be reproduced exactly in Blender without the original shader code. HJT FBX+ builds a clean, editable approximation and keeps every game texture in the node tree, labeled with its role, so you can build your own shader on top.

## Built-in profiles

| Profile | Detects | Texture sources | Default material | Notes |
|---|---|---|---|---|
| **Generic FBX** | Fallback for everything | FBX links, Unity JSON, common suffixes (`_D`, `_N`, `_ORM`, `BaseColor`, `Normal`, `Roughness`, …) | PBR | Faithful import for any FBX |
| **Genshin Impact** | `Avatar_*_Mat_<Part>` materials, `*_Tex_<Part>_Diffuse/Lightmap/Normalmap`, face light maps | Material names → `{prefix}_Tex_{part}_*`; *Dress* parts fall back to Body/Hair (flagged as inferred) | Toon | Diffuse and normal maps faithful; light maps, shadow ramps and face maps attached as auxiliary textures |
| **Honkai: Star Rail** | `*_Color`, `*_LightMap`, `*_Cool_Ramp` / `*_Warm_Ramp`, face maps, stockings, expression maps | Material and part names | Toon | Base colors faithful; game-shader data maps auxiliary |
| **Zenless Zone Zero** | `MAT_<Name>_UI` materials with a `Materials/` folder of Unity JSON | Unity JSON (`_MainTex`/`_D` base color, `_N` normal, `_M`/`_A` data maps) | Toon | Hair-shadow stencil and outline meshes are hidden, not deleted |
| **Honkai Impact 3rd** | `Avatar_<Name>_C<n>_…` conventions, `*_Color`, `*_LightMap`, `*_Ramp`, face shadows | Material and part names | Toon | Base colors faithful; game-shader data maps auxiliary |
| **AFK Journey** | `pbsc_char_*_show` materials, `iGame/Char/*` shaders, Trident exporter | Unity JSON in `Materials/` (`_MainTex`, `_Mask`, `_MiscMap`, `_SDF`, `_MatCapTex`, …) | Toon, driven by the game's `_DarkTint`, `_ShadowThreshold`, `_ShadowFeather`; shadow-pass materials unlit | Bone lengths fitted (cosmetic); skinning verified |

The naming rules for game rips follow common community export tools. Rips made with other tools may name things differently: duplicate the profile and adjust its rules.

### Automatic detection

With *Profile: Auto Detect*, every model is scored against every profile's detection rules. A rule adds its weight once if **any** name of its target matches its regular expression. The highest score at or above the profile's `min_score` wins; otherwise *Generic FBX* is used. The preview shows which profile was picked, and you can override it for all imports or pin one with **Use for Imports**.

Detection targets: `material`, `texture` (texture file names in the FBX and next to it), `mesh`, `bone`, `creator` (the exporting application), `file` (the file or archive member path), `shader` (Unity shader names from material JSON), `any`.

## Creating and editing profiles

- **Duplicate** a built-in profile and edit the copy, or **Add** an empty one (it starts from the Generic defaults).
- **Edit** loads a profile into the *Profile Editor* sub-panel with the most common fields (name, description, default template, material options, slot rules, Unity slot names, material rules, categories, corrections). Press **Save Profile**.
- **Edit JSON** opens the whole profile in Blender's Text Editor; **Apply JSON** validates it and saves it. Problems (bad regular expressions, unknown slots or import settings, invalid values, missing id or name) are reported and nothing is saved until they are fixed.
- **Export** writes a `.json` file to share; **Import** adds one.
- Editing a built-in profile saves a customized copy that overrides it. **Delete** on that copy restores the original.

User profiles are stored as `<id>.json` in the `profiles` folder of the add-on's user folder (see [INSTALL.md](INSTALL.md)). Files dropped there appear after **Reload**.

## Profile format reference

Anything missing from a profile falls back to the Generic defaults, so a profile only needs the parts it changes. All patterns are Python regular expressions, matched case-insensitively against file names without the extension unless noted.

```json
{
  "schema": 1,
  "id": "my_game",
  "name": "My Game",
  "description": "What this profile is for, what is faithful and what is approximated.",
  "author": "You",
  "version": "1.0",

  "detect": {
    "min_score": 3,
    "rules": [
      {"target": "material", "pattern": "^MG_.+_Mat$", "weight": 3},
      {"target": "creator", "pattern": "(?i)mygame exporter", "weight": 2}
    ]
  },

  "import": {"use_custom_normals": true, "ignore_leaf_bones": true},

  "textures": {
    "slots": [
      {"slot": "base_color", "pattern": "_col$"},
      {"slot": "lightmap", "pattern": "_ilm$"}
    ],
    "replace_default_slots": false,
    "unity_slots": {"_CustomColorTex": "base_color", "_ToonRamp": "ramp"},
    "name_templates": [
      {"material": "(?i)^(?P<prefix>.+)_Mat_(?P<part>[A-Za-z]+)$", "textures": ["{prefix}_Tex_{part}", "{prefix}_{part}"]}
    ],
    "part_fallbacks": {"dress": ["body", "hair"]},
    "strip_material_tokens": ["(?i)^(?:mat|mtl)[_\\- ]+"],
    "use_fbx_references": true,
    "use_material_json": true,
    "prefer_material_json": true,
    "name_matching": true,
    "search_depth": 4,
    "search_parent_levels": 1
  },

  "materials": {
    "default": {"template": "TOON", "shadow_tint": [0.8, 0.75, 0.85], "shadow_threshold": 0.5},
    "rules": [
      {"match": "(?i)_face$", "category": "Face", "settings": {"backface_culling": "ON"}},
      {"match": "(?i)glass", "category": "Effect", "settings": {"alpha_mode": "BLEND"}},
      {"match": "(?i)^Hidden/", "target": "shader", "settings": {"template": "FLAT"}}
    ]
  },

  "categories": {
    "lod": {"enabled": true, "action": "HIDE_LOWER", "patterns": ["(?i)_lod(\\d+)$"]},
    "collision": {"enabled": true, "action": "WIRE_HIDE", "patterns": ["^UCX_"]},
    "custom": [{"name": "Outline mesh", "pattern": "(?i)_outline$", "action": "HIDE"}]
  },

  "corrections": {"fit_bone_lengths": true, "verify_skinning": true}
}
```

### `detect`

| Key | Description |
|---|---|
| `min_score` | Minimum total weight for the profile to be picked. |
| `rules[]` | `target` (see *Automatic detection*), `pattern`, `weight` (default 1). |

### `import`

Overrides for import settings when *Profile Import Settings* is on: `engine`, `global_scale`, `use_custom_normals`, `colors_type`, `use_anim`, `anim_offset`, `ignore_leaf_bones`, `force_connect_children`, `automatic_bone_orientation`, `primary_bone_axis`, `secondary_bone_axis`, `use_prepost_rot`, `use_custom_props`, `use_subsurf`, `keep_tangents`, `inbetween_drivers`. The values are the same as in *Advanced Settings*.

### `textures`

| Key | Description |
|---|---|
| `slots[]` | `{"slot": ..., "pattern": ...}`: file-name rules checked **before** the built-in suffix rules. |
| `replace_default_slots` | `true` to use only this profile's slot rules. |
| `unity_slots` | Unity material property → slot, added to the built-in table (`_MainTex`, `_BaseMap`, `_BumpMap`, `_MetallicGlossMap`, `_MaskMap`, `_EmissionMap`, `_LightMap`, `_RampTex`, `_SDF`, …). Unknown properties become auxiliary textures. |
| `name_templates[]` | `material`: a regex with named groups. `textures`: texture name stems built from the groups (`{prefix}`, `{part}`, any group name); slot suffixes are then matched as usual. `slots` (optional): limit the template to these slots. |
| `part_fallbacks` | Part name → other parts to borrow textures from when a part has none (marked inferred). |
| `strip_material_tokens` | Regexes removed from material names before name matching (`MAT_`, `_mtl`, `.001`, …). |
| `use_fbx_references`, `use_material_json`, `name_matching` | Turn texture sources on or off for this profile (the sidebar options can only turn them off further). |
| `prefer_material_json` | When both exist, trust the Unity JSON over the FBX's own texture links. |
| `search_depth`, `search_parent_levels` | Folder search limits for loose files. |

**Slots**: `base_color`, `normal`, `metallic`, `roughness`, `smoothness`, `specular`, `ao`, `emission`, `emission_mask`, `opacity`, `height`, `orm`, `mra`, `rma`, `metallic_smoothness`, `hdrp_mask` (wired into the material), and `mask`, `lightmap`, `ramp`, `face_map`, `matcap`, `detail`, `env`, `misc`, `stocking`, `expression`, `aux` (auxiliary: loaded and labeled, not wired).

### `materials`

`default` holds the material settings for every material; each `rules[]` entry (`match` regex, optional `target`: `material` (default), `shader` or `mesh`) merges its `settings` on top and can set a `category` label shown in the report. All matching rules apply in order.

| Setting | Values | Default | Description |
|---|---|---|---|
| `template` | `PBR`, `TOON`, `FLAT`, `KEEP`, `TEMPLATE` | `PBR` | Material builder (see [USAGE.md](USAGE.md#3-material-reconstruction)). |
| `template_material` | material name | – | Reference material for `TEMPLATE`. |
| `alpha_mode` | `AUTO`, `OPAQUE`, `CLIP`, `HASHED`, `BLEND` | `AUTO` | Transparency mode. `AUTO` reads the Unity blend/cutout settings or the texture's alpha. |
| `alpha_source` | `AUTO`, `BASE_ALPHA`, `OPACITY_MAP`, `NONE` | `AUTO` | Where alpha comes from. |
| `alpha_threshold` | 0–1 | 0.5 | Cutoff for `CLIP`. |
| `base_alpha_usage` | `AUTO`, `NONE`, `TRANSPARENCY`, `EMISSION_MASK` | `AUTO` | What the base color's alpha channel means. Many game shaders store a mask there, not transparency. |
| `backface_culling` | `AUTO`, `ON`, `OFF` | `AUTO` | `AUTO` follows the Unity `_Cull`/`_CullMode` value (double-sided when culling is off). |
| `vertex_color` | `NONE`, `MULTIPLY`, `AUTO` | `NONE` | Multiply the base color by the vertex colors. |
| `normal_format` | `AUTO`, `RGB`, `RG`, `AG` | `AUTO` | 2-channel normal maps get their Z reconstructed. `AUTO` detects BC5 DDS files. |
| `normal_convention` | `OPENGL`, `DIRECTX` | `OPENGL` | `DIRECTX` flips the green channel. |
| `normal_strength` | number | 1.0 | Normal Map strength. |
| `ao_mode` | `AUX`, `MULTIPLY` | `AUX` | Keep AO as a labeled texture, or multiply it into the base color. |
| `emission_strength` | number | 1.0 | Emission strength. |
| `roughness`, `metallic`, `specular` | number or `null` | `null` | Constant values when there is no map (`null` = Blender's default or the Unity value). |
| `shadow_tint` | `[r, g, b]` | `[0.78, 0.74, 0.84]` | Toon: shadow color multiplier (replaced by the game's own value when the Unity JSON has one). |
| `shadow_threshold`, `shadow_feather` | 0–1 | 0.5, 0.02 | Toon: where the shadow starts and how soft its edge is. |
| `use_material_params` | `true`/`false` | `true` | Use colors and values from the Unity JSON (`_Color`, `_Cutoff`, `_BumpScale`, …). |

### `categories`

| Key | Description |
|---|---|
| `lod` | `enabled`, `action` (`HIDE_LOWER`: keep LOD0 visible and hide the rest; `COLLECTIONS`: one sub-collection per level; `KEEP`), `patterns` (the first group is the LOD number). FBX `LodGroup` nodes are used as well when the file has them. |
| `collision` | `enabled`, `action` (`WIRE_HIDE`: wireframe and hidden in renders; `HIDE`; `KEEP`), `patterns`. |
| `custom[]` | `name`, `pattern`, optional `target` (`object`, the default, matches FBX object names; `material` matches the object's material names), `action` (`HIDE`, `WIRE`, `COLLECTION`, `KEEP`). For things like outline meshes, shadow proxies, effect cards. |

Objects are never deleted. Categories only change visibility, display and collections.

### `corrections`

| Key | Description |
|---|---|
| `fit_bone_lengths` | Resize bones so each chain's bones reach their children. Many game exports have tiny or giant bones; this only changes how the rig looks in Blender, never the deformation or animation. |
| `verify_skinning` | Check and repair the binding of skinned meshes to their armature. |

## Tips for writing rules

- Start from the closest built-in profile and use the import preview to check the result: the texture counts (faithful / inferred / missing) and warnings update when you press *Refresh*.
- *Show FBX Structure* (Import Diagnostics) lists the exact material, mesh and texture names in a file.
- Keep `min_score` high enough that your profile does not grab unrelated files; give the most specific rules the most weight.
- Put the most specific slot rules first: the first matching rule wins.
