# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio

from . import armature_ops, diag_ops, import_ops, material_ops, profile_ops, session_ops, update_ops

CLASSES = (import_ops.CLASSES + session_ops.CLASSES + profile_ops.CLASSES + material_ops.CLASSES + diag_ops.CLASSES +
           armature_ops.CLASSES + update_ops.CLASSES)
