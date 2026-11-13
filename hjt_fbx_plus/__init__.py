# SPDX-License-Identifier: GPL-3.0-or-later
# HJT FBX+ - Advanced FBX & Game Asset Importer
# Copyright (C) 2026 HJT Studio
#
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
# even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details. You should have received a copy of the GNU General
# Public License along with this program. If not, see <https://www.gnu.org/licenses/>.

#entry point. blender 3.6-4.1 read bl_info, 4.2+ extensions read blender_manifest.toml
# heavy imports only happen in register() so just importing the package stays cheap

bl_info = {
    "name": "HJT FBX+",
    "author": "HJT Studio",
    "version": (1, 3, 0),
    "blender": (3, 6, 0),
    "location": "File > Import > HJT FBX+  |  3D View > Sidebar > HJT FBX+",
    "description": "Advanced FBX & Game Asset Importer: automatic textures, material reconstruction, "
                   "game profiles and bone chain fixing",
    "doc_url": "https://github.com/Jideeh1/HJT-FBX-PLUS",
    "tracker_url": "https://github.com/Jideeh1/HJT-FBX-PLUS/issues",
    "category": "Import-Export",
}


def register():
    from . import registration
    registration.register()


def unregister():
    from . import registration
    registration.unregister()
