bl_info = {
    "name": "PLY Sequence Importer",
    "author": "Arda Evin",
    "version": (1, 6, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > PLY Sequence",
    "description": "Import a folder of 3DGS PLY files as an animated sequence using the Gaussian Splatting addon",
    "category": "Import-Export",
}

import bpy
import glob
import os
from bpy.props import StringProperty, IntProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup

# ──────────────────────────────────────────────
# Visibility driven by KEYFRAMES (render-safe)
#
# v1.6.0 — Earlier versions toggled hide_viewport /
# hide_render from a frame_change_post handler. That
# triggers a collection / depsgraph resync from inside
# the render's per-frame depsgraph update and crashes
# Blender (EXCEPTION_ACCESS_VIOLATION in
# graph_id_tag_update during RE_RenderAnim).
#
# Instead we keyframe each object's visibility with
# CONSTANT interpolation. The depsgraph evaluates
# keyframed visibility natively and safely during both
# viewport playback and rendering, and it persists in
# the .blend with no runtime handler required.
# ──────────────────────────────────────────────

_VIS_PATHS = ("hide_viewport", "hide_render")


def _set_vis_key(obj, frame, hidden):
    """Insert a hide_viewport + hide_render keyframe on obj at `frame`."""
    obj.hide_viewport = hidden
    obj.hide_render = hidden
    for path in _VIS_PATHS:
        obj.keyframe_insert(data_path=path, frame=frame)


def _clear_sequence_visibility(frame_objects):
    """Remove any existing visibility animation from the frame objects.

    The frame objects are fully owned by this addon (static children of the
    PLY_Sequence empty), so wiping their animation data is safe and avoids
    version-specific fcurve traversal across Blender 4.x / 5.x action slots.
    """
    for obj in frame_objects:
        if obj and obj.animation_data:
            obj.animation_data_clear()


def _keyframe_sequence_visibility(frame_objects, start):
    """Keyframe per-frame visibility for the whole sequence.

    Object i is visible only at timeline frame (start + i). CONSTANT
    interpolation + CONSTANT extrapolation means:
      • the first object holds visible *before* the sequence  (hold first frame)
      • the last  object holds visible *after*  the sequence  (hold last  frame)
    """
    prefs = bpy.context.preferences.edit
    old_interp = prefs.keyframe_new_interpolation_type
    prefs.keyframe_new_interpolation_type = 'CONSTANT'
    try:
        n = len(frame_objects)
        for i, obj in enumerate(frame_objects):
            if obj is None:
                continue
            f = start + i
            if i == 0:
                _set_vis_key(obj, f, False)            # visible (held before → hold first)
                if n > 1:
                    _set_vis_key(obj, f + 1, True)     # hidden afterwards
            elif i == n - 1:
                _set_vis_key(obj, f - 1, True)         # hidden before
                _set_vis_key(obj, f, False)            # visible (held after → hold last)
            else:
                _set_vis_key(obj, f - 1, True)         # hidden before
                _set_vis_key(obj, f, False)            # visible on its frame
                _set_vis_key(obj, f + 1, True)         # hidden after
    finally:
        prefs.keyframe_new_interpolation_type = old_interp


def _remove_legacy_handler():
    """Strip the old runtime frame-change handler if a pre-1.6 file/session
    still has it registered. Keyframed visibility replaces it."""
    bpy.app.handlers.frame_change_post[:] = [
        h for h in bpy.app.handlers.frame_change_post
        if getattr(h, '__name__', '') != '_ply_update_visibility'
    ]


# ──────────────────────────────────────────────
# Properties
# ──────────────────────────────────────────────

class PLYSequenceProperties(PropertyGroup):
    # ── User-facing inputs ──────────────────────
    ply_folder: StringProperty(
        name="PLY Folder",
        description="Folder containing the .ply files to import as a sequence",
        default="",
        subtype='DIR_PATH',
    )
    start_frame: IntProperty(
        name="Start Frame",
        description="Timeline frame number where the sequence begins",
        default=1,
        min=0,
    )

    # ── Saved state (persists inside the .blend) ─
    active_frame_objects: StringProperty(
        name="Active Frame Objects",
        description="Comma-separated list of frame object names (internal use)",
        default="",
    )
    active_start_frame: IntProperty(
        name="Active Start Frame",
        description="Start frame used when the sequence was last imported (internal use)",
        default=1,
    )


# ──────────────────────────────────────────────
# Attribute cleanup helper
# ──────────────────────────────────────────────

# Attributes the addon imports but that are already converted into the
# forms the GN/material actually read — safe to drop immediately.
_REDUNDANT_ATTRS = {
    # Higher-order SH bands (degree 2 & 3) — degree 0+1 kept for view colour
    'sh4','sh5','sh6','sh7','sh8','sh9','sh10','sh11','sh12','sh13','sh14','sh15',
    # Pre-conversion intermediates — GN uses opacity / scale / rot_euler instead
    'log_opacity',  # converted → opacity
    'logscale',     # converted → scale
    'quatxyz',      # converted → rot_euler
    'quatw',        # converted → rot_euler
}

def _strip_redundant_attrs(mesh):
    """Remove attributes that are not needed for display or rendering."""
    names = [a.name for a in mesh.attributes if a.name in _REDUNDANT_ATTRS]
    for name in names:
        mesh.attributes.remove(mesh.attributes[name])


def _tidy_gn_tree(ng):
    """Arrange GaussianSplatting GN nodes into a clean left-to-right layout.

    Only moves nodes whose name is recognised — unknown nodes are left alone.
    """
    if ng is None:
        return

    nodes = {n.name: n for n in ng.nodes}

    positions = {
        # Selection chain ────────────────────────────────
        "Named Attribute":          (-950,  450),   # opacity
        "Value":                    (-950,  250),   # opacity threshold
        "Math":                     (-700,  380),   # opacity > threshold
        "Boolean":                  (-950,   50),   # display-all toggle
        "Random Value":             (-950, -150),   # random display %
        "Math.001":                 (-700,  -70),   # MAX(toggle, random)
        "Boolean Math":             (-450,  200),   # AND → Selection

        # Main geometry flow ─────────────────────────────
        "Group Input":              (-1200,   0),
        "Mesh to Points":           ( -150,   0),

        # Instance mesh (upper) ──────────────────────────
        "Ico Sphere":               (  100,  350),
        "Set Shade Smooth":         (  350,  350),

        # Points branch ──────────────────────────────────
        "Set Point Radius":         (  100, -150),

        # Scale / rotation chain (bottom) ────────────────
        "Named Attribute.001":      (-950, -400),   # scale
        "Vector Math":              (-700, -400),   # scale * 2
        "Vector Math.001":          (-450, -400),   # dot → radius
        "Named Attribute.002":      (-950, -600),   # rot_euler

        # Output chain ───────────────────────────────────
        "Instance on Points":       (  650,  150),
        "Switch":                   (  900,    0),
        "Set Material":             ( 1150,    0),
        "Realize Instances":        ( 1400,    0),
        "Group Output":             ( 1650,    0),
    }

    for name, (x, y) in positions.items():
        if name in nodes:
            nodes[name].location = (x, y)


# ──────────────────────────────────────────────
# Operator — import sequence
# ──────────────────────────────────────────────

class PLYSEQ_OT_import(Operator):
    bl_idname  = "plyseq.import"
    bl_label   = "Import PLY Sequence"
    bl_description = (
        "Import every .ply file in the chosen folder as one frame of an animated "
        "sequence, parented to a single Empty for easy transform control"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props       = context.scene.ply_sequence_props
        ply_folder  = bpy.path.abspath(props.ply_folder).rstrip("\\/")
        frame_start = props.start_frame

        # ── Validation ────────────────────────────────────────────────────
        if not ply_folder:
            self.report({'ERROR'}, "No PLY folder selected.")
            return {'CANCELLED'}

        if not os.path.isdir(ply_folder):
            self.report({'ERROR'}, f"Folder not found: {ply_folder}")
            return {'CANCELLED'}

        if not hasattr(bpy.ops.object, 'import_gaussian_splatting'):
            self.report(
                {'ERROR'},
                "The '3D Gaussian Splatting' addon is not installed / enabled. "
                "Please enable it in Preferences > Add-ons first.",
            )
            return {'CANCELLED'}

        files = sorted(glob.glob(os.path.join(ply_folder, "*.ply")))
        if not files:
            self.report({'ERROR'}, f"No .ply files found in: {ply_folder}")
            return {'CANCELLED'}

        # ── Import via the 3DGS addon ─────────────────────────────────────
        imported_objects = []
        for filepath in files:
            bpy.ops.object.select_all(action='DESELECT')
            bpy.ops.object.import_gaussian_splatting(filepath=filepath)
            selected = list(context.selected_objects)
            if selected:
                imported_objects.append(selected[0])

        if not imported_objects:
            self.report({'ERROR'}, "Import produced no objects. Check your PLY files.")
            return {'CANCELLED'}

        # ── Collect shared GN group + material from the first object ──────
        first      = imported_objects[0]
        shared_gn  = next(
            (m.node_group for m in first.modifiers
             if m.type == 'NODES' and m.node_group),
            None,
        )
        shared_mat = first.data.materials[0] if first.data.materials else None

        # ── Tidy the GN node layout ────────────────────────────────────────
        _tidy_gn_tree(shared_gn)

        # ── Rename, consolidate GN/material, strip attrs each frame object ─
        frame_objects = []
        for i, obj in enumerate(imported_objects):
            obj.name      = f"frame_{i:04d}"
            obj.data.name = f"frame_{i:04d}_mesh"

            if shared_gn:
                for mod in obj.modifiers:
                    if mod.type == 'NODES':
                        old_ng = mod.node_group
                        mod.node_group = shared_gn
                        if old_ng and old_ng != shared_gn:
                            try:
                                bpy.data.node_groups.remove(old_ng)
                            except Exception:
                                pass

            if shared_mat and obj.data.materials:
                old_mat = obj.data.materials[0]
                obj.data.materials[0] = shared_mat
                if old_mat and old_mat != shared_mat:
                    try:
                        bpy.data.materials.remove(old_mat)
                    except Exception:
                        pass

            # ── Strip redundant attributes (saves ~50% file size) ──────────
            _strip_redundant_attrs(obj.data)
            frame_objects.append(obj)

        # ── Empty parent ───────────────────────────────────────────────────
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0.0, 0.0, 0.0))
        empty      = context.active_object
        empty.name = "PLY_Sequence"

        for obj in frame_objects:
            obj.parent = empty
            obj.matrix_parent_inverse = empty.matrix_world.inverted()

        # ── Keyframe visibility (render-safe) ──────────────────────────────
        _remove_legacy_handler()
        _clear_sequence_visibility(frame_objects)
        _keyframe_sequence_visibility(frame_objects, frame_start)

        # ── Timeline range ─────────────────────────────────────────────────
        context.scene.frame_start = frame_start
        context.scene.frame_end   = frame_start + len(frame_objects) - 1

        # ── Persist sequence data INTO the scene (for the move feature) ────
        props.active_frame_objects = ",".join(obj.name for obj in frame_objects)
        props.active_start_frame   = frame_start

        # Refresh to the first frame
        context.scene.frame_set(frame_start)

        self.report(
            {'INFO'},
            f"PLY Sequence: {len(frame_objects)} frames imported "
            f"(frames {frame_start}–{frame_start + len(frame_objects) - 1}). "
            f"Parent empty: '{empty.name}'.",
        )
        return {'FINISHED'}


# ──────────────────────────────────────────────
# Operator — update start frame on existing sequence
# ──────────────────────────────────────────────

class PLYSEQ_OT_update_start_frame(Operator):
    bl_idname      = "plyseq.update_start_frame"
    bl_label       = "Apply Start Frame"
    bl_description = "Move the existing sequence to start at the new Start Frame value"
    bl_options     = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ply_sequence_props

        if not props.active_frame_objects:
            self.report({'ERROR'}, "No active sequence found. Import a sequence first.")
            return {'CANCELLED'}

        new_start = props.start_frame
        names     = props.active_frame_objects.split(",")
        objs      = [bpy.data.objects.get(n) for n in names]
        count     = len(names)

        # Re-key visibility at the new start frame
        _remove_legacy_handler()
        _clear_sequence_visibility(objs)
        _keyframe_sequence_visibility(objs, new_start)

        # Update stored state + timeline range
        props.active_start_frame = new_start
        context.scene.frame_start = new_start
        context.scene.frame_end   = new_start + count - 1
        context.scene.frame_current = max(new_start,
                                          min(context.scene.frame_current,
                                              new_start + count - 1))
        context.scene.frame_set(context.scene.frame_current)

        self.report({'INFO'},
                    f"Sequence start moved to frame {new_start} "
                    f"(end: {new_start + count - 1}).")
        return {'FINISHED'}


# ──────────────────────────────────────────────
# Operator — bake/upgrade a pre-1.6 (handler-based) sequence
# ──────────────────────────────────────────────

class PLYSEQ_OT_bake_visibility(Operator):
    bl_idname      = "plyseq.bake_visibility"
    bl_label       = "Bake Visibility Keyframes"
    bl_description = (
        "Convert a sequence imported with an older version (runtime handler) to "
        "render-safe visibility keyframes. Removes the legacy frame-change handler"
    )
    bl_options     = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.ply_sequence_props

        if not props.active_frame_objects:
            self.report({'ERROR'}, "No active sequence found to bake.")
            return {'CANCELLED'}

        names = props.active_frame_objects.split(",")
        objs  = [bpy.data.objects.get(n) for n in names]
        start = props.active_start_frame

        _remove_legacy_handler()
        _clear_sequence_visibility(objs)
        _keyframe_sequence_visibility(objs, start)

        context.scene.frame_start = start
        context.scene.frame_end   = start + len(names) - 1
        context.scene.frame_set(context.scene.frame_current)

        self.report({'INFO'},
                    f"Baked render-safe visibility keyframes for {len(names)} frames. "
                    f"You can now render the animation. Save the file to keep it.")
        return {'FINISHED'}


# ──────────────────────────────────────────────
# Panel
# ──────────────────────────────────────────────

class PLYSEQ_PT_panel(Panel):
    bl_label       = "PLY Sequence Importer"
    bl_idname      = "PLYSEQ_PT_panel"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "PLY Sequence"

    def draw(self, context):
        layout = self.layout
        props  = context.scene.ply_sequence_props

        col = layout.column(align=True)
        col.label(text="PLY Folder:")
        col.prop(props, "ply_folder", text="")

        layout.separator()

        col = layout.column(align=True)
        col.label(text="Start Frame:")
        col.prop(props, "start_frame", text="")

        layout.separator()
        layout.operator("plyseq.import", icon='IMPORT', text="Import PLY Sequence")

        # Show active sequence info + start-frame override if one is loaded
        if props.active_frame_objects:
            layout.separator()
            box = layout.box()
            count = len(props.active_frame_objects.split(","))
            box.label(text="Active Sequence:", icon='SEQUENCE')
            box.label(text=f"  {count} frames  |  current start: {props.active_start_frame}")
            box.separator()
            box.label(text="Move sequence to:")
            row = box.row(align=True)
            row.prop(props, "start_frame", text="Frame")
            row.operator("plyseq.update_start_frame", text="Apply", icon='FILE_REFRESH')

            # Upgrade button — only needed for files imported with v1.5 or older
            legacy = any(getattr(h, '__name__', '') == '_ply_update_visibility'
                         for h in bpy.app.handlers.frame_change_post)
            if legacy:
                box.separator()
                box.label(text="Legacy handler detected:", icon='ERROR')
                box.operator("plyseq.bake_visibility",
                             text="Bake Render-Safe Keyframes", icon='KEYFRAME_HLT')

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Requires:", icon='INFO')
        col.label(text="  3D Gaussian Splatting addon")


# ──────────────────────────────────────────────
# Register / Unregister
# ──────────────────────────────────────────────

_classes = (
    PLYSequenceProperties,
    PLYSEQ_OT_import,
    PLYSEQ_OT_update_start_frame,
    PLYSEQ_OT_bake_visibility,
    PLYSEQ_PT_panel,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.ply_sequence_props = PointerProperty(type=PLYSequenceProperties)

    # Clean up any legacy runtime handler left over from v1.5 or earlier
    _remove_legacy_handler()


def unregister():
    _remove_legacy_handler()

    if hasattr(bpy.types.Scene, 'ply_sequence_props'):
        del bpy.types.Scene.ply_sequence_props

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
