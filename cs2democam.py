bl_info = {
    "name": "CS2 Demo-Cam Exporter",
    "author": "OpenAI",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > CS2",
    "description": "Import CS2 POV cameras from CSV, bake & export per-player FBX for UE5.5",
    "category": "Import-Export",
}

import bpy
import csv
import os
from math import radians, cos, sin
from mathutils import Vector, Euler

# --- UI settings stored on the Scene ---
class CS2DemoCamSettings(bpy.types.PropertyGroup):
    csv_path: bpy.props.StringProperty(
        name="CSV File",
        description="Path to your POV CSV",
        subtype='FILE_PATH'
    )
    export_dir: bpy.props.StringProperty(
        name="Export Folder",
        description="Directory to write FBX files",
        subtype='DIR_PATH'
    )
    tickrate: bpy.props.IntProperty(
        name="Demo Tickrate",
        description="CSV tickrate / bake FPS",
        default=64,
        min=1
    )
    export_fps: bpy.props.IntProperty(
        name="FBX Export FPS",
        description="FPS to set on the exported FBX",
        default=64,
        min=1
    )
    frame_step: bpy.props.FloatProperty(
        name="Bake Step",
        description="Frame step for FBX baking",
        default=1.0,
        min=0.001
    )
    head_offset_ue: bpy.props.FloatProperty(
        name="Head Offset (cm)",
        description="Vertical offset to raise camera to head height in UE units",
        default=150.0
    )
    import_scale: bpy.props.FloatProperty(
        name="Import Scale",
        description="FBX import scale factor in UE5",
        default=0.023
    )
    crouch_offset_ue: bpy.props.FloatProperty(
        name="Crouch Offset (cm)",
        description="Vertical drop when fully crouched in UE units",
        default=50.0
    )

# --- The Operator that does the work ---
class CS2_OT_ExportDemoCams(bpy.types.Operator):
    bl_idname = "cs2.export_demo_cams"
    bl_label = "Export Demo Cameras"
    bl_description = "Read the CSV, bake cameras, and export individual FBXs"
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = context.scene.cs2_demo_cam
        csv_path   = bpy.path.abspath(settings.csv_path)
        export_dir = bpy.path.abspath(settings.export_dir)
        tickrate   = settings.tickrate
        export_fps = settings.export_fps
        frame_step = settings.frame_step

        print("[CS2 Export] Starting camera export...")
        print(f"[CS2 Export] Reading CSV: {csv_path}")

        # --- Pass 1: read entire CSV into memory per player ---
        raw_rows = []
        with open(csv_path, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                # convert types
                row['tick'] = int(row['tick'])
                for k in ('pos_x','pos_y','pos_z','view_dir_x','view_dir_y'):
                    row[k] = float(row[k])
                for k in ('is_ducking','is_ducking_in_progress','is_unducking_in_progress','is_standing'):
                    row[k] = int(row[k])
                raw_rows.append(row)

        # prepare camera objects, actions, and raw row lists
        cam_data_map = {}
        for row in raw_rows:
            player = row['player_name']
            if player not in cam_data_map:
                cam_name = f"Cam_{player}"
                cam = bpy.data.cameras.new(cam_name)
                cam_obj = bpy.data.objects.new(cam_name, cam)
                context.collection.objects.link(cam_obj)
                cam_obj.animation_data_create()
                action = bpy.data.actions.new(name=f"{cam_name}_Action")
                cam_obj.animation_data.action = action
                frames = {k: [] for k in ('loc_x','loc_y','loc_z','rot_x','rot_y','rot_z')}
                cam_data_map[player] = {
                    'obj': cam_obj,
                    'action': action,
                    'frames': frames,
                    'rows': []
                }
                print(f"[CS2 Export] Created camera: {cam_name}")
            cam_data_map[player]['rows'].append(row)

        # helper: detect first run-length of a binary flag
        def detect_duration(rows, flag):
            count = 0
            for r in rows:
                if r[flag] == 1:
                    count += 1
                elif count > 0:
                    return count
            return count

        # compute Blender-unit offsets
        head_bu    = (settings.head_offset_ue / 100.0) / settings.import_scale
        crouch_bu  = (settings.crouch_offset_ue / 100.0) / settings.import_scale

        # defaults in ticks if no run found
        DEFAULT_CROUCH_TICKS   = 13
        DEFAULT_UNCROUCH_TICKS = 9

        # --- Pass 1.5: detect dynamic crouch/uncrouch durations per player ---
        for player, data in cam_data_map.items():
            rows = data['rows']
            cd = detect_duration(rows, 'is_ducking_in_progress') or DEFAULT_CROUCH_TICKS
            uc = detect_duration(rows, 'is_unducking_in_progress') or DEFAULT_UNCROUCH_TICKS
            data['crouch_dur']   = cd
            data['uncrouch_dur'] = uc

        # Source2→Blender forward vector
        def src2_forward(yaw_deg, pitch_deg):
            y = radians(-yaw_deg)
            p = radians(pitch_deg)
            fx = cos(p) * cos(y)
            fy = cos(p) * sin(y)
            fz = -sin(p)
            return Vector((fy, fx, fz))

        # helper: smoothstep curve
        def smoothstep(t):
            return t * t * (3.0 - 2.0 * t)

        # --- Pass 2: build keyframe lists with eased Z offsets ---
        for player, data in cam_data_map.items():
            frames   = data['frames']
            cd       = data['crouch_dur']
            uc       = data['uncrouch_dur']

            state = 'standing'
            timer = 0

            for row in data['rows']:
                t   = row['tick']
                x,y,z = row['pos_x'], row['pos_y'], row['pos_z']
                yaw, pitch = row['view_dir_x'], row['view_dir_y']
                duck = row['is_ducking']
                dp   = row['is_ducking_in_progress']
                up   = row['is_unducking_in_progress']
                st   = row['is_standing']

                # orientation (unchanged)
                forward = src2_forward(yaw, pitch)
                quat    = forward.to_track_quat('-Z','Y')
                eul     = quat.to_euler('XYZ')
                eul.z  += radians(-90)

                # refined state-machine (same as before)...
                if state == 'standing':
                    if dp == 1:
                        state = 'crouch'
                        timer = 1

                elif state == 'crouch':
                    if dp == 1 and timer < cd:
                        timer += 1
                    if timer >= cd:
                        state = 'crouched'
                        timer = 0
                    elif dp == 0 and duck == 0:
                        state = 'uncrouch'
                        timer = int(round(uc * (1.0 - (timer / cd))))

                elif state == 'crouched':
                    if up == 1:
                        state = 'uncrouch'
                        timer = 1

                elif state == 'uncrouch':
                    if timer < uc:
                        timer += 1
                    else:
                        state = 'standing'
                        timer = 0

                # --- here’s the eased Z offset ---
                if state == 'crouch':
                    f = timer / cd
                    z_off = smoothstep(f) * crouch_bu
                elif state == 'crouched':
                    z_off = crouch_bu
                elif state == 'uncrouch':
                    f = timer / uc
                    z_off = (1.0 - smoothstep(f)) * crouch_bu
                else:  # standing
                    z_off = 0.0

                final_z = z + head_bu - z_off

                # store keyframes
                frames['loc_x'].append((t, x))
                frames['loc_y'].append((t, y))
                frames['loc_z'].append((t, final_z))
                frames['rot_x'].append((t, eul.x))
                frames['rot_y'].append((t, eul.y))
                frames['rot_z'].append((t, eul.z))

            print(f"[CS2 Export] Processing player: {player}, rows: {len(data['rows'])}")


        # --- Pass 3: create F-Curves in bulk ---
        print("[CS2 Export] Creating F-Curves...")
        for data in cam_data_map.values():
            action = data['action']
            frames = data['frames']
            fcurves = {}
            for idx, key in enumerate(('loc_x','loc_y','loc_z')):
                fcurves[key] = action.fcurves.new(data_path='location', index=idx)
            for idx, key in enumerate(('rot_x','rot_y','rot_z')):
                fcurves[key] = action.fcurves.new(data_path='rotation_euler', index=idx)
            for key, fcu in fcurves.items():
                pts = frames[key]
                if not pts:
                    continue
                fcu.keyframe_points.add(len(pts))
                for i, (f, val) in enumerate(pts):
                    kp = fcu.keyframe_points[i]
                    kp.co = (f, val)
                fcu.keyframe_points.sort()
                fcu.update()
        print("[CS2 Export] F-Curves populated.")

        # --- Pass 3.5: normalize timeline to start at frame 1 ---
        print("[CS2 Export] Computing frame range & offset...")
        first, last = None, None
        for data in cam_data_map.values():
            for fcu in data['action'].fcurves:
                for kp in fcu.keyframe_points:
                    f = kp.co.x
                    if first is None or f < first: first = f
                    if last  is None or f > last:  last  = f
        offset = 1.0 - first
        for data in cam_data_map.values():
            for fcu in data['action'].fcurves:
                for kp in fcu.keyframe_points:
                    kp.co.x += offset
                fcu.update()
        scene = context.scene
        scene.frame_start = 1
        scene.frame_end   = int(last + offset)
        print(f"[CS2 Export] Scene range set: {scene.frame_start} – {scene.frame_end}")

        # --- Pass 4: export each camera as its own FBX ---
        print(f"[CS2 Export] Switching scene FPS to export setting: {export_fps}")
        scene.render.fps = export_fps
        os.makedirs(export_dir, exist_ok=True)

        total = len(cam_data_map)
        for i, data in enumerate(cam_data_map.values(), start=1):
            cam_obj = data['obj']
            action  = data['action']

            # re-assign action to ensure correct baking
            cam_obj.animation_data.action = action

            bpy.ops.object.select_all(action='DESELECT')
            cam_obj.select_set(True)
            bpy.context.view_layer.objects.active = cam_obj

            out_path = os.path.join(export_dir, f"{cam_obj.name}.fbx")
            print(f"[CS2 Export] Exporting ({i}/{total}) {cam_obj.name} → {out_path}")

            bpy.ops.export_scene.fbx(
                filepath=out_path,
                use_selection=True,
                object_types={'CAMERA'},
                bake_anim=True,
                bake_anim_use_all_actions=False,
                bake_anim_use_nla_strips=False,
                bake_anim_step=frame_step,
                bake_anim_simplify_factor=0,
                axis_forward='-Z',
                axis_up='Y'
            )
            print(f"[CS2 Export] Finished {cam_obj.name}")

        print("[CS2 Export] All cameras exported.")
        return {'FINISHED'}

# --- The UI Panel ---
class CS2_PT_DemoCamExporterPanel(bpy.types.Panel):
    bl_label = "CS2 Demo-Cam Exporter"
    bl_idname = "CS2_PT_demo_cam_exporter"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'CS2'

    def draw(self, context):
        layout = self.layout
        s = context.scene.cs2_demo_cam

        layout.prop(s, "csv_path")
        layout.prop(s, "export_dir")
        layout.prop(s, "tickrate")
        layout.prop(s, "export_fps")
        layout.prop(s, "frame_step")
        layout.separator()
        layout.prop(s, "head_offset_ue")
        layout.prop(s, "import_scale")
        layout.separator()
        layout.prop(s, "crouch_offset_ue")
        layout.separator()
        layout.operator("cs2.export_demo_cams", icon='EXPORT')

# --- Registration ---
classes = (
    CS2DemoCamSettings,
    CS2_OT_ExportDemoCams,
    CS2_PT_DemoCamExporterPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.cs2_demo_cam = bpy.props.PointerProperty(type=CS2DemoCamSettings)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.cs2_demo_cam

if __name__ == "__main__":
    register()
