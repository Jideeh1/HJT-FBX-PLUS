# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#unit tests for everything that doesnt need blender. stdlib unittest only, runs on python 3.10+
#   python -m unittest discover -s tests/unit -v          (from the repo root)
# set HJT_TEST_ASSETS to the generated assets dir to also run the fbx round trips on real exports

import array
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from hjt_fbx_plus.archives import open_archive, detect_format  # noqa: E402
from hjt_fbx_plus.archives.base import PasswordRequired, WrongPassword, safe_relpath, normalize_member_path  # noqa: E402
from hjt_fbx_plus.archives import crypto  # noqa: E402
from hjt_fbx_plus.archives.index import ArchiveIndex  # noqa: E402
from hjt_fbx_plus.core.cache import CacheManager  # noqa: E402
from hjt_fbx_plus.fbx import ascii as fbx_ascii, binary  # noqa: E402
from hjt_fbx_plus.fbx.ascii_writer import write_ascii  # noqa: E402
from hjt_fbx_plus.fbx.elements import FbxArray, FbxNode, name_class_split  # noqa: E402
from hjt_fbx_plus.fbx.document import FbxDocument  # noqa: E402
from hjt_fbx_plus.fbx.inspect import inspect_document  # noqa: E402
from hjt_fbx_plus.fbx import prepare  # noqa: E402
from hjt_fbx_plus.profiles.registry import Registry  # noqa: E402
from hjt_fbx_plus.profiles.schema import validate  # noqa: E402
from hjt_fbx_plus.textures.index import TexEntry, TextureIndex  # noqa: E402
from hjt_fbx_plus.textures.slots import DEFAULT_SLOT_RULES, classify_name, compile_rules  # noqa: E402
from hjt_fbx_plus.textures.unity import UnityMaterial  # noqa: E402

ASSETS = os.environ.get("HJT_TEST_ASSETS", "")


def tiny_fbx():
    #minimal but valid 7.4 scene: one model + geometry + material + connections
    def P(*vals, types):
        return FbxNode(b"P", list(vals), types)
    root = FbxNode(b"")
    root.children = [
        FbxNode(b"FBXHeaderExtension", [], b"", [FbxNode(b"FBXHeaderVersion", [1003], b"I"),
                                                FbxNode(b"FBXVersion", [7400], b"I"),
                                                FbxNode(b"Creator", [b"unit test"], b"S")]),
        FbxNode(b"GlobalSettings", [], b"", [FbxNode(b"Version", [1000], b"I"), FbxNode(b"Properties70", [], b"", [
            P(b"UpAxis", b"int", b"Integer", b"", 1, types=b"SSSSI"),
            P(b"UnitScaleFactor", b"double", b"Number", b"", 100.0, types=b"SSSSD")])]),
        FbxNode(b"Objects", [], b"", [
            FbxNode(b"Geometry", [10, b"Cube\x00\x01Geometry", b"Mesh"], b"LSS", [
                FbxNode(b"Vertices", [FbxArray.from_values("d", [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0])], b"d"),
                FbxNode(b"PolygonVertexIndex", [FbxArray.from_values("i", [0, 1, 2, -4])], b"i")]),
            FbxNode(b"Model", [20, b"Cube\x00\x01Model", b"Mesh"], b"LSS", [FbxNode(b"Version", [232], b"I")]),
            FbxNode(b"Material", [30, b"Mat\x00\x01Material", b""], b"LSS", [FbxNode(b"ShadingModel", [b"phong"], b"S"),
                                                                            FbxNode(b"Properties70", [], b"", [
                                                                                P(b"Opacity", b"double", b"", b"", 1.0, types=b"SSSSD")])]),
        ]),
        FbxNode(b"Connections", [], b"", [FbxNode(b"C", [b"OO", 10, 20], b"SLL"), FbxNode(b"C", [b"OO", 20, 0], b"SLL"),
                                         FbxNode(b"C", [b"OO", 30, 20], b"SLL")]),
        FbxNode(b"Takes", [], b""),
    ]
    buf = io.BytesIO()
    binary.write_binary(buf, root, 7400)
    return buf.getvalue()


def tree_equal(a, b, path=""):
    p = path + "/" + a.name.decode()
    assert a.name == b.name, p
    assert bytes(a.types) == bytes(b.types), (p, a.types, b.types)
    for x, y in zip(a.props, b.props):
        if isinstance(x, FbxArray):
            assert list(x.values()) == list(y.values()), p
        elif isinstance(x, float):
            assert abs(x - y) <= 1e-9 * max(1.0, abs(x)), (p, x, y)
        else:
            xb = x.tobytes() if hasattr(x, "tobytes") else x
            yb = y.tobytes() if hasattr(y, "tobytes") else y
            assert xb == yb, (p, x, y)
    assert len(a.children) == len(b.children), p
    for ca, cb in zip(a.children, b.children):
        tree_equal(ca, cb, p)


class FbxTests(unittest.TestCase):
    def test_binary_roundtrip(self):
        data = tiny_fbx()
        res = binary.read_binary(data=data)
        out = io.BytesIO()
        binary.write_binary(out, res.root, res.version)
        self.assertEqual(out.getvalue(), data)

    def test_lazy_subtree_passthrough(self):
        #curves left unparsed get copied as bytes; offsets after a grown node must still line up
        res = binary.read_binary(data=tiny_fbx())
        objs = next(n for n in res.root.children if n.name == b"Objects")
        for i in range(3):
            objs.children.append(FbxNode(b"AnimationCurve", [100 + i, b"\x00\x01AnimCurve", b""], b"LSS", [
                FbxNode(b"Default", [0.0], b"D"), FbxNode(b"KeyVer", [4009], b"I"),
                FbxNode(b"KeyTime", [FbxArray.from_values("l", [0, 46186158000 * (i + 1)])], b"l"),
                FbxNode(b"KeyValueFloat", [FbxArray.from_values("f", [0.5, 1.5 + i])], b"f")]))
        buf = io.BytesIO()
        binary.write_binary(buf, res.root, 7400)
        src = buf.getvalue()
        outs = []
        for lazy in (None, frozenset((b"AnimationCurve",))):
            r = binary.read_binary(data=src, lazy_names=lazy)
            geo = next(n for n in r.root.children if n.name == b"Objects").children[0]
            geo.add_prop("S", b"grown so every curve after it moves")
            if lazy:
                curves = [n for n in next(n for n in r.root.children if n.name == b"Objects").children if n.name == b"AnimationCurve"]
                self.assertTrue(all(c.raw_children is not None for c in curves))
            out = io.BytesIO()
            binary.write_binary(out, r.root, 7400)
            outs.append(out.getvalue())
        self.assertEqual(outs[0], outs[1])
        tree_equal(binary.read_binary(data=outs[0]).root, binary.read_binary(data=outs[1]).root)
        #different header width cant be copied raw, must fall back to a real rebuild
        r = binary.read_binary(data=src, lazy_names=frozenset((b"AnimationCurve",)))
        out = io.BytesIO()
        binary.write_binary(out, r.root, 7500)
        tree_equal(binary.read_binary(data=src).root, binary.read_binary(data=out.getvalue()).root)

    def test_64bit_headers(self):
        res = binary.read_binary(data=tiny_fbx())
        out = io.BytesIO()
        binary.write_binary(out, res.root, 7500)
        again = binary.read_binary(data=out.getvalue())
        self.assertEqual(again.version, 7500)
        tree_equal(res.root, again.root)

    def test_ascii_roundtrip_and_typing(self):
        res = binary.read_binary(data=tiny_fbx())
        txt = io.StringIO()
        write_ascii(txt, res.root, res.version)
        back = fbx_ascii.read_ascii(data=txt.getvalue().encode("utf-8"))
        tree_equal(res.root, back.root)

    def test_ascii_name_classes(self):
        src = b'; FBX 7.4.0 project file\nFBXHeaderExtension:  {\n\tFBXVersion: 7400\n}\nObjects:  {\n' \
              b'\tAnimationStack: 5, "AnimStack::Take 001", "" {\n\t}\n' \
              b'\tModel: 7, "Model::Char1::Hips", "LimbNode" {\n\t}\n}\nConnections:  {\n\tC: "OO",7,0\n}\n'
        doc = fbx_ascii.read_ascii(data=src)
        objs = doc.root.find(b"Objects").children
        self.assertEqual(objs[0].props[1], b"Take 001\x00\x01AnimStack")
        self.assertEqual(objs[1].props[1], b"Char1::Hips\x00\x01Model")
        self.assertEqual(objs[1].types, b"LSS")
        c = doc.root.find(b"Connections").children[0]
        self.assertEqual((c.types, c.props[1], c.props[2]), (b"SLL", 7, 0))

    def test_ascii_v6_arrays_and_continuations(self):
        src = (b'; FBX 6.1.0 project file\nFBXHeaderExtension:  {\n\tFBXVersion: 6100\n}\nObjects:  {\n'
               b'\tModel: "Model::Box", "Mesh" {\n\t\tVertices: 0,0,0,1,0,0\n\t\t,1,1,0\n'
               b'\t\tPolygonVertexIndex: 0,1,-3\n\t}\n}\n')
        doc = fbx_ascii.read_ascii(data=src)
        model = doc.root.find(b"Objects").children[0]
        self.assertEqual(model.find(b"Vertices").props[0].count, 9)
        self.assertEqual(list(model.find(b"PolygonVertexIndex").props[0].values()), [0, 1, -3])

    def test_document_and_inspect(self):
        doc = FbxDocument.from_bytes(tiny_fbx())
        s = inspect_document(doc)
        self.assertEqual(s["counts"]["meshes"], 1)
        self.assertEqual(s["counts"]["materials"], 1)
        self.assertEqual(s["geometry"], {"vertices": 4, "polygons": 1})
        self.assertEqual(s["meshes"][0]["materials"], ["Mat"])

    def test_prepare_uids_and_takes(self):
        doc = FbxDocument.from_bytes(tiny_fbx())
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "p.fbx")
            r = prepare.prepare_document(doc, out)
            self.assertEqual(r.uid_count, 3)  #model, geometry, material
            self.assertEqual(r.fixes.get("filled_empty_takes"), 1)
            again = FbxDocument.open(out, lazy=False)
            m = again.get(20)
            self.assertEqual(m.prop(b"hjt_fbx_uid"), b"20")
            self.assertEqual(again.root.find(b"Takes").children[0].name, b"Current")
            again.close()

    def test_sanitizer_canonicalizes(self):
        root = binary.read_binary(data=tiny_fbx()).root
        p70 = root.find(b"Objects").children[2].find(b"Properties70")
        p70.children[0].props[2] = b"Bad label"  #Opacity "double" must have label "Number"
        p70.children.append(FbxNode(b"P", [b"Flag", b"bool", b"", b"", 2], b"SSSSI"))
        p70.children.append(FbxNode(b"P", [b"Speed", b"Float", b"", b"U", 1.5], b"SSSSD"))
        doc = FbxDocument(root, 7400, "binary")
        res = prepare.PrepareResult("x")
        prepare._sanitize_tree(doc.root, res, False)
        self.assertEqual(p70.children[0].props[2], b"Number")
        self.assertEqual(p70.children[1].props[4], 1)
        self.assertEqual(p70.children[2].types, b"SSSSF")

    def test_name_class_split(self):
        self.assertEqual(name_class_split(b"Cube\x00\x01Model"), (b"Cube", b"Model"))
        self.assertEqual(name_class_split(b"Model::Cube"), (b"Cube", b"Model"))

    @unittest.skipUnless(ASSETS and os.path.isdir(ASSETS), "HJT_TEST_ASSETS not set")
    def test_real_exports_roundtrip(self):
        for name in ("character/Hero.fbx", "props/Crate.fbx", "embedded/Hero_embedded.fbx"):
            path = os.path.join(ASSETS, name)
            res = binary.read_binary(path)
            out = io.BytesIO()
            binary.write_binary(out, res.root, res.version)
            back = binary.read_binary(data=out.getvalue())
            tree_equal(res.root, back.root)
            txt = io.StringIO()
            write_ascii(txt, res.root, res.version)
            asc = fbx_ascii.read_ascii(data=txt.getvalue().encode("utf-8"))
            tree_equal(res.root, asc.root)
            res.close()


class CryptoTests(unittest.TestCase):
    def test_fips197(self):
        pt = bytes.fromhex("00112233445566778899aabbccddeeff")
        for kind in ("python", "numpy"):
            if kind == "numpy" and crypto._np is None:
                continue
            e = crypto.AesEcb(bytes(range(16)), kind)
            self.assertEqual(e.encrypt(pt).hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")
            e = crypto.AesEcb(bytes(range(32)), kind)
            c = e.encrypt(pt)
            self.assertEqual(c.hex(), "8ea2b7ca516745bfeafc49904b496089")
            self.assertEqual(e.decrypt(c), pt)

    def test_cbc_stream_matches_python(self):
        key, iv = os.urandom(32), os.urandom(16)
        data = os.urandom(4096)
        #encrypt w the pure block cipher in cbc by hand
        e = crypto.AesEcb(key, "python")
        prev = iv
        ct = b""
        for i in range(0, len(data), 16):
            blk = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
            prev = e.encrypt(blk)
            ct += prev
        for kind in ("python", "numpy"):
            if kind == "numpy" and crypto._np is None:
                continue
            d = crypto.CbcDecryptor(key, iv, kind)
            out = b"".join(d.decrypt(ct[i:i + 999]) for i in range(0, len(ct), 999))
            self.assertEqual(out, data)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(os.path.join(self.src, "Model", "Textures"))
        with open(os.path.join(self.src, "Model", "char.fbx"), "wb") as fh:
            fh.write(tiny_fbx())
        with open(os.path.join(self.src, "Model", "Textures", "body_d.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + os.urandom(5000))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _check_reader(self, path, password=None):
        r = open_archive(path, password=password)
        files = {e.path: e for e in r.files()}
        self.assertIn("Model/char.fbx", files)
        out = r.extract(list(files.values()), os.path.join(self.tmp, "out_" + os.path.basename(path)))
        with open(out["Model/char.fbx"], "rb") as fh:
            self.assertEqual(fh.read(), tiny_fbx())
        idx = ArchiveIndex(r.entries)
        self.assertEqual(len(idx.fbx), 1)
        r.close()

    def test_zip(self):
        z = os.path.join(self.tmp, "a.zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _d, files in os.walk(self.src):
                for f in files:
                    full = os.path.join(root, f)
                    zf.write(full, os.path.relpath(full, self.src))
        self.assertEqual(detect_format(z), "zip")
        self._check_reader(z)

    def _seven(self):
        import shutil as sh
        return sh.which("7zz") or sh.which("7z") or sh.which("7za")

    def test_7z_native(self):
        seven = self._seven()
        if not seven:
            self.skipTest("7-Zip not installed")
        for name, extra in (("lzma2.7z", ["-m0=lzma2"]), ("bcj.7z", ["-mf=BCJ", "-m0=lzma"]), ("enc.7z", ["-mhe=on", "-ppw"])):
            p = os.path.join(self.tmp, name)
            subprocess.run([seven, "a", "-bso0", "-bsp0"] + extra + [p, "."], cwd=self.src, check=True)
            if name == "enc.7z":
                with self.assertRaises(PasswordRequired):
                    open_archive(p)
                with self.assertRaises(WrongPassword):
                    open_archive(p, password="nope")
                self._check_reader(p, "pw")
            else:
                self._check_reader(p)

    def test_7z_solid_resume_any_order(self):
        #parked decoders must never hand out the wrong bytes, whatever order stuff gets asked for in
        seven = self._seven()
        if not seven:
            self.skipTest("7-Zip not installed")
        d = os.path.join(self.tmp, "solid_src")
        os.makedirs(d)
        blobs = {}
        for i in range(24):
            name = "f%02d.bin" % i
            blobs[name] = os.urandom(3000 + i * 997) if i % 3 else (b"abc%d" % i) * 4000
            with open(os.path.join(d, name), "wb") as fh:
                fh.write(blobs[name])
        for name, extra in (("solid.7z", ["-ms=on"]), ("solid_enc.7z", ["-ms=on", "-ppw"])):
            p = os.path.join(self.tmp, name)
            subprocess.run([seven, "a", "-bso0", "-bsp0"] + extra + [p, "."], cwd=d, check=True)
            r = open_archive(p, password="pw" if "enc" in name else None)
            self.assertTrue(r.solid)
            files = {e.path: e for e in r.files()}
            orders = (["f03.bin"], ["f07.bin", "f09.bin"], ["f08.bin"], ["f01.bin"], ["f20.bin"],
                      ["f02.bin", "f21.bin"], ["f22.bin"], ["f23.bin"], ["f00.bin"], ["f23.bin"])
            for n, want in enumerate(orders):
                out = r.extract([files[w] for w in want], os.path.join(self.tmp, "o_%s_%d" % (name, n)))
                for w in want:
                    with open(out[w], "rb") as fh:
                        self.assertEqual(fh.read(), blobs[w], "%s %s round %d" % (name, w, n))
            self.assertLessEqual(len(r._cursors), 2)
            r.close()

    def test_rar_if_tools(self):
        import shutil as sh
        rar = sh.which("rar")
        if not rar:
            self.skipTest("rar not installed")
        p = os.path.join(self.tmp, "a.rar")
        subprocess.run([rar, "a", "-idq", "-r", p, "."], cwd=self.src, check=True)
        try:
            self._check_reader(p)
        except Exception as exc:  # noqa: BLE001
            from hjt_fbx_plus.archives.base import BackendUnavailable
            if isinstance(exc, BackendUnavailable):
                self.skipTest(str(exc))
            raise

    def test_path_safety(self):
        self.assertEqual(safe_relpath("../../etc/passwd"), "etc/passwd")
        self.assertEqual(safe_relpath("C:\\Windows\\evil.dll"), "Windows/evil.dll")
        self.assertEqual(safe_relpath("a/CON/b<>.png"), "a/_CON/b__.png")
        self.assertEqual(normalize_member_path("./a//b/../c.png"), "a/c.png")

    def test_cache_reuse_and_release(self):
        z = os.path.join(self.tmp, "c.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.write(os.path.join(self.src, "Model", "char.fbx"), "Model/char.fbx")
        cache = CacheManager(root=os.path.join(self.tmp, "cache"))
        r = open_archive(z)
        got1 = cache.materialize(r, r.files())
        got2 = cache.materialize(r, r.files())
        self.assertEqual(got1, got2)
        self.assertEqual(cache.release_files(list(got1.values())), 1)
        cache.cleanup_session()
        self.assertFalse(os.path.exists(cache.session))
        r.close()


class TextureTests(unittest.TestCase):
    def index(self, paths):
        idx = TextureIndex("archive")
        for p in paths:
            idx.add(TexEntry(p, 100, hash(p) & 0xFFFFFFFF, archive_entry=object()))
        return idx

    def test_duplicate_names_resolved_by_location(self):
        idx = self.index(["CharA/Textures/Body_D.png", "CharB/Textures/Body_D.png"])
        best, alts, amb = idx.resolve_reference("C:\\work\\CharB\\Textures\\Body_D.png", "CharB")
        self.assertEqual(best.path, "CharB/Textures/Body_D.png")
        self.assertFalse(amb)
        best, alts, amb = idx.resolve_reference("Body_D.png", "CharA")
        self.assertEqual(best.path, "CharA/Textures/Body_D.png")

    def test_ambiguity_flagged(self):
        idx = self.index(["X/Body_D.png", "Y/Body_D.png"])
        best, alts, amb = idx.resolve_reference("Body_D.png", "")
        self.assertTrue(amb)

    def test_other_extension(self):
        idx = self.index(["M/tex/Hair.png"])
        best, alts, amb = idx.resolve_reference("D:/rip/Hair.tga", "M")
        self.assertEqual(best.path, "M/tex/Hair.png")

    def test_slot_rules(self):
        rules = compile_rules(DEFAULT_SLOT_RULES)
        for stem, slot in (("Crate_BaseColor", "base_color"), ("crate_normal", "normal"), ("Rock_ORM", "orm"),
                           ("wall_roughness", "roughness"), ("glass_opacity", "opacity"), ("char_d", "base_color")):
            self.assertEqual(classify_name(stem, rules)[0], slot, stem)

    def test_unity_json_layouts(self):
        list_layout = {"m_Name": "M", "m_Shader": {"Name": "Standard"}, "m_SavedProperties": {
            "m_TexEnvs": [{"first": "_MainTex", "second": {"m_Texture": {"Name": "a"}, "m_Scale": {"x": 2, "y": 2}}}],
            "m_Floats": [{"Key": "_Mode", "Value": 1}, {"Key": "_Cutoff", "Value": 0.3}, {"Key": "_Cull", "Value": 0}],
            "m_Colors": [{"Key": "_Color", "Value": {"r": 1, "g": 0.5, "b": 0.5, "a": 1}}]}}
        um = UnityMaterial.from_json(list_layout)
        self.assertEqual(um.textures["_MainTex"]["name"], "a")
        self.assertEqual(um.textures["_MainTex"]["scale"], (2.0, 2.0))
        p = um.params()
        self.assertEqual((p["blend"], p["alpha_threshold"], p["cull"]), ("CLIP", 0.3, "NONE"))


class ProfileTests(unittest.TestCase):
    def test_builtins_valid(self):
        reg = Registry()
        self.assertEqual(reg.errors, [])
        for need in ("generic", "genshin_impact", "honkai_star_rail", "zenless_zone_zero", "honkai_impact_3rd", "afk_journey"):
            self.assertIn(need, reg.profiles)
            self.assertEqual(validate(reg.profiles[need].raw), [], need)

    def test_user_profile_lifecycle(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as e:
            reg = Registry(d)
            pid = reg.duplicate("genshin_impact")
            self.assertTrue(os.path.exists(os.path.join(d, pid + ".json")))
            exp = os.path.join(e, "exp.json")  #outside the profile folder, else reload picks it up again
            reg.export(pid, exp)
            reg.delete(pid)
            self.assertNotIn(pid, reg.profiles)
            pid2 = reg.import_file(exp)
            self.assertIn(pid2, reg.profiles)
            bad = {"id": "x y", "name": ""}
            self.assertTrue(validate(bad))

    def test_detection(self):
        from hjt_fbx_plus.profiles.detect import detect
        reg = Registry()
        summary = {"material_names": ["MAT_Anby_Body_UI", "MAT_Anby_Face_UI"], "texture_files": [], "object_names": [],
                   "bone_names": [], "header": {}}
        self.assertEqual(detect(reg, summary)[0], "zenless_zone_zero")
        summary = {"material_names": ["pbsc_char_hero_lch_show"], "texture_files": [], "object_names": [],
                   "bone_names": [], "header": {"creator": "Trident FBX Writer"}}
        self.assertEqual(detect(reg, summary, ["pbsc_char_hero_lch.png"])[0], "afk_journey")
        summary = {"material_names": ["lambert1"], "texture_files": ["wood.png"], "object_names": ["Box"],
                   "bone_names": [], "header": {}}
        self.assertEqual(detect(reg, summary)[0], "generic")


if __name__ == "__main__":
    unittest.main(verbosity=2)
