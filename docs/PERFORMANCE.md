# Performance

HJT FBX+ is built so that the extra work it does (reading archives, analyzing files, finding textures, repairing the FBX) costs little next to Blender's own import, and so that Blender stays responsive while it happens.

## How it stays fast

- **No full extraction.** Archives are indexed from their directory; only the FBX being previewed and the textures a model actually uses are extracted.
- **Memory-mapped, lazy FBX parsing.** Large arrays (vertices, normals, UVs, weights) are not decoded unless something needs them, and the pre-import rewrite streams unchanged arrays straight from the source file.
- **Solid archives in one pass.** A solid 7z or RAR is one compressed stream, so file *N* can only be reached by decoding files *0…N-1*. HJT FBX+ keeps its 7z decoder paused between extractions and continues from there. For external extractors, it fetches everything a batch (or an analysis run) needs from an archive in a single pass.
- **Background threads.** Listing, extraction, parsing and texture search run in worker threads. Only the actual Blender import runs on the main thread, as Blender requires.
- **Caching.** Extracted files, texture indexes of folders and open archives are reused within a session; identical textures become one image.

## Benchmarks

Measured with `tests/blender/bench.py` on a 4-core Intel Xeon 2.8 GHz Linux machine with 15 GB RAM. The generated test data (`tests/make_bench_assets.py`) contains:

- **Heavy.fbx** (43.6 MB): a 200-bone rig with a 410k-vertex skinned mesh, a 1M-vertex static mesh, 8 materials with 1024² textures, and 2 animation clips. 1.41 million vertices in total.
- **many/** (153 MB, 3,052 files): 40 small models with their textures, 2,880 filler images and 12 incompressible 2048² PNGs.

### Large FBX

Median of three interleaved runs. Times are in seconds.

| | Blender 5.2.3 | Blender 4.2.23 |
|---|---:|---:|
| Preview analysis (parse, counts, texture plan) | 0.30 | 0.31 |
| Stock `import_scene.fbx` (Python importer) | 8.91 | 11.21 |
| **HJT FBX+, Python importer** | **9.13** | **11.69** |
|   prepare (parse, repair, rewrite) | 0.64 | 0.75 |
|   Blender importer | 8.46 | 10.92 |
|   finish (materials, textures, post-processing) | 0.01 | 0.01 |
| Stock `wm.fbx_import` (C++ importer) | 1.52 | n/a |
| **HJT FBX+, C++ importer** | **2.43** | n/a |
|   prepare | 0.62 | |
|   Blender importer | 1.63 | |
|   finish | 0.01 | |

The overhead is the *prepare* step, about 15 ms per MB of FBX. It reads and rewrites the file to make object mapping exact and to repair what Blender's importers cannot handle. Under it, the Blender importer runs as fast as it does on its own. Analysis peaked at about 0.3 GB of process memory, Blender's own baseline included.

### Archives with thousands of files

Blender 5.2.3. *Analyze* and *batch import* follow the real UI flow: models are analyzed when added, then imported as one batch.

| Archive (3,052 files, 151–153 MB) | Open + index | Preview one model | Analyze 40 models | Batch import 40 models | Extract 151 MB of large PNGs |
|---|---:|---:|---:|---:|---:|
| ZIP (Deflate) | 0.15 s | 0.04 s | 0.06 s | 1.01 s | 0.63 s |
| 7Z, solid LZMA2 | 0.04 s | 0.05 s | 0.06 s | 0.94 s | 0.17 s |
| 7Z, solid, AES-256, encrypted headers | 0.09 s | 0.08 s | 0.13 s | 0.93 s | 7.72 s ¹ |
| RAR5, solid (external extractor) | 0.29 s | 0.07 s | 0.12 s | 2.09 s | 1.56 s |

¹ Blender's bundled Python has no AES library, so HJT FBX+ decrypts with a vectorized numpy implementation at about 20 MB/s. Install the optional **pycryptodomex** package (one click in the preferences) for about 400 MB/s. In the same test it decrypted the 151 MB archive in 0.37 s.

Before the single-pass extraction described above, the same 40-model batch took 52 s from the AES 7z and 39 s from the solid RAR.

### Running the benchmarks

```bash
blender -b --factory-startup --python tests/make_bench_assets.py -- /tmp/hjt-bench
```

```bash
blender -b --factory-startup --python tests/blender/bench.py -- . /tmp/hjt-bench
```

Add `--profile` for a cProfile breakdown of the HJT FBX+ code, or `--archives-only` to skip the large FBX. The results are also written to `/tmp/hjt-bench/results.json`.

## Tips for large assets

- **Use the C++ importer** (*Advanced Settings › Import Engine › C++ (Fast)*, Blender 4.5+) for big meshes and batches. It is 4–6× faster than the Python importer. It ignores the bone-axis and manual-orientation options, which game assets rarely need.
- **Install pycryptodomex** if you work with password-protected 7z or AES ZIP archives.
- **Keep extracted files** (*Preferences › Clean Up › When Blender Closes*) when you re-import the same archives while iterating on settings.
- **Turn off Analyze on Add** for very large queues if you do not need the preview. Imports work without it.
- **Background threads** (preferences) can be raised to 3–4 on fast SSDs with many small archives. Extraction from a single archive is sequential either way.
- **Pack vs. copy**: packing many large textures makes the .blend big. *Copy to Folder* keeps them as separate files next to the .blend.
