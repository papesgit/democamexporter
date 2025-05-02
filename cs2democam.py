bl_info = {
    "name": "CS2 Demo-Cam Exporter",
    "author": "Your Name",
    "version": (1, 0, 2),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > CS2 Demo-Cam Exporter",
    "description": "Import camera data from a CSV, bake F-Curves, and export each camera as optimized FBX for UE5.5",
    "category": "Import-Export",
}

import bpy
import csv
import os
from math import radians, cos, sin
from mathutils import Vector

# ---- Addon Properties ----

def register_properties():
    bpy.types.Scene.cs2_csv_path = bpy.props.StringProperty(
        name="CSV File",
        description="Path to the CSV file containing camera data",
        subtype='FILE_PATH'
    )
    bpy.types.Scene.cs2_export_dir = bpy.props.StringProperty(
        name="Export Directory",
        description="Folder where FBX files will be saved",
        subtype='DIR_PATH'
    )
    bpy.types.Scene.cs2_tickrate = bpy.props.IntProperty(
        name="Tickrate",
        description="Demo tickrate (and scene FPS) to use when importing",
        default=64,
        min=1
    )
    bpy.types.Scene.cs2_export_fps = bpy.props.IntProperty(
        name="Export FPS",
        description="Scene FPS setting for FBX export",
        default=64,
        min=1
    )
    bpy.types.Scene.cs2_frame_step = bpy.props.FloatProperty(
        name="Frame Step",
        description="Bake step for FBX export",
        default=1.0,
        min=0.001
    )

def unregister_properties():
    del bpy.types.Scene.cs2_csv_path
    del bpy.types.Scene.cs2_export_dir
    del bpy.types.Scene.cs2_tickrate
    del bpy.types.Scene.cs2_export_fps
    del bpy.types.Scene.cs2_frame_step

# ---- Main Operator ----
class CS2_OT_ExportDemoCams(bpy.types.Operator):
    bl_idname = "cs2.export_demo_cams"
    bl_label = "Export Demo Cameras"
    bl_description = "Import camera data from CSV and export each camera as optimized FBX"

    def execute(self, context):
        scene       = context.scene
        csv_path    = bpy.path.abspath(scene.cs2_csv_path)
        export_dir  = bpy.path.abspath(scene.cs2_export_dir)
        tickrate    = scene.cs2_tickrate
        export_fps  = scene.cs2_export_fps
        frame_step  = scene.cs2_frame_step

        # Validate paths
        if not os.path.isfile(csv_path):
            self.report({'ERROR'}, f"CSV not found: {csv_path}")
            return {'CANCELLED'}
        if not os.path.isdir(export_dir):
            try:
                os.makedirs(export_dir, exist_ok=True)
            except Exception as e:
                self.report({'ERROR'}, f"Cannot create export directory: {e}")
                return {'CANCELLED'}

        # Set initial scene FPS
        scene.render.fps = tickrate

        # Read CSV and accumulate camera data
        cam_data_map = {}
        def src2_forward(yaw_deg, pitch_deg):
            # Convert Source2 yaw/pitch to a Blender forward vector
            y = radians(-yaw_deg)
            p = radians(pitch_deg)
            fx = cos(p) * cos(y)
            fy = cos(p) * sin(y)
            fz = -sin(p)
            return Vector((fy, fx, fz))

        try:
            with open(csv_path, newline='') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    tick   = int(row['tick'])
                    player = row['player_name']
                    if player not in cam_data_map:
                        cam_name = f"Cam_{player}"
                        cam_obj  = bpy.data.objects.new(cam_name, bpy.data.cameras.new(cam_name))
                        bpy.context.collection.objects.link(cam_obj)
                        cam_obj.animation_data_create()
                        action = bpy.data.actions.new(name=f"{cam_name}_Action")
                        cam_obj.animation_data.action = action
                        frames = {k: [] for k in ('loc_x','loc_y','loc_z','rot_x','rot_y','rot_z')}
                        cam_data_map[player] = {'obj': cam_obj, 'action': action, 'frames': frames}

                    entry  = cam_data_map[player]
                    frames = entry['frames']

                    x = float(row['pos_x']); y = float(row['pos_y']); z = float(row['pos_z'])
                    yaw   = float(row['view_dir_x']); pitch = float(row['view_dir_y'])

                    forward = src2_forward(yaw, pitch)
                    quat    = forward.to_track_quat('-Z','Y')
                    eul     = quat.to_euler('XYZ')
                    eul.z  += radians(-90)

                    frames['loc_x'].append((tick, x))
                    frames['loc_y'].append((tick, y))
                    frames['loc_z'].append((tick, z))
                    frames['rot_x'].append((tick, eul.x))
                    frames['rot_y'].append((tick, eul.y))
                    frames['rot_z'].append((tick, eul.z))
        except Exception as e:
            self.report({'ERROR'}, f"Failed to read CSV: {e}")
            return {'CANCELLED'}

        # Bulk-populate F-Curves for location and rotation separately
        for entry in cam_data_map.values():
            action = entry['action']
            frames = entry['frames']
            # Location channels
            loc_curves = [action.fcurves.new(data_path='location', index=i) for i in range(3)]
            # Rotation channels
            rot_curves = [action.fcurves.new(data_path='rotation_euler', index=i) for i in range(3)]
            # Assign keyframes
            for idx, pts in enumerate((frames['loc_x'], frames['loc_y'], frames['loc_z'])):
                if pts:
                    loc_curves[idx].keyframe_points.add(len(pts))
                    for i, (frame, val) in enumerate(pts):
                        loc_curves[idx].keyframe_points[i].co = (frame, val)
                    loc_curves[idx].keyframe_points.sort()
                    loc_curves[idx].update()
            for idx, pts in enumerate((frames['rot_x'], frames['rot_y'], frames['rot_z'])):
                if pts:
                    rot_curves[idx].keyframe_points.add(len(pts))
                    for i, (frame, val) in enumerate(pts):
                        rot_curves[idx].keyframe_points[i].co = (frame, val)
                    rot_curves[idx].keyframe_points.sort()
                    rot_curves[idx].update()

        # Compute global frame range & apply offset
        all_frames = [kp.co.x for entry in cam_data_map.values() for fc in entry['action'].fcurves for kp in fc.keyframe_points]
        min_f = min(all_frames)
        max_f = max(all_frames)
        offset = 1.0 - min_f
        for entry in cam_data_map.values():
            for fc in entry['action'].fcurves:
                for kp in fc.keyframe_points:
                    kp.co.x += offset
                fc.update()
        scene.frame_start = 1
        scene.frame_end   = int(max_f + offset)

        # Switch to export FPS
        scene.render.fps = export_fps

        # Export each camera
        bpy.ops.object.select_all(action='DESELECT')
        for entry in cam_data_map.values():
            cam_obj = entry['obj']
            cam_obj.select_set(True)
            bpy.context.view_layer.objects.active = cam_obj
            out_path = os.path.join(export_dir, f"{cam_obj.name}.fbx")
            bpy.ops.export_scene.fbx(
                filepath=out_path,
                use_selection=True,
                object_types={'CAMERA'},
                bake_anim=True,
                bake_anim_use_all_actions=False,
                bake_anim_use_nla_strips=False,
                bake_anim_step=frame_step,
                bake_anim_simplify_factor=0,
                use_custom_props=False,
                apply_scale_options='FBX_SCALE_ALL',
                axis_forward='-Z',
                axis_up='Y'
            )
            cam_obj.select_set(False)

        self.report({'INFO'}, "CS2 Demo-Cam Exporter: Export complete with proper rotations")
        return {'FINISHED'}

# ---- UI Panel ----
class CS2_PT_DemoCamExporterPanel(bpy.types.Panel):
    bl_label = "CS2 Demo-Cam Exporter"
    bl_idname = "CS2_PT_demo_cam_exporter"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'CS2 Demo-Cam'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "cs2_csv_path")
        layout.prop(scene, "cs2_export_dir")
        layout.prop(scene, "cs2_tickrate")
        layout.prop(scene, "cs2_export_fps")
        layout.prop(scene, "cs2_frame_step")
        layout.operator("cs2.export_demo_cams", icon='EXPORT')

# ---- Registration ----
classes = [CS2_OT_ExportDemoCams, CS2_PT_DemoCamExporterPanel]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    register_properties()

def unregister():
    unregister_properties()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
