bl_info = {
    "name": "PLY Sequence Importer",
    "author": "Arda Evin",
    "version": (1, 5, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > PLY Sequence",
    "description": "Import a folder of 3DGS PLY files as an animated sequence using the Gaussian Splatting addon",
    "category": "Import-Export",
}

import bpy
import glob
import os
from bpy.app.handlers import persistent
from bpy.props import StringProperty, IntProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup

# ──────────────────────────────────────────────
# Frame-change handler  (module-level so Blender
# can reference it by name after file reloads)
# ──────────────────────────────────────────────

def _ply_update_visibility(scene):
    """Show only the frame object that matches scene.frame_current.
    Clamps to first frame before the sequence and last frame after it."""
    props = scene.ply_sequence_props
    frame_start = props.active_start_frame
    objects_csv = props.active_frame_objects   # comma-separated object names

    if not objects_csv:
        return

    names   = objects_csv.split(",")
    # Clamp: hold first frame before sequence, hold last frame after sequence
    current = max(0, min(scene.frame_current - frame_start, len(names) - 1))

    for i, name in enumerate(names):
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        visible = (i == current)
        obj.hide_viewport = not visible
        obj.hide_render   = not visible


# ──────────────────────────────────────────────
# Persistent load-post handler
# Re-registers the frame handler every time a
# .blend file is opened.
# ──────────────────────────────────────────────

@persistent
def _ply_load_post(filepath):
    """Called after every file load — restores the frame-change handler."""
    _remove_frame_handler()

    # Walk every scene in the file; register handler if any has sequence data
    for scene in bpy.data.scenes:
        if scene.ply_sequence_props.active_frame_objects:
            bpy.app.handlers.frame_change_post.append(_ply_update_visibility)
            # Trigger once so the correct frame is visible immediately
            _ply_update_visibility(scene)
            break   # one handler is enough — it iterates over scenes itself


def _remove_frame_handler():
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

    Works by node *type* so it is robust to name changes across addon versions.
    Only moves nodes whose type is recognised — unknown nodes are left alone.
    """
    if ng is None:
        return

    nodes = {n.name: n for n in ng.nodes}

    # ── Map bl_idname → desired (x, y) ───────────────────────────────────
    # Layout groups:
    #   Selection chain  (top)    : opacity filter + random display %
    #   Main flow        (middle) : Group In → Mesh to Points → … → Group Out
    #   Instance mesh    (upper)  : Ico Sphere → Set Shade Smooth
    #   Scale/rotation   (bottom) : named attrs → vector maths
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
# Operator
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

        # ── Rename, consolidate GN/material, hide each frame object ───────
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

            obj.hide_viewport = True
            obj.hide_render   = True
            frame_objects.append(obj)

        # ── Empty parent ───────────────────────────────────────────────────
        bpy.ops.object.select_all(action='DESELECT')
        bpy.ops.object.empty_add(type='PLAIN_AXES', location=(0.0, 0.0, 0.0))
        empty      = context.active_object
        empty.name = "PLY_Sequence"

        for obj in frame_objects:
            obj.parent = empty
            obj.matrix_parent_inverse = empty.matrix_world.inverted()

        # ── Timeline range ─────────────────────────────────────────────────
        context.scene.frame_start = frame_start
        context.scene.frame_end   = frame_start + len(frame_objects) - 1

        # ── Persist sequence data INTO the scene (survives file save/load) ─
        props.active_frame_objects = ",".join(obj.name for obj in frame_objects)
        props.active_start_frame   = frame_start

        # ── Register frame-change handler ──────────────────────────────────
        _remove_frame_handler()
        bpy.app.handlers.frame_change_post.append(_ply_update_visibility)

        # Show first frame immediately
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
        count     = len(props.active_frame_objects.split(","))

        # Update stored state
        props.active_start_frame = new_start

        # Update timeline range
        context.scene.frame_start = new_start
        context.scene.frame_end   = new_start + count - 1

        # Clamp current frame to valid range and refresh visibility
        context.scene.frame_current = max(new_start,
                                          min(context.scene.frame_current,
                                              new_start + count - 1))
        _ply_update_visibility(context.scene)

        self.report({'INFO'},
                    f"Sequence start moved to frame {new_start} "
                    f"(end: {new_start + count - 1}).")
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
    PLYSEQ_PT_panel,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.ply_sequence_props = PointerProperty(type=PLYSequenceProperties)

    # Register the persistent load-post handler
    if _ply_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_ply_load_post)


def unregister():
    _remove_frame_handler()

    if _ply_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_ply_load_post)

    if hasattr(bpy.types.Scene, 'ply_sequence_props'):
        del bpy.types.Scene.ply_sequence_props

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
