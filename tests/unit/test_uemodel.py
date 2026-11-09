# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#writes tiny synthetic .uemodel files in every layout n reads them back. no game assets

import gzip
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    import numpy  # noqa: F401
    from hjt_fbx_plus.uemodel import reader as R
except ImportError:  # pragma: no cover
    R = None

VERTS = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (0.0, 50.0, 0.0), (0.0, 0.0, 200.0)]
TRIS = [0, 1, 2, 0, 2, 3]


def fs(s):
    b = s.encode() + b"\x00"
    return struct.pack("<i", len(b)) + b


def floats(vals):
    return struct.pack("<%df" % len(vals), *vals)


def i32(*v):
    return struct.pack("<%di" % len(v), *v)


def header(version, body, name="SK_Test", compress=None):
    out = b"UEFORMAT" + fs("UEMODEL") + bytes([version]) + fs(name)
    if version >= 10:
        out += fs("/Game/SK_Test")
    if compress == "GZIP":
        packed = gzip.compress(body)
        return out + b"\x01" + fs("GZIP") + i32(len(body), len(packed)) + packed
    return out + b"\x00" + body


def attr(name, payload):
    return fs(name) + i32(len(payload)) + payload


def attrs(*items):
    return i32(len(items)) + b"".join(items)


def section(name, count, payload):
    return fs(name) + i32(count, len(payload)) + payload


def bone(name, parent, pos, rot, scale=None):
    return fs(name) + i32(parent) + floats(pos) + floats(rot) + (floats(scale) if scale else b"")


def v10_file(compress=None):
    flat_v = [c for v in VERTS for c in v]
    lod = fs("LOD0") + attrs(
        attr("VERTICES", i32(4) + floats(flat_v)),
        attr("NORMALS", i32(4) + floats([1, 0, 0, 1] * 4)),
        attr("TEXCOORDS", i32(1) + fs("UV0") + i32(4) + floats([0, 0, 1, 0, 0, 0.25, 1, 1])),
        attr("INDICES", i32(6) + struct.pack("<6I", *TRIS)),
        attr("VERTEXCOLORS", i32(1) + fs("COL0") + i32(4) + bytes(range(16))),
        attr("MATERIALS", i32(2) + fs("MI_A") + fs("/Game/MI_A") + i32(0, 1) + fs("MI_B") + fs("/Game/MI_B") + i32(3, 1)),
        attr("WEIGHTS", i32(2) + struct.pack("<Hif", 1, 3, 0.75) + struct.pack("<Hif", 0, 0, 1.0)),
        attr("MORPHTARGETS", i32(1) + fs("Smile") + i32(1) + floats([0, 0, 10, 0, 0, 1]) + i32(3)),
    )
    skel = attrs(attr("BONES", i32(2) + bone("root", -1, (0, 0, 0), (0, 0, 0, 1), (1, 1, 1))
                      + bone("spine", 0, (0, 20, 100), (0, 0, 0.7071, 0.7071), (1, 1, 1))))
    body = attrs(attr("LODS", i32(1) + lod), attr("SKELETON", skel))
    return header(10, body, compress=compress)


def v9_file():
    flat_v = [c for v in VERTS for c in v]
    inner = (section("VERTICES", 4, floats(flat_v)) + section("INDICES", 6, struct.pack("<6I", *TRIS))
             + section("NORMALS", 4, floats([1, 0, 0, 1] * 4))
             + section("TEXCOORDS", 1, i32(4) + floats([0, 0, 1, 0, 0, 0.25, 1, 1]))
             + section("MATERIALS", 1, fs("MI_A") + fs("/Game/MI_A") + i32(0, 2))
             + section("WEIGHTS", 1, struct.pack("<hif", 1, 2, 0.5)))
    lod = fs("LOD0") + i32(len(inner)) + inner
    skel = section("BONES", 2, bone("root", -1, (0, 0, 0), (0, 0, 0, 1)) + bone("spine", 0, (0, 20, 100), (0, 0, 0, 1)))
    return header(9, section("LODS", 1, lod) + section("SKELETON", 0, skel))


def v3_file():
    flat_v = [c for v in VERTS for c in v]
    body = (section("VERTICES", 4, floats(flat_v)) + section("INDICES", 6, struct.pack("<6I", *TRIS))
            + section("NORMALS", 4, floats([0, 0, 1, 0] * 4))
            + section("VERTEXCOLORS", 1, fs("C") + i32(4) + bytes(16))
            + section("BONES", 1, bone("root", -1, (0, 0, 0), (0, 0, 0, 1))))
    return header(3, body)


@unittest.skipIf(R is None, "numpy not available")
class UEModelReaderTest(unittest.TestCase):
    def check_mesh(self, m, mirrored):
        lod = m.lods[0]
        self.assertEqual(lod.verts.shape, (4, 3))
        self.assertEqual(lod.indices.tolist(), [[0, 1, 2], [0, 2, 3]])
        y = -0.5 if mirrored else 0.5
        self.assertAlmostEqual(float(lod.verts[2][1]), y, places=6)
        self.assertAlmostEqual(float(lod.verts[3][2]), 2.0, places=6)

    def test_v10_attributes(self):
        m = R.read_uemodel(v10_file())
        self.assertEqual((m.version, m.name, m.object_path), (10, "SK_Test", "/Game/SK_Test"))
        self.check_mesh(m, True)
        lod = m.lods[0]
        self.assertEqual([x[0] for x in lod.materials], ["MI_A", "MI_B"])
        self.assertAlmostEqual(float(lod.uvs[0][1][2][1]), 0.75, places=6)  #v flipped
        self.assertEqual(lod.colors[0][1][1].tolist(), [4, 5, 6, 7])
        self.assertEqual(lod.weights["bone"].tolist(), [1, 0])
        self.assertEqual(lod.morphs[0][0], "Smile")
        self.assertAlmostEqual(float(lod.morphs[0][2][0][2]), 0.1, places=6)
        self.assertEqual([b.name for b in m.bones], ["root", "spine"])
        self.assertAlmostEqual(float(m.bones[1].pos[1]), -0.2, places=6)
        self.assertAlmostEqual(float(m.bones[1].rot[3]), -0.7071, places=4)

    def test_v10_gzip(self):
        m = R.read_uemodel(v10_file(compress="GZIP"))
        self.assertEqual(m.compression, "GZIP")
        self.check_mesh(m, True)

    def test_v9_sections(self):
        m = R.read_uemodel(v9_file())
        self.check_mesh(m, True)
        self.assertEqual(m.lods[0].materials[0][:2], ("MI_A", "/Game/MI_A"))
        self.assertEqual(int(m.lods[0].weights["vert"][0]), 2)
        self.assertEqual(len(m.bones), 2)

    def test_v3_flat_not_mirrored(self):
        m = R.read_uemodel(v3_file())
        self.check_mesh(m, False)
        self.assertEqual(m.lods[0].normals.shape, (4, 3))
        self.assertEqual(m.bones[0].name, "root")

    def test_rejects_garbage(self):
        with self.assertRaises(R.UEFormatError):
            R.read_uemodel(b"NOTAFILE" + bytes(32))
        with self.assertRaises(R.UEFormatError):
            R.read_uemodel(v10_file()[:-40])

    def test_summary_shape(self):
        m = R.read_uemodel(v10_file())
        s = R.summarize([("Body", m), ("Hair", m)], "Hero")
        self.assertEqual(s["counts"]["meshes"], 2)
        self.assertEqual(s["counts"]["bones"], 2)
        self.assertEqual([x["uid"] for x in s["materials"]], ["ue:MI_A", "ue:MI_B"])
        self.assertEqual(s["materials"][0]["users"], ["Body", "Hair"])


if __name__ == "__main__":
    unittest.main()
