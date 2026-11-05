# Dependencies

HJT FBX+ needs nothing besides Blender for FBX files, ZIP archives and most 7Z archives. Everything else is optional and only used when present.

## Built in (no installation)

| Feature | Implementation |
|---|---|
| FBX import | Blender's own importers: `io_scene_fbx` (bundled add-on, all versions) and the C++ importer (Blender 4.5+) |
| FBX reading, analysis, repair, ASCII → binary | HJT FBX+'s own pure-Python FBX reader and writer |
| ZIP | Python `zipfile` + HJT FBX+ code for WinZip AES, legacy ZipCrypto and non-UTF-8 file names. Methods: Store, Deflate, BZip2, LZMA |
| 7Z | HJT FBX+'s own reader on Python's `lzma`, `zlib` and `bz2`. Methods: Copy, LZMA, LZMA2, Deflate, BZip2, BCJ (x86, ARM, ARMT, PPC, SPARC, IA64), Delta, 7zAES. Encrypted headers, solid blocks, empty files, multi-volume `.7z.001…` |
| AES decryption | Vectorized numpy implementation (numpy ships with Blender), about 20 MB/s; pure-Python fallback without numpy |
| Images | Blender. HJT FBX+ reads image headers itself (PNG, JPEG, TGA, DDS, BMP, TIFF, EXR, HDR, WebP, …) to pick color spaces and detect formats |

## External extractors (optional)

Needed for **RAR** archives (RAR4 and RAR5; the RAR format is proprietary and has no free Python decoder) and for 7Z archives using **PPMd, BCJ2, Deflate64** or **Zstandard**. Any one of these is enough; HJT FBX+ finds them automatically:

| Tool | RAR | RAR passwords | Multi-volume RAR | Exotic 7Z | Where to get it |
|---|:---:|:---:|:---:|:---:|---|
| **7-Zip** (`7z`, `7zz`, `7za`; also NanaZip, PeaZip's 7z, Keka) | ✓ ¹ | ✓ | ✓ | ✓ | Windows: [7-zip.org](https://www.7-zip.org). macOS: `brew install sevenzip`. Linux: `7zip` or `p7zip-full` + `p7zip-rar` |
| **UnRAR** / WinRAR (`unrar`, `UnRAR.exe`, `Rar.exe`) | ✓ | ✓ | ✓ | – | Windows: WinRAR. macOS: `brew install unrar`. Linux: `unrar` |
| **The Unarchiver** (`unar` + `lsar`) | ✓ | ✓ | ✓ | ✓ ³ | macOS: `brew install unar`. Linux: `unar` |
| **bsdtar** (libarchive) | ✓ | – | – | – ² | Built into macOS (`/usr/bin/tar`) and Windows 10/11 (`tar.exe`). Linux: `libarchive-tools` |

¹ Some Linux 7-Zip packages are built without the RAR codec. HJT FBX+ checks this and uses another tool if needed.
² bsdtar reads common 7Z files, which HJT FBX+ handles itself anyway.
³ PPMd and BCJ2, not Zstandard.

Search order for RAR: 7-Zip, UnRAR, The Unarchiver, bsdtar. Tools are looked up on the `PATH` and in the usual install locations (Program Files, Homebrew, MacPorts, `/usr/bin`, `/usr/local/bin`, `/snap/bin`). You can also set the paths in *Preferences › Add-ons › HJT FBX+* and press **Detect Tools**. The *Archive Management* panel shows which formats your system can read.

External tools run as background processes without console windows. Their output is staged in a private folder and checked for path traversal before anything reaches the cache.

## Optional Python packages

These can be installed with one click under *Preferences › Add-ons › HJT FBX+ › Optional packages*. They are installed with Blender's own `pip` into HJT FBX+'s user folder (`<user folder>/lib/py3XX/`), never into Blender's installation, and are only loaded by HJT FBX+.

| Package | What it adds |
|---|---|
| **pycryptodomex** | Fast AES: about 400 MB/s instead of about 20 MB/s for password-protected 7z and AES ZIP archives |
| **py7zr** | Reads 7z archives using PPMd, BCJ2 or Zstandard when 7-Zip is not installed |

If `cryptography` or `pycryptodome` is already importable in Blender's Python, it is used too.

Installing needs an internet connection. That is the only time HJT FBX+ uses the network (declared as the `network` permission of the extension).

## Python and Blender requirements

- Blender 3.6 or newer (Python 3.10+).
- The **FBX format** add-on (`io_scene_fbx`) enabled (default). On Blender 4.5+ the built-in C++ importer works even when it is disabled.
- No compiled modules: HJT FBX+ is pure Python and works the same on Windows, macOS and Linux, on x64 and ARM.
