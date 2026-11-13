# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

#bone chain alignment. game rips (unity gacha stuff, ue rips from fmodel/umodel, all of em) store joints
# w whatever local axes the engine felt like that day, so blender points every bone sideways
#n chains look like a box of exploded toothpicks
#
#what this does
#  1. every bone points its tail at the child that continues its chain. picking that child:
#     name chain > only child > dominant branch > child sitting on the bones own axis >
#     the only kid that has kids > straight ahead of the parent (only if the parent continued into us)
#  2. leaves + forks (hands, heads, finger tips) use their OWN axis, the same local axis the parent
#     chain ran along. NOT the parents world direction. 1.1.x did that n thigh couldnt pick between
#     calf n the twist bones, so it copied the pelvis n pointed up. half the rig went vertical, grabe
#  3. roll stays as close to the old one as possible
#  4. animation + pose keys get re-expressed in the new bone axes so NOTHING moves. rest mesh is
#     identical, posed mesh is identical, its only the bone axes that change
#
# math for 4 (took me way too long ngl): rest R_old -> R_new, C = R_old^-1 @ R_new (pure rotation,
#heads dont move). the pose basis that keeps every deform matrix the same is  B_new = C^-1 @ B @ C
#and the parents C cancels out on its own so its per bone, no hierarchy walking. nice

from __future__ import annotations

import math
import re

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

_NUM_TAIL = re.compile(r"^(.*?)[_\-. ]*(\d+)$")
_BONE_PATH = re.compile(r'^pose\.bones\["((?:[^"\\]|\\.)*)"\]\.(location|rotation_quaternion|rotation_euler|rotation_axis_angle|scale)$')

DOMINANT_SHARE = 0.6   #branch w 60%+ of the descendants wins a fork (pelvis -> spine, upperarm -> forearm)
AHEAD_DOT = 0.7        #~45 deg cone in front of the parent
FAR_SHARE = 0.45      #child further than ~half the rig away isnt a chain, its a prop bone
AHEAD_SHARE = 0.25     #n it has to be a real branch, not 1 lonely ear bone
ON_AXIS = 0.985        #~10 deg. engines park the next joint right on the bone axis (ue X, max/bip X)
SNAP_AXIS = 0.95


def _stem(name):
    m = _NUM_TAIL.match(name)
    if m:
        return m.group(1).lower().rstrip("_-. "), int(m.group(2))
    return name.lower().rstrip("_-. "), None


def _named_next(parent, kids):
    #Hair_01 -> Hair_02, "Bip001 Spine" -> "Bip001 Spine1". lowest index thats higher wins
    ps, pn = _stem(parent.name)
    hits = []
    for c in kids:
        cs, cn = _stem(c.name)
        if cs != ps or cn is None:
            continue
        if pn is None or cn > pn:
            hits.append((cn, c.name, c))
    return min(hits)[2] if hits else None


def _axes(m3):
    return (m3.col[0].normalized(), m3.col[1].normalized(), m3.col[2].normalized())


def _pick(eb, kids, axes, desc, cont_dir, far):
    #-> chosen child, or None = nothing continues this bone (fork / leaf)
    #cont_dir is the parents direction ONLY when the parent continued into this bone. a branch
    # (thigh off the pelvis) has nothing to do w where the pelvis points
    usable = [c for c in kids if 1e-5 < (c.head - eb.head).length <= far]
    if not usable:
        return None
    named = _named_next(eb, usable)
    if named is not None:
        return named

    def d(c):
        return (c.head - eb.head).normalized()

    def w(c):
        return desc[c.name] + 1

    def backwards(c):
        return cont_dir is not None and d(c).dot(cont_dir) < -0.7

    if len(usable) == 1:
        return None if backwards(usable[0]) else usable[0]
    total = sum(w(c) for c in usable)
    big = max(w(c) for c in usable)
    top = max(usable, key=lambda c: (w(c), (c.head - eb.head).length, c.name))
    if w(top) >= DOMINANT_SHARE * total:
        return top
    #calf, twist, pads n correctives all fighting over the thigh. the real joint sits on the bones
    # own axis n carries the biggest subtree. twist loses on size, pads r off axis
    on = [c for c in usable if w(c) > 1 and w(c) * 2 >= big and max(abs(a.dot(d(c))) for a in axes) >= ON_AXIS]
    if on:
        return max(on, key=lambda c: (w(c), (c.head - eb.head).length, c.name))
    branches = [c for c in usable if w(c) > 1]
    if len(branches) == 1 and not backwards(branches[0]):
        return branches[0]  #foot: ball has the toe, the fwd/bck helpers r loose leaves
    if cont_dir is not None:
        #biggest branch roughly straight ahead (spine2 -> neck). an ear on a head doesnt count
        ahead = [c for c in usable if d(c).dot(cont_dir) >= AHEAD_DOT and w(c) >= AHEAD_SHARE * total]
        if ahead:
            return max(ahead, key=lambda c: (w(c), c.name))
    return None


def _snap(v):
    i = max(range(3), key=lambda k: abs(v[k]))
    if abs(v[i]) < SNAP_AXIS:
        return v.normalized()
    out = Vector((0.0, 0.0, 0.0))
    out[i] = 1.0 if v[i] > 0 else -1.0
    return out


def _tree(bones):
    #-> (kids by parent name, descendant count). blenders .children n .children_recursive rescan EVERY
    # bone per call, so 511 bones = 261k parent lookups n ~2 s gone. one pass here, thats it
    kids = {}
    for b in bones:
        kids.setdefault(b.parent.name if b.parent is not None else None, []).append(b)
    order = list(kids.get(None, ()))
    i = 0
    while i < len(order):
        order.extend(kids.get(order[i].name, ()))
        i += 1
    desc = {}
    for b in reversed(order):
        desc[b.name] = sum(desc[c.name] + 1 for c in kids.get(b.name, ()))
    return kids, desc


def _owned_points(arm, meshes, owned=0.3):
    #bone name -> Nx3 array of verts (armature space) that bone really owns. used to size leaves
    out = {}
    if np is None:
        return out
    names = {b.name for b in arm.data.bones}
    to_arm = np.array(arm.matrix_world.inverted())
    for ob in meshes:
        if ob.type != "MESH" or not any(m.type == "ARMATURE" and m.object == arm for m in ob.modifiers):
            continue
        groups = {g.index: g.name for g in ob.vertex_groups if g.name in names}
        if not groups:
            continue
        me = ob.data
        co = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        m = to_arm @ np.array(ob.matrix_world)
        co = co @ m[:3, :3].T + m[:3, 3]
        buckets = {}
        for v in me.vertices:
            for g in v.groups:
                if g.weight >= owned:
                    name = groups.get(g.group)
                    if name is not None:
                        buckets.setdefault(name, []).append(v.index)
        for name, idx in buckets.items():
            pts = co[idx]
            out[name] = pts if name not in out else np.vstack((out[name], pts))
    return out


def _reach(points, head, axis):
    if points is None or not len(points):
        return 0.0
    along = (points - np.array(head)) @ np.array(axis)
    return float(along.max()) if along.size else 0.0


def align_chains(arm, meshes=(), compensate=True):
    #-> dict w counts. arm must be an armature object linked to the current view layer
    if arm is None or arm.type != "ARMATURE":
        return {"aligned": 0, "straight": 0, "keys": 0}
    pts = _owned_points(arm, meshes)
    vl = bpy.context.view_layer
    prev_active = vl.objects.active
    prev_mode = arm.mode
    hidden = arm.hide_get()
    if hidden:
        arm.hide_set(False)
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    vl.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    corr = {}
    aligned = straight = 0
    try:
        ebs = arm.data.edit_bones
        old = {eb.name: eb.matrix.copy() for eb in ebs}
        was_connected = {eb.name for eb in ebs if eb.use_connect}
        for eb in ebs:
            eb.use_connect = False  #connected kids would ride along when we move the tail, nope
        kids, desc = _tree(ebs)
        #rough rig size so a root bone cant be 2 meters long lol. 5-95 percentile so one katana bone
        # parked 4m away doesnt make the whole rig "huge"
        heads = [eb.head for eb in ebs]
        ext = []
        for i in range(3):
            vals = sorted(h[i] for h in heads)
            a = vals[int(len(vals) * 0.05)]
            b = vals[min(len(vals) - 1, int(len(vals) * 0.95))]
            ext.append(b - a)
        size = max(Vector(ext).length, 1e-2)
        new_dir = {}
        new_len = {}
        local = {}
        chosen = {}
        #weapon / prop / fx bones parked across the room. pointing at them = 4m long toothpick
        far = size * FAR_SHARE

        def visit(eb):
            nonlocal aligned, straight
            par = eb.parent
            r_old = old[eb.name].to_3x3()
            cont = new_dir.get(par.name) if par is not None and chosen.get(par.name) == eb.name else None
            old_z = eb.z_axis.copy()
            child = _pick(eb, kids.get(eb.name, []), _axes(r_old), desc, cont, far)
            if child is not None:
                off = child.head - eb.head
                d = off.normalized()
                length = off.length
                chosen[eb.name] = child.name
                aligned += 1
            else:
                #the parents chain ran along some local axis (ue: X). same axis here = hand goes
                # towards the fingers, finger tip keeps its curl, hair tip keeps its swoop
                lp = local.get(par.name) if par is not None else None
                d = (r_old @ lp).normalized() if lp is not None else (eb.tail - eb.head).normalized()
                ref = new_len.get(par.name, eb.length) if par is not None else eb.length
                r = _reach(pts.get(eb.name), eb.head, d)
                length = r if r > 0 else (ref * 0.5 if par is not None else eb.length)
                length = min(max(length, ref * 0.2), ref * 3.0, size * (0.2 if par is not None else 0.08))
                straight += 1
            length = max(length, 1e-3)
            lv = r_old.inverted_safe() @ d
            local[eb.name] = _snap(lv) if child is not None else lv.normalized()
            eb.tail = eb.head + d * length
            eb.align_roll(old_z)
            new_dir[eb.name] = d
            new_len[eb.name] = length
            for c in kids.get(eb.name, ()):
                visit(c)

        for root in [eb for eb in ebs if eb.parent is None]:
            visit(root)
        for name in was_connected:
            eb = ebs.get(name)
            if eb is not None and eb.parent is not None and (eb.head - eb.parent.tail).length < 1e-6:
                eb.use_connect = True
        for eb in ebs:
            c = old[eb.name].to_3x3().inverted_safe() @ eb.matrix.to_3x3()
            if max(abs(x) for row in (c - Matrix.Identity(3)) for x in row) > 1e-6:
                corr[eb.name] = c.to_4x4()
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        if hidden:
            arm.hide_set(True)
        vl.objects.active = prev_active
        if prev_mode not in ("OBJECT", "EDIT") and prev_active == arm:
            try:
                bpy.ops.object.mode_set(mode=prev_mode)
            except RuntimeError:
                pass
    arm.data["hjt_chains_aligned"] = int(arm.data.get("hjt_chains_aligned", 0)) + 1
    keys = 0
    if compensate and corr:
        keys = _compensate(arm, corr)
    return {"aligned": aligned, "straight": straight, "keys": keys}


_HEALTH = {}


def chain_health(arm):
    #-> (bones pointing at their chain child, bones that have one). same picker as align_chains, so
    # forks n leaves dont count at all. cheap enough for a panel, cached anyway
    data = arm.data
    key = (data.name, len(data.bones), data.get("hjt_chains_aligned", 0))
    hit = _HEALTH.get(key)
    if hit is not None:
        return hit
    kids, desc = _tree(data.bones)
    heads = [b.head_local for b in data.bones]
    ext = []
    for i in range(3):
        vals = sorted(h[i] for h in heads) or [0.0]
        ext.append(vals[min(len(vals) - 1, int(len(vals) * 0.95))] - vals[int(len(vals) * 0.05)])
    far = max(Vector(ext).length, 1e-2) * FAR_SHARE
    good = total = 0
    stack = [(b, None) for b in data.bones if b.parent is None]
    while stack:
        b, cont = stack.pop()
        y = b.tail_local - b.head_local
        child = _pick(_RestBone(b), [_RestBone(c) for c in kids.get(b.name, ())], _axes(b.matrix_local.to_3x3()), desc, cont, far)
        if child is not None:
            total += 1
            if y.length > 1e-9 and y.normalized().dot((child.head - b.head_local).normalized()) > 0.995:
                good += 1
        for c in kids.get(b.name, ()):
            nxt = None
            if child is not None and c.name == child.name:
                nxt = (c.head_local - b.head_local).normalized()
            stack.append((c, nxt))
    if len(_HEALTH) > 64:
        _HEALTH.clear()
    _HEALTH[key] = (good, total)
    return good, total


class _RestBone:
    #lets _pick read a rest Bone like an EditBone
    __slots__ = ("name", "head", "parent")

    def __init__(self, b):
        self.name = b.name
        self.head = b.head_local
        self.parent = b.parent


#animation compensation. this is the part that made me wanna throw my pc out the window

def _actions_for(arm):
    acts = []
    ad = arm.animation_data
    if ad is not None:
        if ad.action is not None:
            acts.append(ad.action)
        for tr in ad.nla_tracks:
            for st in tr.strips:
                if st.action is not None:
                    acts.append(st.action)
    prefix = arm.name + "|"
    for a in bpy.data.actions:
        if a.name.startswith(prefix):
            acts.append(a)
    seen = set()
    out = []
    for a in acts:
        if a.name not in seen:
            seen.add(a.name)
            out.append(a)
    return out


def _bags(action):
    #-> list of (fcurve container, fcurves). legacy action.fcurves or 4.4+/5.x channelbags
    out = []
    if "fcurves" in action.bl_rna.properties:
        try:
            out.append((action, action.fcurves))
            return out
        except Exception:  # noqa: BLE001
            pass
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for bag in getattr(strip, "channelbags", ()):
                out.append((bag, bag.fcurves))
    return out


def _new_fc(container, path, index, group):
    try:
        return container.fcurves.new(path, index=index, action_group=group)
    except TypeError:
        return container.fcurves.new(path, index=index, group_name=group)


_DEFAULTS = {"location": (0.0, 0.0, 0.0), "rotation_quaternion": (1.0, 0.0, 0.0, 0.0),
             "rotation_euler": (0.0, 0.0, 0.0), "rotation_axis_angle": (0.0, 0.0, 1.0, 0.0), "scale": (1.0, 1.0, 1.0)}


def _conj_basis(c, ci, loc, rot, scale):
    m = Matrix.LocRotScale(Vector(loc), rot, Vector(scale))
    return ci @ m @ c


def _compensate(arm, corr):
    total = 0
    pose = arm.pose
    for action in _actions_for(arm):
        for container, fcs in _bags(action):
            by_bone = {}
            for fc in fcs:
                m = _BONE_PATH.match(fc.data_path)
                if m is None:
                    continue
                name = m.group(1).replace('\\"', '"')
                if name in corr:
                    by_bone.setdefault(name, {})[(m.group(2), fc.array_index)] = fc
            for name, chans in by_bone.items():
                total += _fix_bone_keys(pose.bones.get(name), corr[name], container, chans, name)
    #static pose (no keys) too, so the current frame doesnt jump
    for name, c in corr.items():
        pb = pose.bones.get(name)
        if pb is None:
            continue
        mb = pb.matrix_basis
        if max(abs(x) for row in (mb - Matrix.Identity(4)) for x in row) < 1e-7:
            continue  #rest pose stays rest pose, dont sprinkle float dust on it
        pb.matrix_basis = c.inverted_safe() @ mb @ c
    return total


def _qmul(a, b):
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack((aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw), axis=-1)


def _key_arrays(fc):
    n = len(fc.keyframe_points)
    co = np.empty(n * 2, dtype=np.float64)
    fc.keyframe_points.foreach_get("co", co)
    return co[0::2], co[1::2]


def _fix_bone_keys(pb, c, container, chans, name):
    #numpy for the usual case (quaternion keys, uniform scale): all frames of a bone in one go instead
    # of a python matrix dance per frame. 40 clips x 256 bones went from ~2.5 s to a blink.
    # euler / axis angle / squashy non uniform scale -> the exact per frame path below
    if pb is None:
        return 0
    if np is None or not any(k[0] == "rotation_quaternion" for k in chans) or             any(k[0] in ("rotation_euler", "rotation_axis_angle") for k in chans):
        return _fix_bone_keys_slow(pb, c, container, chans, name)
    keys = {k: _key_arrays(fc) for k, fc in chans.items()}
    keyed = np.unique(np.round(np.concatenate([t for t, _v in keys.values()]), 4))
    if not len(keyed):
        return 0
    lo, hi = int(math.ceil(keyed[0])), int(math.floor(keyed[-1]))
    frames = np.union1d(keyed, np.arange(lo, hi + 1, dtype=np.float64)) if hi >= lo else keyed
    nf = len(frames)
    base = {"location": tuple(pb.location), "scale": tuple(pb.scale), "rotation_quaternion": _DEFAULTS["rotation_quaternion"]}

    def values(path, n):
        out = np.empty((nf, n), dtype=np.float64)
        for i in range(n):
            k = keys.get((path, i))
            if k is None:
                out[:, i] = base[path][i]
            elif len(k[0]) == nf and np.array_equal(np.round(k[0], 4), frames):
                out[:, i] = k[1]
            else:
                fc = chans[(path, i)]
                out[:, i] = [fc.evaluate(f) for f in frames]
        return out

    loc = values("location", 3)
    rot = values("rotation_quaternion", 4)
    scale = values("scale", 3)
    if np.any(scale.max(axis=1) - scale.min(axis=1) > 1e-5):
        return _fix_bone_keys_slow(pb, c, container, chans, name)
    rot /= np.maximum(np.linalg.norm(rot, axis=1), 1e-12)[:, None]
    c3 = c.to_3x3()
    qc = np.array(tuple(c3.to_quaternion()))
    qci = qc * np.array((1.0, -1.0, -1.0, -1.0))
    new_rot = _qmul(_qmul(qci[None, :], rot), qc[None, :])
    flip = np.concatenate(([1.0], np.where(np.einsum("ij,ij->i", new_rot[1:], new_rot[:-1]) < 0, -1.0, 1.0)))
    new_rot *= np.cumprod(flip)[:, None]
    new_loc = loc @ np.array(c3.inverted_safe()).T
    out = {"location": new_loc, "rotation_quaternion": new_rot, "scale": scale}
    paths = ["location", "rotation_quaternion"] + (["scale"] if any(k[0] == "scale" for k in chans) else [])
    group = name
    any_fc = next(iter(chans.values()))
    if any_fc.group is not None:
        group = any_fc.group.name
    prefs = bpy.context.preferences.edit
    written = 0
    flat = np.empty(nf * 2, dtype=np.float64)
    flat[0::2] = frames
    for path in paths:
        for i in range(out[path].shape[1]):
            vals = out[path][:, i]
            fc = chans.get((path, i))
            if fc is None:
                if np.all(np.abs(vals - _DEFAULTS[path][i]) < 1e-7):
                    continue
                fc = _new_fc(container, 'pose.bones["%s"].%s' % (name.replace('"', '\\"'), path), i, group)
            interp = fc.keyframe_points[0].interpolation if len(fc.keyframe_points) else "LINEAR"
            kp = fc.keyframe_points
            kp.clear()
            kp.add(nf)
            flat[1::2] = vals
            kp.foreach_set("co", flat)
            if interp != prefs.keyframe_new_interpolation_type or prefs.keyframe_new_handle_type != "AUTO_CLAMPED":
                for k in kp:
                    k.interpolation = interp
                    k.handle_left_type = k.handle_right_type = "AUTO_CLAMPED"
            written += nf
            fc.update()
    return written


def _fix_bone_keys_slow(pb, c, container, chans, name):
    ci = c.inverted_safe()
    keyed = {round(k.co[0], 4) for fc in chans.values() for k in fc.keyframe_points}
    if not keyed:
        return 0
    #bake every whole frame in between too. conjugating the keys alone is exact ON the keys but
    # bezier/euler in-betweens drift (tested it, up to 1cm on a hand lmao). game rips are baked
    #per frame anyway so usually this adds nothing
    lo, hi = int(math.ceil(min(keyed))), int(math.floor(max(keyed)))
    frames = sorted(keyed | {float(f) for f in range(lo, hi + 1)})
    rot_path = None
    for p in ("rotation_quaternion", "rotation_euler", "rotation_axis_angle"):
        if any(k[0] == p for k in chans):
            rot_path = p
            break
    if rot_path is None:
        rot_path = {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}.get(pb.rotation_mode, "rotation_euler")
    euler_mode = pb.rotation_mode if pb.rotation_mode not in ("QUATERNION", "AXIS_ANGLE") else "XYZ"
    group = name
    any_fc = next(iter(chans.values()))
    if any_fc.group is not None:
        group = any_fc.group.name

    def ev(path, n):
        base = _DEFAULTS[path]
        if path == "location":
            base = tuple(pb.location)
        elif path == "scale":
            base = tuple(pb.scale)
        return [chans[(path, i)].evaluate(f) if (path, i) in chans else base[i] for i in range(n)]

    out = {}
    prev_q = None
    prev_e = None
    for f in frames:
        loc = ev("location", 3)
        scale = ev("scale", 3)
        if rot_path == "rotation_quaternion":
            rot = Quaternion(ev(rot_path, 4)).normalized()
        elif rot_path == "rotation_axis_angle":
            w, x, y, z = ev(rot_path, 4)
            rot = Quaternion(Vector((x, y, z)).normalized() if Vector((x, y, z)).length > 1e-9 else Vector((0, 0, 1)), w)
        else:
            rot = Euler(ev(rot_path, 3), euler_mode).to_quaternion()
        nl, nr, ns = _conj_basis(c, ci, loc, rot, scale).decompose()
        if prev_q is not None and nr.dot(prev_q) < 0:
            nr.negate()
        prev_q = nr.copy()
        if rot_path == "rotation_quaternion":
            rv = tuple(nr)
        elif rot_path == "rotation_axis_angle":
            ax, ang = nr.to_axis_angle()
            rv = (ang, ax.x, ax.y, ax.z)
        else:
            e = nr.to_euler(euler_mode, prev_e) if prev_e is not None else nr.to_euler(euler_mode)
            prev_e = e
            rv = tuple(e)
        out[f] = {"location": tuple(nl), rot_path: rv, "scale": tuple(ns)}

    #which channels need writing. loc/rot always (C mixes axes), scale only if it was keyed or isnt uniform
    paths = ["location", rot_path]
    if any(k[0] == "scale" for k in chans) or any(max(v["scale"]) - min(v["scale"]) > 1e-5 for v in out.values()):
        paths.append("scale")
    written = 0
    for path in paths:
        n = 4 if path in ("rotation_quaternion", "rotation_axis_angle") else 3
        for i in range(n):
            fc = chans.get((path, i))
            if fc is None:
                if all(abs(v[path][i] - _DEFAULTS[path][i]) < 1e-7 for v in out.values()):
                    continue
                fc = _new_fc(container, 'pose.bones["%s"].%s' % (name.replace('"', '\\"'), path), i, group)
            #rebuild the whole curve in one go. inserting keys one by one reallocates the array n
            # every python ref to a keyframe turns into garbage pointing at random memory. ask me
            #how i know. fml
            interp = fc.keyframe_points[0].interpolation if len(fc.keyframe_points) else "LINEAR"
            kp = fc.keyframe_points
            kp.clear()
            kp.add(len(frames))
            flat = []
            for f in frames:
                flat += (f, out[f][path][i])
            kp.foreach_set("co", flat)
            for k in kp:
                k.interpolation = interp
                k.handle_left_type = k.handle_right_type = "AUTO_CLAMPED"
            written += len(frames)
            fc.update()
    return written
