# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from hjt_fbx_plus import objfmt  # noqa: E402

CUBE = "v -1 {a} -1\nv 1 {a} -1\nv 1 {b} 1\nv -1 {b} 1\nvt 0 0\nf 1 2 3 4\n"


class ObjTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_up_axis(self):
        z_up = self.write("z.obj", "v 0.1 -0.5 0\nv -0.1 0.5 0.8\nv 0 0 0.4\nf 1 2 3\n")
        y_up = self.write("y.obj", "v 0.1 0 -0.5\nv -0.1 1.7 0.5\nv 0 0.4 0\nf 1 2 3\n")
        floating = self.write("f.obj", "v 0 -1 -1\nv 1 1 1\nf 1 2 1\n")
        self.assertEqual(objfmt.scan(z_up)["up"], "Z")
        self.assertEqual(objfmt.scan(y_up)["up"], "Y")
        self.assertEqual(objfmt.scan(floating)["up"], "Y")  #no clue -> the obj standard

    def test_names_materials_and_mtl(self):
        p = self.write("Hero_LOD0.obj", "mtllib Hero.mtl\no Body\n" + CUBE.format(a=0, b=1) + "usemtl Skin\ng Hair\nusemtl Hair\nf 1 2 3\n")
        info = objfmt.scan(p)
        self.assertEqual(info["objects"], ["Body", "Hair"])
        self.assertEqual(info["materials"], ["Skin", "Hair"])
        self.assertEqual(info["mtllibs"], ["Hero.mtl"])
        self.assertEqual(objfmt.model_name(p), "Hero")
        mtl = objfmt.read_mtl(b"newmtl Skin\nmap_Kd -s 1 1 1 -o 0 0 0 tex\\skin d.png\nbump -bm 0.5 tex/skin_n.png\n"
                              b"newmtl Hair\nmap_d hair_a.png\n")
        self.assertEqual([(t["slot_hint"], t["relative"]) for t in mtl["Skin"]],
                         [("base_color", "tex/skin d.png"), ("normal", "tex/skin_n.png")])
        self.assertEqual(mtl["Hair"][0]["slot_hint"], "opacity")
        s = objfmt.summarize(p, info, mtl)
        self.assertEqual([m["uid"] for m in s["materials"]], ["obj:Skin", "obj:Hair"])
        self.assertEqual(len(s["materials"][0]["textures"]), 2)

    def test_no_materials_gets_one_named_after_the_model(self):
        p = self.write("NPC_Dog_Size01_Body_001_LOD0.obj", CUBE.format(a=0, b=1))
        s = objfmt.summarize(p, objfmt.scan(p), {})
        self.assertEqual(s["material_names"], ["NPC_Dog_Size01_Body_001"])
        self.assertTrue(any(i["code"] == "obj_no_materials" for i in s["issues"]))


if __name__ == "__main__":
    unittest.main()
