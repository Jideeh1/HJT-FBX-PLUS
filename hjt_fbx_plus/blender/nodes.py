# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#tiny shader node toolkit so materials.py reads like a recipe instead of 400 lines of nodes.new
# everything goes thru compat so 3.6 n 5.2 both build the same tree

from __future__ import annotations

import bpy

from . import compat


class Tree:
    def __init__(self, mat, clear=True):
        self.mat = mat
        self.tree = compat.ensure_node_tree(mat)
        self.nodes = self.tree.nodes
        self.links = self.tree.links
        if clear:
            self.nodes.clear()

    def node(self, type_name, x=0, y=0, label="", **props):
        n = self.nodes.new(type_name)
        n.location = (x, y)
        if label:
            n.label = label
        for k, v in props.items():
            try:
                setattr(n, k, v)
            except (AttributeError, TypeError, ValueError):
                pass
        return n

    def link(self, out_sock, in_sock):
        if out_sock is None or in_sock is None:
            return None
        return self.links.new(out_sock, in_sock)

    def set_input(self, node, key, value):
        s = compat.socket(node, key) if isinstance(key, str) else key
        if s is None:
            return False
        if hasattr(value, "is_output") or hasattr(value, "links"):
            self.link(value, s)
            return True
        try:
            s.default_value = value
        except (TypeError, ValueError, AttributeError):
            try:
                s.default_value = tuple(value)[:len(s.default_value)]
            except Exception:  # noqa: BLE001
                return False
        return True

    #building blocks
    def image(self, img, x, y, label="", uv_socket=None, extension="REPEAT"):
        n = self.node("ShaderNodeTexImage", x, y, label)
        n.image = img
        try:
            n.extension = extension
        except TypeError:
            pass
        if uv_socket is not None:
            self.link(uv_socket, n.inputs["Vector"])
        return n

    def uv_input(self, x, y, uv_name="", scale=(1.0, 1.0), offset=(0.0, 0.0)):
        #uv map node (+ mapping when tiled/offset). returns the vector socket or None for the default uv
        if not uv_name and tuple(scale) == (1.0, 1.0) and tuple(offset) == (0.0, 0.0):
            return None
        uv = self.node("ShaderNodeUVMap", x - 200, y, "UV")
        if uv_name:
            uv.uv_map = uv_name
        out = uv.outputs["UV"]
        if tuple(scale) != (1.0, 1.0) or tuple(offset) != (0.0, 0.0):
            mp = self.node("ShaderNodeMapping", x, y, "Tiling / Offset")
            self.link(out, mp.inputs["Vector"])
            mp.inputs["Location"].default_value = (offset[0], offset[1], 0.0)
            mp.inputs["Scale"].default_value = (scale[0], scale[1], 1.0)
            out = mp.outputs["Vector"]
        return out

    def mix_color(self, blend, a, b, fac=1.0, x=0, y=0, label=""):
        #ShaderNodeMix in RGBA mode. a/b = socket or rgba tuple
        n = self.node("ShaderNodeMix", x, y, label)
        n.data_type = "RGBA"
        n.blend_type = blend
        fs = compat.socket_by_id(n, "Factor_Float")
        sa = compat.socket_by_id(n, "A_Color")
        sb = compat.socket_by_id(n, "B_Color")
        for sock, val in ((fs, fac), (sa, a), (sb, b)):
            if hasattr(val, "is_output"):
                self.link(val, sock)
            elif val is not None:
                try:
                    sock.default_value = val if sock is fs else tuple(val) + (1.0,) * (4 - len(val))
                except (TypeError, ValueError):
                    pass
        return compat.socket_by_id(n, "Result_Color", output=True)

    def math(self, op, a, b=None, x=0, y=0, clamp=False, label=""):
        n = self.node("ShaderNodeMath", x, y, label, operation=op, use_clamp=clamp)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if hasattr(v, "is_output"):
                self.link(v, n.inputs[i])
            else:
                n.inputs[i].default_value = float(v)
        return n.outputs[0]

    def separate(self, color_sock, x, y, label=""):
        n = self.node("ShaderNodeSeparateColor", x, y, label)
        self.link(color_sock, n.inputs[0])
        return n

    def combine(self, r, g, b, x, y):
        n = self.node("ShaderNodeCombineColor", x, y)
        for i, v in enumerate((r, g, b)):
            if hasattr(v, "is_output"):
                self.link(v, n.inputs[i])
            else:
                n.inputs[i].default_value = float(v)
        return n.outputs[0]

    def frame(self, label, nodes, color=None):
        if not nodes:
            return None
        f = self.node("NodeFrame", 0, 0, label)
        f.label_size = 14
        if color is not None:
            f.use_custom_color = True
            f.color = color
        for n in nodes:
            n.parent = f
        return f


_RG_GROUP = "HJT Normal from RG"


def normal_rg_group():
    #rebuilds Z for 2 channel normal maps (bc5 / dxt5nm): z = sqrt(1 - x^2 - y^2)
    g = bpy.data.node_groups.get(_RG_GROUP)
    if g is not None:
        return g
    g = bpy.data.node_groups.new(_RG_GROUP, "ShaderNodeTree")
    compat.new_group_socket(g, "INPUT", "X", "NodeSocketFloat", 0.5)
    compat.new_group_socket(g, "INPUT", "Y", "NodeSocketFloat", 0.5)
    compat.new_group_socket(g, "OUTPUT", "Color", "NodeSocketColor")
    n = g.nodes
    gi = n.new("NodeGroupInput")
    go = n.new("NodeGroupOutput")
    gi.location = (-600, 0)
    go.location = (600, 0)

    def math(op, a, b=None, loc=(0, 0)):
        m = n.new("ShaderNodeMath")
        m.operation = op
        m.location = loc
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if hasattr(v, "is_output"):
                g.links.new(v, m.inputs[i])
            else:
                m.inputs[i].default_value = v
        return m.outputs[0]

    #x,y in 0..1 -> -1..1
    xs = math("MULTIPLY_ADD", gi.outputs[0], 2.0, (-400, 100))
    xs.node.inputs[2].default_value = -1.0
    ys = math("MULTIPLY_ADD", gi.outputs[1], 2.0, (-400, -100))
    ys.node.inputs[2].default_value = -1.0
    x2 = math("MULTIPLY", xs, xs, (-200, 100))
    y2 = math("MULTIPLY", ys, ys, (-200, -100))
    s = math("ADD", x2, y2, (0, 0))
    one_minus = math("SUBTRACT", 1.0, s, (150, 0))
    clamped = math("MAXIMUM", one_minus, 0.0, (300, 0))
    z = math("SQRT", clamped, None, (420, -50))
    zc = math("MULTIPLY_ADD", z, 0.5, (520, -50))
    zc.node.inputs[2].default_value = 0.5
    comb = n.new("ShaderNodeCombineColor")
    comb.location = (450, 150)
    g.links.new(gi.outputs[0], comb.inputs[0])
    g.links.new(gi.outputs[1], comb.inputs[1])
    g.links.new(zc, comb.inputs[2])
    g.links.new(comb.outputs[0], go.inputs[0])
    return g
