bl_info = {
    "name": "Sausage64 Character Export",
    "description": "Exports a sausage-link character with animations.",
    "author": "Buu342",
    "version": (1, 1),
    "blender": (2, 80, 0),
    "location": "File > Export > Sausage64 Character (.S64)",
    "warning": "",
    "wiki_url": "https://github.com/buu342/Blender-Sausage64",
    "tracker_url": "",
    "support": 'COMMUNITY',
    "category": "Import-Export"
}

import re
import bpy
import copy
import math
import bmesh
import operator
import traceback
import mathutils
import itertools
import collections
from bpy_extras.io_utils import axis_conversion

DefaultAnimFPS = 30.0
DebugS64Export = False

class S64Vertex:
    def __init__(self):
        self.coor = None # Vertex X Y Z
        self.norm = None # Vertex Normal X Y Z
        self.colr = None # Vertex Color R G B
        self.uv   = None # Vertex U V
    def __str__(self):
        return ("S64Vert: [%.4f, %.4f, %.4f]" % self.coor[:])

class S64Face:
    def __init__(self):
        self.verts = [] # List of vertex indices
        self.mat = ""   # Material name for this face
    def __str__(self):
        string = "S64Face:"
        for i in self.verts:
            string = string+" "+str(i)
        string = string+" "+self.mat
        return string

class S64Mesh:
    def __init__(self, name):
        self.name  = name # Skeleton name
        self.verts = {}   # Dict of vertices
        self.faces = []   # List of faces
        self.mats  = []   # List of materials used by this mesh
        self.props = []   # List of custom properties
        self.root  = None # Bone root location
    def __str__(self):
        string = "S64Mesh: '"+self.name+"'\n"
        string = string+"Root: "+str(self.root)+"\n"
        string = string+"Materials: "+str(self.mats)+"\n"
        string = string + "Verts:\n"
        for i in self.verts:
            string = string + "\t"+str(i)+" -> "+str(self.verts[i])+"\n"
        string = string + "Faces:\n"
        for i in self.faces:
            string = string + "\t"+str(i)+"\n"
        return string
    def sharesMats(self, other):
        if (len(self.mats) != len(other.mats)):
           return False
        for m in self.mats:
            if (not m in other.mats):
                return False
        return True

class S64Anim:
    def __init__(self, name):
        self.name   = name # Animation name
        self.frames = {}   # Dict of animation frames
    def __str__(self):
        string = "S64Anim: '"+self.name+"'\n"
        for i in self.frames:
            string = string + "\tFrame "+str(i)+"\n"
            for j in self.frames[i]:
                string = string+str(self.frames[i][j])+"\n"
        return string

class S64KeyFrame:
    def __init__(self, bone):
        self.bone  = bone # Affected bone name
        self.pos   = None # Bone position
        self.ang   = None # Bone rotation
        self.scale = None # Bone scale
    def __str__(self):
        string = "\t\tS64Keyframe: '"+self.bone+"'\n"
        string = string+"\t\t Pos = "+str(self.pos)+"\n"
        string = string+"\t\t Ang = "+str(self.ang)+"\n"
        string = string+"\t\t Scale = "+str(self.scale)
        return string

def isNewBlender():
    return bpy.app.version >= (2, 80)

def matmul(a, b):
    if (isNewBlender()):
        return operator.matmul(a, b)
    return a*b

def validstring(a):
    if (a[0].isdigit()):
        a = "_" + a
    return re.sub('\W|^(?=\d)','_', a)

def setupData(self, object, skeletonList, meshList):
    finalList = collections.OrderedDict()
    animList = collections.OrderedDict()
    warnedgroups = []
    
    # The first element should always be None (for objects without bones)
    finalList["None"] = S64Mesh("None")
    finalList["None"].root = mathutils.Vector((0, 0, 0))
    
    # Go through the list of bones and add all deformable bones
    for v in skeletonList:
        for b in v.data.bones:
            if b.use_deform: # Ignore non deformable bones
                if (b.name == "None"):
                    self.report({'ERROR'}, 'You should not have a bone named "None"')
                    return 'CANCELLED', None
                
                finalList[b.name] = S64Mesh(b.name)
                finalList[b.name].root = mathutils.Vector((b.head_local.x, b.head_local.y, b.head_local.z))
                
                # Add custom bone properties
                for prop in b.keys():
                    if (prop != "_RNA_UI"):
                        finalList[b.name].props.append(prop)
                        
    # Now go through all the meshes and create the model data, finding out which vert belongs to which bone
    for m in meshList:
        
        # If auto smooth is enabled, calculate the split normals
        if (m.data.has_custom_normals):
            m.data.calc_normals_split()
        
        # Create a copy of the mesh
        self.duplicatemodel = m
        mcopy = None
        if (isNewBlender()):
            mcopy = self.duplicatemodel.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
        else:
            mcopy = self.duplicatemodel.to_mesh(scene=bpy.context.scene, apply_modifiers=True, settings='PREVIEW')
            
        # Perform triangulation if necessary
        if (self.setting_triangulate):
            bm = bmesh.new()
            bm.from_mesh(mcopy)
            if (isNewBlender()):
                bmesh.ops.triangulate(bm, faces=bm.faces[:])
            else:
                bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method=0, ngon_method=0)
            bm.to_mesh(mcopy)
            bm.free()
        
        # Setup a bmesh and validate the data
        bm = bmesh.new()
        bm.from_mesh(mcopy)
        bm.verts.index_update()
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        if (isNewBlender()):
            tris = bm.calc_loop_triangles()
        else:
            tris = bm.calc_tessface()
            
        # Warn if the model is small
        if (bm.calc_volume() < 10000/self.setting_scale):
            self.report({'WARNING'}, 'Your model seems quite small, it might not render properly!')
            
        for f in bm.faces:
            face = S64Face()
            
            bone_name = "None"
            
            # Ensure we don't have too many verts in this face
            if (len(f.verts) > 4):
                self.report({'ERROR'}, 'Faces should not have more than 4 vertices!')
                return 'CANCELLED', None
                
            # Go through all the verts
            for i, v in enumerate(f.verts):
                loop_index = f.loops[i].index
                vert_index = v.index
                
                # Create the vertex
                vert = S64Vertex()
                vert.coor = v.co[:]
                
                # Add the vertex normal
                if (mcopy.has_custom_normals):
                    vert.norm = mcopy.data.loops[loop_index].normal[:]
                else:
                    vert.norm = v.normal[:]
                
                # Try to add the vertex color
                try:
                    vert.colr = mcopy.vertex_colors[bm.loops.layers.color.active.name].data[loop_index].color
                except (IndexError, KeyError, AttributeError) as e:
                    vert.colr = (1.0, 1.0, 1.0)
                
                # Try to add the vertex UV
                try:
                    vert.uv = mcopy.uv_layers[bm.loops.layers.uv.active.name].data[loop_index].uv
                    vert.uv = (vert.uv[0], 1-vert.uv[1])
                except (IndexError, KeyError, AttributeError) as e:
                    vert.uv = (0.0, 0.0)
                
                # Get the vertex weight and store this vertex in the corresponding bone in our mesh list
                layer_deform = bm.verts.layers.deform.active
                vgroup_names = {vgroup.index: vgroup.name for vgroup in m.vertex_groups}
                if (layer_deform is not None):
                    for g in v[layer_deform].items():
                        if (g[1] > 0.5):
                            if (g[0] in vgroup_names):
                                if (vgroup_names[g[0]] in finalList):
                                    bone_name = vgroup_names[g[0]]
                                elif (vgroup_names[g[0]] not in warnedgroups):
                                    self.report({'WARNING'}, 'Vertex group "'+vgroup_names[g[0]]+'" does not match any bone names. Assuming None.')
                                    warnedgroups.append(vgroup_names[g[0]])
                finalList[bone_name].verts[len(finalList[bone_name].verts)] = vert
                
                # Add this vertex's index to the face's vertex list
                face.verts.append(len(finalList[bone_name].verts)-1)
            
            # Get the material used by this face
            try:
                face.mat = m.material_slots[f.material_index].name
            except IndexError:
                face.mat = "None"
            
            # Store the material used by this face in the finalList
            if (not face.mat in finalList[bone_name].mats):
                finalList[bone_name].mats.append(face.mat)
                
            # Store this face in finalList
            finalList[bone_name].faces.append(face)
        
    # Remove any list element that's empty
    for i in list(finalList.keys()):
        if (len(finalList[i].faces) == 0):
            del finalList[i]
            
    # Set each skeleton to pose mode
    for v in skeletonList:
        v.data.pose_position = "POSE"
        if (isNewBlender()):
            bpy.context.view_layer.update()
        else:
            bpy.context.scene.update()
        
    # Now iterate through all the animations and add them to the list
    if (len(bpy.data.actions) > 0):
        for a in bpy.data.actions:
        
            # Ignore actions with fake users
            if (a.use_fake_user):
                continue
                
            anim = S64Anim(a.name)
            start = a.frame_range.x
            end = a.frame_range.y
            keyscale = (DefaultAnimFPS/self.setting_animfps)
            
            # Cycle through all the fcurves and add keyframe numbers that exist
            for fcurve in a.fcurves:
                for p in fcurve.keyframe_points:
                    if not p.co.x in anim.frames:
                        anim.frames[p.co.x*keyscale] = {}
           
            # Get the current frame so that we can reset it later
            framebefore = bpy.context.scene.frame_current
           
            # Now that we have a list of places where keyframes exist, lets get anim data for that frame
            for k in anim.frames:

                # Modify the current Blender keyframe so we can get the pose data
                bpy.context.scene.frame_set(int(k/keyscale))

                # Go through all skeletons and add the bone's data
                for s in skeletonList:
                
                    # Get the action before we mess with it, and then mess with it
                    actionbefore = s.animation_data.action
                    s.animation_data.action = a
                    if (isNewBlender()):
                        bpy.context.view_layer.update()
                    else:
                        bpy.context.scene.update()
                    
                    for b in s.data.bones:
                    
                        # Ensure this bone exists in finalList
                        if (not b.name in finalList):
                            continue
                        
                        boneframe = S64KeyFrame(b.name)
                        
                        # Grab the bone data for its rest and posed version
                        obone = s.data.bones[b.name]
                        pbone = s.pose.bones[b.name]

                        # Get the translation matricies from the head location
                        trans = mathutils.Matrix.Translation(obone.head_local)
                        itrans = mathutils.Matrix.Translation(-obone.head_local)

                        # Calculate the bone space matricies and convert them to world space
                        mat_final = matmul(pbone.matrix, obone.matrix_local.inverted())
                        mat_final = matmul(itrans, matmul(mat_final, trans))
                        
                        # Store the different data from the matricies
                        boneframe.pos   = mat_final.to_translation()
                        boneframe.scale = mat_final.to_scale()
                        
                        # Store the rotation
                        boneframe.ang   = mat_final.to_quaternion()
                        
                        # Add this bone's frame data to the to the list of frame data for this keyframe
                        anim.frames[k][b.name] = boneframe
                
                    # Put the armature back to its original animation (IE the one before we started exporting)
                    s.animation_data.action = actionbefore
                
            # Fix the frame number
            bpy.context.scene.frame_set(int(framebefore))
            if (isNewBlender()):
                bpy.context.view_layer.update()
            else:
                bpy.context.scene.update()
                
            # Store the animation in the animation list
            animList[anim.name] = anim
    
    # Sort the faces by texture
    for i in finalList:
        finalList[i].faces.sort(key=lambda x: x.mat, reverse=False)

    # Fix meshes that have no root (because they don't have bones)
    if (finalList[i].root == None):
        finalList[i].root = mathutils.Vector((0.0, 0.0, 0.0))
    
    # Now, lets find redundant vertices and remove them
    for i in finalList:
        vertscopy = finalList[i].verts.copy()
        for i1, v1 in vertscopy.items():
            if (not i1 in finalList[i].verts):
                continue
            for i2, v2 in vertscopy.items():
                if ((i1 == i2) or (not i2 in finalList[i].verts)):
                    continue
                if ((v1.coor == v2.coor) and (v1.norm == v2.norm) and (v1.colr == v2.colr) and (v1.uv == v2.uv)):
                    for f in finalList[i].faces:
                        for fi, fv in enumerate(f.verts):
                            if (fv == i2):
                                f.verts[fi] = i1
                    finalList[i].verts.pop(i2)

    # Because the vertex indices are now not in order, let's fix that
    for i in finalList:
        vertdict = {}
        vertcount = 0
        ordered = collections.OrderedDict()
            
        # Get the list of verts in order of appearance from the faces
        for f in finalList[i].faces:
            for k, v in enumerate(f.verts):
                if (not f.verts[k] in vertdict):
                    vertdict[f.verts[k]] = vertcount
                    vertcount = vertcount + 1
                f.verts[k] = vertdict[f.verts[k]]
        
        # Then sort the vertex dictionary based on the lookup table made in the previous loop
        for v in finalList[i].verts:
            ordered[vertdict[v]] = finalList[i].verts[v]
        finalList[i].verts = collections.OrderedDict(sorted(ordered.items()))
    
    # Animation keyframes are also not in order, so sort them too
    for i in animList:
        animList[i].frames = collections.OrderedDict(sorted(animList[i].frames.items()))
    
    # Sort animations alphabetically
    animList = collections.OrderedDict(sorted(animList.items()))
        
    # Return the list with all the meshes and animations
    return finalList, animList

def optimizeData(self, context, finalList, animList):

    # Sort meshes alphabetically
    finalList = collections.OrderedDict(sorted(finalList.items()))
    
    # Remove empty keyframes and animations
    for i in animList:
        for f in list(animList[i].frames):
            if (len(animList[i].frames[f]) == 0):
                del animList[i].frames[f]
    for i in list(animList):
        if (len(animList[i].frames) == 0):
            self.report({'WARNING'}, "Animation '"+animList[i].name+"' was deleted because it was empty.")
            del animList[i]
    
    # Print the model data for debugging purposes
    if (DebugS64Export):
        for i in finalList:
            print(finalList[i])
        for i in animList:
            print(animList[i])
    
    # Return the sorted lists
    return finalList, animList

def writeFile(self, object, finalList, animList):
    with open(self.filepath, 'w') as file:
        file.write("/**********************************\n")
        file.write("      Sausage64 Character Mesh\n")
        file.write("         Script by Buu342\n")
        file.write("            Version 1.1\n")
        file.write("**********************************/\n\n")
        
        # Write the mesh data
        for n, m in finalList.items():
        
            # Start a new mesh
            file.write("BEGIN MESH "+validstring(n)+"\n")
            if (self.setting_upaxis == 'Z'):
                file.write("ROOT "+("%.4f " % (m.root.x*self.setting_scale))+("%.4f " % (m.root.y*self.setting_scale))+("%.4f\n" % (m.root.z*self.setting_scale)))
            else:
                file.write("ROOT "+("%.4f " % (m.root.x*self.setting_scale))+("%.4f " % (m.root.z*self.setting_scale))+("%.4f\n" % (-m.root.y*self.setting_scale)))
            if (len(m.props) > 0):
                file.write("PROPERTIES "+' '.join(m.props)+"\n")
            
            # Write the list of vertices
            file.write("BEGIN VERTICES\n")
            for k, v in m.verts.items():
                if (self.setting_upaxis == 'Z'):
                    file.write(("%.4f " % (v.coor[0]*self.setting_scale))+("%.4f " % (v.coor[1]*self.setting_scale))+("%.4f " % (v.coor[2]*self.setting_scale)))
                    file.write("%.4f %.4f %.4f " % v.norm[:])
                else:
                    file.write(("%.4f " % (v.coor[0]*self.setting_scale))+("%.4f " % (v.coor[2]*self.setting_scale))+("%.4f " % (-v.coor[1]*self.setting_scale)))
                    file.write(("%.4f " % v.norm[0])+("%.4f " % v.norm[2])+("%.4f " % (-v.norm[1])))
                file.write(("%.4f " % v.colr[0])+("%.4f " % v.colr[1])+("%.4f " % v.colr[2]))
                file.write("%.4f %.4f" % v.uv[:])
                file.write("\n")
            file.write("END VERTICES\n")
            
            # Write the list of faces
            file.write("BEGIN FACES\n")
            for f in m.faces:
                file.write(str(len(f.verts))+" ")
                for v in f.verts:
                    file.write(str(v)+" ")
                if (f.mat != "" and f.mat is not None):
                    file.write(validstring(f.mat)+"\n")
                else:
                    file.write("None\n")
            file.write("END FACES\n")
            
            # End this mesh
            file.write("END MESH "+validstring(n)+"\n\n")
            
        # Write the animation data
        for n, a in animList.items():
        
            # Start a new Animation
            file.write("BEGIN ANIMATION "+validstring(n)+"\n")
            
            # Write the list of keyframes
            for kf in a.frames:
                file.write("BEGIN KEYFRAME "+str(int(kf))+"\n")
                for b in a.frames[kf]:
                    frame = a.frames[kf][b]
                    file.write(validstring(frame.bone))
                    if (self.setting_upaxis == 'Z'):
                        file.write((" %.4f " % (frame.pos.x*self.setting_scale))+("%.4f " % (frame.pos.y*self.setting_scale))+("%.4f" % (frame.pos.z*self.setting_scale)))
                        file.write((" %.4f " % frame.ang.w)+(" %.4f " % frame.ang.x)+("%.4f " % frame.ang.y)+("%.4f" % frame.ang.z))
                        file.write((" %.4f " % frame.scale.x)+("%.4f " % frame.scale.y)+("%.4f\n" % frame.scale.z))
                    else:
                        file.write((" %.4f " % (frame.pos.x*self.setting_scale))+("%.4f " % (frame.pos.z*self.setting_scale))+("%.4f" % (-frame.pos.y*self.setting_scale)))
                        file.write((" %.4f " % (frame.ang.w))+(" %.4f " % frame.ang.x)+("%.4f " % frame.ang.z)+("%.4f" % (-frame.ang.y)))
                        file.write((" %.4f " % frame.scale.x)+("%.4f " % frame.scale.z)+("%.4f\n" % frame.scale.y))
                file.write("END KEYFRAME "+str(int(kf))+"\n")
                
            file.write("END ANIMATION "+validstring(n)+"\n\n")
            
    self.report({'INFO'}, 'File exported sucessfully!')
    return {'FINISHED'}

def CleanUp(meshList, skeletonList, oldmodes, oldposes, oldactive):
    if (isNewBlender()):
        viewscene = bpy.context.view_layer
    else:
        viewscene = bpy.context.scene
    for v in meshList:
        viewscene.objects.active = v
        bpy.ops.object.mode_set(mode=oldmodes[v])
    for v in skeletonList:
        viewscene.objects.active = v
        bpy.ops.object.mode_set(mode=oldmodes[v])
        v.data.pose_position = oldposes[v]
        if (isNewBlender()):
            bpy.context.view_layer.update()
        else:
            bpy.context.scene.update()
    viewscene.objects.active = oldactive

class ObjectExport(bpy.types.Operator):
    """Exports a sausage-link character with animations."""
    bl_idname = "object.export_sausage64"
    bl_label = "Sausage64 Character Export" # The text on the export button
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".S64"
    
    filter_glob          = bpy.props.StringProperty(default="*.S64", options={'HIDDEN'}, maxlen=255)
    setting_triangulate  = bpy.props.BoolProperty(name="Triangulate", description="Triangulate objects.", default=False)
    setting_onlyselected = bpy.props.BoolProperty(name="Selected only", description="Export selected objects only.", default=False)
    setting_onlyvisible  = bpy.props.BoolProperty(name="Visible only", description="Export visible objects only.", default=True)
    setting_animfps      = bpy.props.FloatProperty(name="Animation FPS", description="By default, Sausage64 assumes animations are 30FPS. Changing this value will scale the animation to match this framerate.", min=0.0, max=1000.0, default=30.0)
    setting_scale        = bpy.props.FloatProperty(name="Export Scale", description="The size of the exported model", min=0.0, max=1000.0, default=1.0)
    setting_upaxis       = bpy.props.EnumProperty(name="Up Axis", description="The selected axis points upward", items=(('Z', "Z", "The Z axis points up"), ('Y', "Y", "The Y axis points up")), default='Z')
    filepath             = bpy.props.StringProperty(subtype='FILE_PATH')    

    # If we are running on Blender 2.9.3 or newer, it will expect the new "annotation"
    # syntax on these parameters. In order to make newer versions of blender happy
    # we will hack these parameters into the annotation dictionary
    if isNewBlender():
        __annotations__ = {"filter_glob" : filter_glob,
                           "setting_triangulate" : setting_triangulate,
                           "setting_onlyselected" : setting_onlyselected,
                           "setting_onlyvisible" : setting_onlyvisible,
                           "setting_animfps" : setting_animfps,
                           "setting_scale" : setting_scale,
                           "setting_upaxis" : setting_upaxis,
                           "filepath" : filepath}
    
    def execute(self, context):
        skeletonList = []
        meshList = []
        self.filepath = bpy.path.ensure_ext(self.filepath, ".S64")
        self.duplicatemodel = None
                
        # Pick out what objects we're going to look over
        list = bpy.data.objects
        if (self.setting_onlyselected):
            list = bpy.context.selected_objects
            
        # Start the actual parsing by organizing all the objects in the scene into an array
        for v in list:
            if (not self.setting_onlyvisible or ((isNewBlender() and v.visible_get()) or (not isNewBlender() and v.is_visible(bpy.context.scene)))):
                if (v.type == 'ARMATURE'):
                    skeletonList.append(v)
                elif (v.type == 'MESH'):
                    meshList.append(v)
                    
        # Warn if stuff might be missing
        list = bpy.data.objects
        meshcount = 0
        skeletoncount = 0
        for v in list:
            if (v.type == 'ARMATURE'):
                skeletoncount = skeletoncount + 1
            elif (v.type == 'MESH'):
                meshcount = meshcount + 1
        if (meshcount > 0 and not meshList):
            self.report({'WARNING'}, 'No mesh was exported with the selected options. Did you mean to do this?')
            return {'CANCELLED'}
        if (self.setting_onlyselected and skeletoncount > 0 and not skeletonList):
            self.report({'WARNING'}, 'No skeleton was exported with the selected options. Did you mean to do this?')
                
        # Next, organize the data further by splitting them into categories
        oldmodes = {}
        oldposes = {}
        if (isNewBlender()):
            viewscene = bpy.context.view_layer
        else:
            viewscene = bpy.context.scene
        oldactive = viewscene.objects.active
        try:
            # Force the objects in the scene to specific modes before export
            for v in meshList:
                oldmodes[v] = v.mode
                viewscene.objects.active = v
                bpy.ops.object.mode_set(mode="OBJECT")
            for v in skeletonList:
                oldmodes[v] = v.mode
                viewscene.objects.active = v
                bpy.ops.object.mode_set(mode="OBJECT")
                oldposes[v] = v.data.pose_position
                v.data.pose_position = "REST"
                if (isNewBlender()):
                    bpy.context.view_layer.update()
                else:
                    bpy.context.scene.update()
            viewscene.objects.active = oldactive
            
            # Perform the data parsing
            finalList, animList = setupData(self, context, skeletonList, meshList)
            CleanUp(meshList, skeletonList, oldmodes, oldposes, oldactive)
        except Exception:
            self.report({'ERROR'}, traceback.format_exc())
            CleanUp(meshList, skeletonList, oldmodes, oldposes, oldactive)
            return {'CANCELLED'}
        if (finalList == 'CANCELLED'):
            return {'CANCELLED'}
            
        # Optimize the data further
        finalList, animList = optimizeData(self, context, finalList, animList)
        
        # Finally, dump all the organized data to a file
        writeFile(self, context, finalList, animList);
        return {'FINISHED'}

    def invoke(self, context, event):
        if not self.filepath:
            fname = bpy.path.display_name_from_filepath(bpy.data.filepath)
            if (fname == ""):
                fname = "Untitled"
            self.filepath = bpy.path.ensure_ext(fname, ".S64")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

# Blender Script registration
def menu_func_export(self, context):
    self.layout.operator(ObjectExport.bl_idname, text="Sausage64 Character (.S64)")

def register():
    bpy.utils.register_class(ObjectExport)
    if (isNewBlender()):
        bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    else:
        bpy.types.INFO_MT_file_export.append(menu_func_export)

def unregister():
    bpy.utils.unregister_class(ObjectExport)
    if (isNewBlender()):
        bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    else:
        bpy.types.INFO_MT_file_export.remove(menu_func_export)

if __name__ == "__main__":
    register()