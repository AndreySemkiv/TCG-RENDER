"""Source-driven original L-card draw preparation.

Shaders are decoded unchanged. Their actual SPIR-V inputs, descriptors and
uniform offsets are reflected per keyword variant, rather than assuming that
Unity's common parameter block describes every compiled program.
"""
from pathlib import Path
import hashlib
import json
import math
import re
import struct
from functools import lru_cache

from .compiler import CompilationError, _absolute, _columns, _inverse
from .assets import _multiply
from .shader_import import extract_shader, write_manifest


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _uniform_member_access(instructions,member_counts,constants):
    """Conservatively prove which declared uniform members are accessed.

    A direct struct access has a constant first index. Whole-block loads, pointer
    copies/calls, dynamic or unknown direct uses conservatively retain every
    member. No branch-value or runtime-pointer assumption removes a field.
    """
    used={variable:set() for variable in member_counts}
    metadata={0,3,4,5,6,7,8,10,11,14,15,16,17,59,71,72,73,74,75,317,330,331,332}
    for op,args in instructions:
        if op in metadata or 19<=op<=52:continue # declarations/constants never read a UBO
        if op in (65,66) and len(args)>=4 and args[2] in used:
            variable=args[2];index=constants.get(args[3])
            if (index is None or len(index)!=1 or not 0<=index[0]<member_counts[variable]):
                used[variable]=None
            elif used[variable] is not None:used[variable].add(index[0])
            continue
        # Any other direct reference may expose a pointer or whole structure.
        for variable in member_counts.keys()&set(args):used[variable]=None
    return {variable:sorted(range(member_counts[variable]) if members is None else members)
            for variable,members in used.items()}


def reflect_spirv(path):
    """Reflect only original executable input/resource declarations and offsets."""
    raw=Path(path).read_bytes()
    if len(raw)%4 or len(raw)<20:
        raise CompilationError('Invalid original SPIR-V framing')
    words=struct.unpack('<%dI'%(len(raw)//4),raw)
    if words[0]!=0x07230203:raise CompilationError('Invalid original SPIR-V magic')
    names,members,decs,member_decs,types,constants,variables={},{},{},{},{},{},[]
    instructions=[]
    def string(value):return struct.pack('<%dI'%len(value),*value).split(b'\0')[0].decode('utf8')
    cursor=5
    while cursor<len(words):
        count,op=words[cursor]>>16,words[cursor]&65535
        if not count or cursor+count>len(words):raise CompilationError('Invalid original SPIR-V instruction')
        a=list(words[cursor+1:cursor+count])
        instructions.append((op,a))
        if op==5:names[a[0]]=string(a[1:])
        elif op==6:members[(a[0],a[1])]=string(a[2:])
        elif op==71:decs.setdefault(a[0],{})[a[1]]=a[2:]
        elif op==72:member_decs.setdefault((a[0],a[1]),{})[a[2]]=a[3:]
        elif op in (20,21,22,23,24,25,26,27,28,29,30,32):types[a[0]]=(op,a[1:])
        elif op in (41,42,43):constants[a[1]]=a[2:] if op==43 else [int(op==41)]
        elif op==59:variables.append({'type':a[0],'id':a[1],'storage':a[2]})
        cursor+=count
    def describe(tid):
        op,args=types[tid]
        if op in (20,21,22):return {'kind':'int' if op in (20,21) else 'float','count':1,'bytes':4}
        if op==23:
            value=describe(args[0]);return dict(value,count=args[1],bytes=value['bytes']*args[1])
        if op==24:
            value=describe(args[0]);return {'kind':'matrix','rows':value['count'],'columns':args[1],'count':value['count']*args[1],'bytes':16*args[1]}
        if op==28:
            length=constants[args[1]][0];value=describe(args[0]);stride=decs.get(tid,{}).get(6,[value['bytes']])[0]
            return {'kind':'array','count':length,'element':value,'stride':stride,'bytes':stride*length}
        if op==27:return {'kind':'sampler','dimension':types[args[0]][1][1]}
        if op==25:return {'kind':'image','dimension':args[1]}
        raise CompilationError('Unrecovered original SPIR-V type '+str((op,args)))
    member_counts={v['id']:len(types[types[v['type']][1][1]][1]) for v in variables if v['storage']==2}
    member_access=_uniform_member_access(instructions,member_counts,constants)
    result={'inputs':[],'uniforms':[],'samplers':[]}
    for var in variables:
        deco=decs.get(var['id'],{});pointer=types[var['type']];tid=pointer[1][1]
        if var['storage']==1 and 30 in deco:
            result['inputs'].append({'name':names.get(var['id'],''),'location':deco[30][0],**describe(tid)})
        if var['storage']==2 and 33 in deco and 34 in deco:
            fields=[]
            for index,field_type in enumerate(types[tid][1]):
                d=member_decs.get((tid,index),{})
                if 35 not in d:raise CompilationError('Original uniform offset missing')
                fields.append({'name':members.get((tid,index),''),'member_index':index,'offset':d[35][0],**describe(field_type)})
            size=max([0]+[f['offset']+f['bytes'] for f in fields])
            result['uniforms'].append({'name':names.get(var['id'],names.get(tid,'')),
                'set':deco[34][0],'binding':deco[33][0],'size':(size+15)//16*16,
                'active_member_indices':member_access[var['id']],'fields':fields})
        if var['storage']==0 and 33 in deco and 34 in deco:
            result['samplers'].append({'name':names.get(var['id'],''),'set':deco[34][0],
                'binding':deco[33][0],**describe(tid)})
    result['inputs'].sort(key=lambda v:v['location'])
    return result


def select_program(parsed, shader_pass, keywords, stage):
    """Select the actual Vulkan program's exact original material keyword set."""
    names=parsed.get('m_KeywordNames',[])
    active=set(keywords or [])
    candidates=[]
    groups=shader_pass[stage].get('m_PlayerSubPrograms',[])
    if stage=='progFragment' and not groups:
        # Unity's Vulkan combined program contains both stages and is indexed
        # only by progVertex in these shipped bundles.
        groups=shader_pass['progVertex'].get('m_PlayerSubPrograms',[])
    for group in groups:
        for program in group:
            if program.get('m_GpuProgramType')!=25:continue
            selected={names[i] for i in program.get('m_KeywordIndices',[])}
            if selected==active:candidates.append(program['m_BlobIndex'])
    if len(set(candidates))!=1:
        raise CompilationError('Original Vulkan material keyword variant is ambiguous/missing: '+parsed['m_Name']+' '+stage+' '+str(sorted(active)))
    return candidates[0]


@lru_cache(maxsize=128)
def _program_metadata(root,relative,program,parameter):
    """Read source-side names/channels retained outside the stripped SPIR-V."""
    import UnityPy,warnings
    from UnityPy.helpers import CompressionHelper
    from UnityPy.streams import EndianBinaryReader
    from UnityPy.export.ShaderConverter import ShaderSubProgram
    UnityPy.config.FALLBACK_UNITY_VERSION='6000.0.78f1'
    with warnings.catch_warnings():
        warnings.simplefilter('ignore');env=UnityPy.load(str(Path(root)/'input/aladin-complete/output'/relative))
    shader=next(o for o in env.objects if o.type.name=='Shader').read()
    blocks=[]
    for platform,offsets,lengths,sizes in zip(shader.platforms,shader.offsets,shader.compressedLengths,shader.decompressedLengths):
        if int(platform)!=18:continue
        if isinstance(offsets,int):offsets,lengths,sizes=[offsets],[lengths],[sizes]
        for offset,length,size in zip(offsets,lengths,sizes):
            blocks.append(CompressionHelper.decompress_lz4(bytes(shader.compressedBlob)[offset:offset+length],size))
    if len(blocks)!=1:raise CompilationError('Original Vulkan metadata requires a unique block')
    block=blocks[0]
    def entry(index):
        start,length,_=struct.unpack_from('<3I',block,4+index*12)
        if start+length>len(block):raise CompilationError('Original parameter entry out of bounds')
        return EndianBinaryReader(block[start:start+length],endian='<')
    reader=entry(program);ShaderSubProgram(reader)
    mask=reader.read_u_int();count=reader.read_int()
    if count<0 or count>16:raise CompilationError('Original vertex channel count out of bounds')
    channels=[(reader.read_int(),reader.read_int()) for _ in range(count)]
    if reader.Position!=reader.Length:raise CompilationError('Unparsed original vertex channel metadata')
    reader=entry(parameter)
    if reader.read_int()!=202012090:raise CompilationError('Unknown original parameter container version')
    buffers=[]
    for _ in range(reader.read_int()):
        name=reader.read_aligned_string();size=reader.read_int();fields=[]
        for _ in range(reader.read_int()):
            fname=reader.read_aligned_string();kind,rows,cols,matrix,array,offset=[reader.read_int() for _ in range(6)]
            fields.append({'name':fname,'offset':offset,'kind':'matrix' if matrix else 'int' if kind==1 else 'float',
                'count':rows*cols,'array':array})
        if reader.read_int()!=0:raise CompilationError('Original shader nested uniform structs not recovered')
        buffers.append({'name':name,'size':size,'fields':fields})
    bindings=[];texture_bindings=[]
    for _ in range(reader.read_int()):
        name=reader.read_aligned_string();kind=reader.read_int();binding=reader.read_int()
        extra=reader.read_int()
        if kind==1:
            if extra!=0:raise CompilationError('Unrecovered original constant buffer binding flag')
            bindings.append({'name':name,'index':binding})
        elif kind==0:
            dimension=reader.read_int()
            texture_bindings.append({'name':name,'index':binding,'sampler':extra,'dimension':dimension})
        else:raise CompilationError('Unrecovered original shader parameter binding type '+str(kind))
    # Additional original parameter sections are not needed here: textures and
    # shared fields are resolved through the serialized common parameter table.
    return {'channels':channels,'channel_mask':mask,'buffers':buffers,'bindings':bindings,'texture_bindings':texture_bindings}


def _restore_names(reflections,root,shader,parsed,shader_pass,keywords):
    names={index:name for name,index in shader_pass['m_NameIndices']}
    data=shader_pass['progVertex'];program=select_program(parsed,shader_pass,keywords,'progVertex')
    groups=data['m_PlayerSubPrograms'];params=data['m_ParameterBlobIndices']
    matches=[(group_index,i) for group_index,group in enumerate(groups) for i,p in enumerate(group)
             if p.get('m_GpuProgramType')==25 and p['m_BlobIndex']==program]
    if len(matches)!=1:raise CompilationError('Ambiguous original parameter-to-program association')
    group,index=matches[0];parameter=params[group][index]
    metadata=_program_metadata(str(root),shader['source']['bundle'].replace('\\','/'),program,parameter)
    semantics={0:'POSITION',1:'NORMAL',2:'TANGENT',3:'COLOR',**{i:'TEXCOORD'+str(i-4) for i in range(4,12)}}
    # Unity's serialized target enums start at ShaderChannel.Vertex (13).
    for item in reflections['vertex']['inputs']:
        source=next((s for s,t in metadata['channels'] if t-13==item['location']),None)
        if source not in semantics:raise CompilationError('Unresolved original vertex semantic at location '+str(item['location']))
        item['name']=semantics[source]
    common=data['m_CommonParameters']
    blocks={}
    for cb in common['m_ConstantBuffers']:
        binding=next(b for b in common['m_ConstantBufferBindings'] if b['m_NameIndex']==cb['m_NameIndex'])
        fields=[]
        for f in cb['m_MatrixParams']+cb['m_VectorParams']:
            matrix=f in cb['m_MatrixParams']
            fields.append({'name':names[f['m_NameIndex']],'offset':f['m_Index'],
                'kind':'matrix' if matrix else 'int' if f.get('m_Type')==1 else 'float',
                'count':f.get('m_RowCount',4)*4 if matrix else f['m_Dim']})
        blocks[binding['m_Index']]={'name':names[cb['m_NameIndex']],'fields':fields}
    for cb in metadata['buffers']:
        binding=next((b for b in metadata['bindings'] if b['name']==cb['name']),None)
        if not binding:continue
        target=blocks.setdefault(binding['index'],{'name':cb['name'],'fields':[]})
        target['fields']+=cb['fields']
    for stage,reflection in reflections.items():
        stageflag=4 if stage=='vertex' else 8
        for cb in reflection['uniforms']:
            bound=blocks.get((stageflag<<24)|(cb['set']<<16)|cb['binding'])
            if not bound:raise CompilationError('Original uniform block metadata missing: '+shader['name']+' '+stage+' '+str((cb['set'],cb['binding']))+' source='+str(list(blocks)))
            cb['name']=bound['name']
            active=cb.get('active_member_indices',list(range(len(cb['fields']))))
            retained=[]
            for member_index,field in enumerate(cb['fields']):
                source=next((f for f in bound['fields'] if f['offset']==field['offset']),None)
                if field.get('member_index',member_index) not in active:
                    cb.setdefault('inactive_declared_members',[]).append({
                        'member_index':field.get('member_index',member_index),'offset':field['offset'],
                        'bytes':field['bytes'],'source_name':source['name'] if source else None,
                        'reason':'Declared SPIR-V member has no executable uniform-pointer access; allocation size/padding preserved'})
                    continue
                if not source:raise CompilationError('Original uniform member metadata missing at '+str(field['offset']))
                if field['kind']=='array' and source['kind']!='matrix':
                    # Keep the actual SPIR-V array extent and ArrayStride.
                    # Source metadata gives element width/type, not the full
                    # array count. Matrices are reflected as vec4 arrays too.
                    field.update(name=source['name'])
                    if source['count']!=field['element']['count'] or source['kind']!=field['element']['kind']:
                        raise CompilationError('Original uniform array element metadata differs: '+source['name'])
                else:field.update(name=source['name'],kind=source['kind'],count=source['count'])
                retained.append(field)
            cb['fields']=retained
        for sampler in reflection['samplers']:
            binding_index=(stageflag<<24)|(sampler['set']<<16)|sampler['binding']
            bound=next((p for p in common['m_TextureParams'] if p['m_Index']==binding_index),None)
            local=next((p for p in metadata['texture_bindings'] if p['index']==binding_index),None)
            if bound:sampler['name']=names[bound['m_NameIndex']]
            elif local:sampler['name']=local['name']
            else:raise CompilationError('Original sampler metadata missing')
    return metadata


def _stages(root, shader, parsed, shader_pass, keywords):
    relative=shader['source']['bundle'].replace('\\','/')
    source=root/'input/aladin-complete/output'/relative
    digest=_sha(source)
    if digest!=shader['source']['bundle_sha256']:
        raise CompilationError('Original shader source changed after scene export: '+relative)
    out=root/'render-cache/general-shaders'/digest
    manifest=out/'decode-manifest.json'
    records=None
    if manifest.is_file():
        cached=_read(manifest).get('decoded_programs',[])
        if cached and all((root/r['source_smolv']).is_file() and _sha(root/r['source_smolv'])==r['source_sha256']
            and (root/r['spirv_file']).is_file() and _sha(root/r['spirv_file'])==r['spirv_sha256']
            and r.get('source_bundle_sha256')==digest for r in cached):records=cached
    if records is None:
        records=extract_shader(root,relative,out/'raw',out/'decoded',prefix=shader['id'])
        write_manifest(manifest,records)
    stages={};used=[]
    for stage,key,model in [('progVertex','vertex',0),('progFragment','fragment',4)]:
        program=select_program(parsed,shader_pass,keywords,stage)
        selected=[r for r in records if re.search(r'\.program%d\.smolv'%program,r['spirv_file'])
                  and r['entry_points'][0]['execution_model']==model]
        if len(selected)!=1:raise CompilationError('Missing original SPIR-V stage for '+shader['name']+' program'+str(program))
        stages[key]=str(root/selected[0]['spirv_file']);used.append(selected[0])
    return stages,used


_COMPARE={0:'always',1:'never',2:'less',3:'equal',4:'less_or_equal',5:'greater',6:'not_equal',7:'greater_or_equal',8:'always'}
_STENCIL={0:'keep',1:'zero',2:'replace',3:'increment_and_clamp',4:'decrement_and_clamp',5:'invert',6:'increment_and_wrap',7:'decrement_and_wrap'}
_BLEND={0:'zero',1:'one',2:'dst_color',3:'src_color',4:'one_minus_dst_color',5:'src_alpha',6:'one_minus_src_color',7:'dst_alpha',8:'one_minus_dst_alpha',9:'src_alpha_saturate',10:'one_minus_src_alpha'}
_BLEND_OP={0:'add',1:'subtract',2:'reverse_subtract',3:'min',4:'max'}


def original_state(state,material):
    def value(item):
        name=item.get('name','')
        if name in material.get('floats',{}):return material['floats'][name]
        if name in material.get('ints',{}):return material['ints'][name]
        return item['val']
    def stencil(item):
        return {'compare':_COMPARE[int(value(item['comp']))],'fail':_STENCIL[int(value(item['fail']))],
                'pass':_STENCIL[int(value(item['pass']))],'depth_fail':_STENCIL[int(value(item['zFail']))]}
    primary=stencil(state['stencilOp']);front=stencil(state['stencilOpFront']);back=stencil(state['stencilOpBack'])
    default={'compare':'always','fail':'keep','pass':'keep','depth_fail':'keep'}
    if front==default:front=primary
    if back==default:back=primary
    blend=state['rtBlend0'];mask=int(value(blend['colMask']))
    return {'cull':{0:'none',1:'front',2:'back'}[int(value(state['culling']))],'front_face':'clockwise',
        'depth_test':int(value(state['zTest']))!=0,'depth_write':bool(value(state['zWrite'])),
        'depth_compare':_COMPARE[int(value(state['zTest']))],
        'color_write_mask':((mask&8)>>3)|((mask&4)>>1)|((mask&2)<<1)|((mask&1)<<3),
        'depth_bias':{'constant':value(state['offsetUnits']),'slope':value(state['offsetFactor']),'clamp':0},
        'stencil':{'enabled':True,'read_mask':int(value(state['stencilReadMask'])),'write_mask':int(value(state['stencilWriteMask'])),
            'reference':int(value(state['stencilRef'])),'front':front,'back':back},
        'blend':{'enabled':True,'src_color':_BLEND[int(value(blend['srcBlend']))],
            'dst_color':_BLEND[int(value(blend['destBlend']))],'src_alpha':_BLEND[int(value(blend['srcBlendAlpha']))],
            'dst_alpha':_BLEND[int(value(blend['destBlendAlpha']))],
            'color_op':_BLEND_OP[int(value(blend['blendOp']))],'alpha_op':_BLEND_OP[int(value(blend['blendOpAlpha']))]}}


def _queue(material,parsed,shader_pass):
    if material.get('custom_render_queue',-1)>=0:return material['custom_render_queue']
    tags=dict(parsed['m_SubShaders'][0].get('m_Tags',{}).get('tags',[]))
    tags.update(dict(shader_pass['m_State'].get('m_Tags',{}).get('tags',[])))
    value=tags.get('QUEUE','Geometry')
    match=re.fullmatch(r'(Background|Geometry|AlphaTest|Transparent|Overlay)([+-]\d+)?',value)
    if not match:raise CompilationError('Unrecovered original render queue '+value)
    return {'Background':1000,'Geometry':2000,'AlphaTest':2450,'Transparent':3000,'Overlay':4000}[match[1]]+int(match[2] or 0)


def _vertex_buffers(mesh,reflection,out,prefix,slot,native_source_root=None):
    rows=[];attrs=[];stride=0
    aliases={'POSITION':('vertices',[0,0,0,1]),'NORMAL':('normals',[0,0,1]),'TANGENT':('tangents',[1,0,0,1]),'COLOR':('colors',[1,1,1,1])}
    channels=[];channel_defaults=[]
    for item in reflection['inputs']:
        name=item['name'];match=re.search(r'(POSITION|NORMAL|TANGENT|COLOR|TEXCOORD)(\d*)',name,re.I)
        if not match:raise CompilationError('Unrecovered original vertex input '+name)
        semantic=match[1].upper();channel=int(match[2] or 0)
        key,default=('uv'+str(channel),[0,0,0,1]) if semantic=='TEXCOORD' else aliases[semantic]
        values=mesh.get(key)
        # Unity resolves missing UV descriptors before binding its default
        # stream; an earlier source UV can be reused even if the shader does
        # not request that earlier semantic. COLOR keeps its source default.
        if not values:
            if semantic=='NORMAL':
                from .vertex_defaults import normal_default
                try:default,source=normal_default(native_source_root)
                except ValueError as exc:raise CompilationError(str(exc)) from exc
                channel_defaults.append(dict(source,location=item['location'],components=item['count']))
                values=[default]*len(mesh['vertices'])
            elif semantic=='TEXCOORD':
                from .vertex_defaults import uv_default
                try:values,default,source=uv_default(native_source_root,mesh,channel)
                except ValueError as exc:raise CompilationError(str(exc)) from exc
                channel_defaults.append(dict(source,location=item['location'],components=item['count']))
            elif semantic=='COLOR':values=[default]*len(mesh['vertices'])
            else:raise CompilationError('Original mesh channel missing: '+key)
        count=item['count'];channels.append((values,default,count));attrs.append({'location':item['location'],'format':'float'+str(count),'offset':stride})
        stride+=count*4
    for index in range(len(mesh['vertices'])):
        for values,default,count in channels:
            value=list(values[index]);rows.extend((value+default[len(value):])[:count])
    vfile=out/(prefix+'.vertices.bin');ifile=out/(prefix+'.indices.bin')
    vfile.write_bytes(struct.pack('<%df'%len(rows),*rows))
    indices=[i for tri in mesh['submeshes'][slot]['triangles'] for i in tri]
    ifile.write_bytes(struct.pack('<%dI'%len(indices),*indices))
    return {'file':str(vfile),'stride':stride,'attributes':attrs,'channel_defaults':channel_defaults},{'file':str(ifile)}


def _defaults(parsed):
    result={}
    for prop in parsed.get('m_PropInfo',{}).get('m_Props',[]):
        value=prop.get('m_DefValue')
        if value is None and 'm_DefValue[0]' in prop:
            values=[prop['m_DefValue[%d]'%i] for i in range(4)]
            value=values if prop['m_Type'] in (0,1) else values[0]
        if value is not None:result[prop['m_Name']]=value
    return result


def _uniform_value(name,material,defaults,builtins,field,property_block=None,camera_depth_context=None):
    # HLSLCC's reflected matrix name retains its source identifier after prefix.
    original=name
    name=re.sub(r'^hlslcc_mtx\d+x\d+','',name)
    if name=='_ZBufferParams':
        return _depth_builtin(builtins,camera_depth_context,property_block)
    if property_block and name in property_block:
        return property_block[name],'original component initialization property block at the selected initial frame'
    if name in builtins:
        from .shader_clock import CLOCK_VARIABLES
        if name in CLOCK_VARIABLES:
            return builtins[name],'Unity builtin clock formula; explicitly selected static offscreen frame t=0 seconds, not captured game time'
        return builtins[name],'original transform / explicit frontal harness camera'
    if name in material.get('floats',{}):return material['floats'][name],'original saved material float'
    if name in material.get('ints',{}):return material['ints'][name],'original saved material integer'
    if name in material.get('colors',{}):return [material['colors'][name][c] for c in 'rgba'],'original saved material vector'
    if name.endswith('_ST'):
        texture=next((v for v in material['textures'] if v['property']==name[:-3]),None)
        if texture:return texture['scale']+texture['offset'],'original material texture scale/offset'
    if name in defaults:return defaults[name],'original shader property default'
    # Preserve every supplied value above. The parent CardBehaviour supplies
    # _Rotation through UpdateHologramRotation from the externally supplied
    # CardDataGroup.HologramRotation, absent from these serialized prefabs.
    # Its zero here is an explicit neutral frontal harness choice, not proof
    # that a controller/caller never writes it. Relief Effect's unassigned
    # ShadowUVTranslate uses the numeric initial-state rule below instead.
    # Unity supplies numeric zero only after MPB/material/global lookup fails:
    # https://docs.unity3d.com/6000.0/Documentation/Manual/writing-shader-material-properties.html
    # This is the declared fresh offscreen harness with empty custom globals,
    # not a capture of the running game's global shader-property state.
    # Shadow's native SetVector is consumed as float2 in the relief Effect
    # shader (original metadata offset88) and as float4 in other programs.
    initial_width=field.get('count')
    allowed_widths={'_Rotation':(3,),'_ShadowUVTranslate':(2,4)}.get(name,())
    if (original==name and field.get('kind')=='float' and initial_width in allowed_widths):
        if name=='_Rotation':
            return [0.0]*initial_width,('Explicit neutral frontal offscreen harness HologramRotation=(0,0,0); '
                'original parent producer CardBehaviour.UpdateHologramRotation writes a renderer MaterialPropertyBlock '
                'from the external CardDataGroup.HologramRotation option; caller/runtime values and running-game globals not captured; '
                'existing MPB/material/shader-default/explicit harness values preserved; native evidence: '
                'reports/additional-scene-families-20261022/rotation-literal-references.json; '
                'numeric initial-state reference: https://docs.unity3d.com/6000.0/Documentation/Manual/writing-shader-material-properties.html')
        return [0.0]*initial_width,('Unity unassigned numeric initial default after MPB/material/shader-default/global lookup; '
            'explicit fresh frontal offscreen harness with empty custom globals; running-game globals not captured; '
            'reference: https://docs.unity3d.com/6000.0/Documentation/Manual/writing-shader-material-properties.html')
    if (original==name=='_NoiseMaskNoiseSpeed' and field.get('kind')=='float'
            and field.get('count')==1 and '_NoiseMaskSpeed' in defaults):
        # The shipped ShadowBox Effect Flow program reads this distinct ID,
        # while ShaderLab/SavedProperties expose _NoiseMaskSpeed. Do not alias
        # its material value. This is the explicit empty-global initial harness,
        # not evidence that every possible game controller leaves it unassigned.
        return 0.0,('Unity unassigned numeric initial default in the explicit fresh offscreen harness with empty custom globals; '
            'compiled _NoiseMaskNoiseSpeed is distinct from source ShaderLab/material _NoiseMaskSpeed and is not aliased; '
            'running-game property writers/globals not captured; existing assigned values preserved; source evidence: '
            'reports/msr-stage-metadata-20261022/noise-speed-source-typo.json; '
            'reference: https://docs.unity3d.com/6000.0/Documentation/Manual/writing-shader-material-properties.html')
    if name=='_CutOff' and name not in defaults:
        # This shipped shader uses _CutOff, while its Properties declare
        # _CutOut. Keep the distinct IDs: an unassigned numeric GPU property
        # starts at zero; do not alias it to the similarly spelled _Cutoff.
        return 0,'unassigned numeric shader uniform; Unity zero initial state (no runtime override reproduced)'
    raise CompilationError('Unresolved original shader uniform '+original+' in '+material['name'])


def frontal_depth_params(near=None,far=None):
    """Unity's normal-depth builtin formula, independent of a material offset."""
    import math
    if near is None and far is None:
        distance=1.9115060567855835
        near,far=distance-.5,distance+.5
    try:
        near,far=float(near),float(far)
    except (ValueError,TypeError,OverflowError) as error:
        raise CompilationError('Frontal depth clipping range must be finite with 0 < near < far') from error
    if not math.isfinite(near) or not math.isfinite(far) or not 0<near<far:
        raise CompilationError('Frontal depth clipping range must be finite with 0 < near < far')
    params=[1-far/near,far/near,(1-far/near)/far,1/near]
    if not all(math.isfinite(value) for value in params) or params[2]==0:
        raise CompilationError('Frontal depth coefficients are not representable')
    return params


def frontal_depth_context(vp,context=None):
    """Validate the existing static orthographic harness, preserving its XY fit.

    These near/far planes describe the renderer's chosen camera, not a recovered
    game pose. Card_Effect's reciprocal math has no ortho correction; preserve
    that source arithmetic without claiming physical displacement in game metres.
    """
    import math
    try:
        finite=(len(vp)==4 and all(len(row)==4 for row in vp)
                and all(math.isfinite(value) for row in vp for value in row))
    except (TypeError,ValueError):
        finite=False
    if not finite or list(vp[2])!=[0,0,1,.5] or list(vp[3])!=[0,0,0,1]:
        raise CompilationError('Depth builtin requires the declared non-reversed frontal projection')
    distance=1.9115060567855835
    near,far=distance-.5,distance+.5
    expected={'schema':1,'authority':'explicit static offscreen harness; original game camera not captured',
        'projection':'orthographic','reversed_z':False,'camera_world_position':[0,0,-distance],
        'near':near,'far':far,'clip_depth_range':[0,1],
        'vp_depth_row':[0,0,1,.5],'vp_w_row':[0,0,0,1],
        'z_buffer_params':frontal_depth_params(near,far),
        'reference':'https://docs.unity3d.com/6000.0/Documentation/Manual/SL-UnityShaderVariables.html',
        'limits':'Original reciprocal LinearEyeDepth arithmetic executes without an orthographic correction; nonzero material offsets are not certified game-metre displacements.'}
    if context is not None:
        if not isinstance(context,dict):
            raise CompilationError('Depth builtin requires a validated camera context')
        # Check the range independently so invalid values receive a clear error.
        frontal_depth_params(context.get('near'),context.get('far'))
        for key in ('schema','projection','camera_world_position','near','far','clip_depth_range',
                    'vp_depth_row','vp_w_row','z_buffer_params'):
            if context.get(key)!=expected[key]:
                raise CompilationError('Depth camera context is incoherent with the declared frontal projection: '+key)
        if context.get('reversed_z') is not False:
            raise CompilationError('Depth camera context is incoherent with the declared non-reversed projection')
    return expected


def _depth_builtin(builtins,context,property_block=None):
    """Keep camera globals independent of saved material/property-block values."""
    if context is None:
        raise CompilationError('Depth builtin requires a validated camera context')
    verified=frontal_depth_context(builtins.get('unity_MatrixVP'),context)
    params=verified['z_buffer_params']
    if (property_block and '_ZBufferParams' in property_block
            or builtins.get('_ZBufferParams')!=params
            or builtins.get('_WorldSpaceCameraPos')!=verified['camera_world_position']):
        raise CompilationError('Depth builtin override is incoherent with the validated camera context')
    return params,'Unity builtin near/far formula for the explicit frontal-camera context; original shader and saved depth offset unchanged'

def _pack_uniform(raw,field,value):
    """Pack reflected scalar/vector/matrix/arrays without flattening stride."""
    if isinstance(value,dict):value=[value.get(k,0) for k in 'rgba']
    if field['kind']=='array':
        element=field['element']
        if element['kind']=='float' and element['count']==4:
            if not isinstance(value,(list,tuple)) or len(value)!=field['count']:
                raise CompilationError('Original vector array extent differs '+field['name'])
            for index,item in enumerate(value):
                if not isinstance(item,(list,tuple)) or len(item)!=4:
                    raise CompilationError('Original vector array width differs '+field['name'])
                struct.pack_into('<4f',raw,field['offset']+index*field['stride'],*item)
            return [list(v) for v in value]
        if element['kind'] not in ('float','int') or element['count']!=1:
            raise CompilationError('Unrecovered original uniform array element '+field['name'])
        if not isinstance(value,(list,tuple)) or len(value)>field['count']:
            raise CompilationError('Original uniform array value/extent differs '+field['name'])
        values=list(value);fmt='i' if element['kind']=='int' else 'f'
        # libunity shader-property conversion 0x6b97c8→0x6c37b0 uses
        # fcvtzs0x6c3814 for float properties feeding signed int uniforms.
        # Unwritten tail remains padding/initial zero; no source element is
        # silently discarded, and the original material deletes unused prims.
        for index,item in enumerate(values):
            if fmt=='i':
                if not -2147483648<=item<2147483648:raise CompilationError('Original int array value outside recovered range')
                item=int(item)
            struct.pack_into('<'+fmt,raw,field['offset']+index*field['stride'],item)
        return values
    if isinstance(value,(list,tuple)) and value and isinstance(value[0],(list,tuple)):value=_columns(value)
    if not isinstance(value,(list,tuple)):value=[value]
    value=list(value)[:field['count']];fmt='i' if field['kind']=='int' else 'f'
    if fmt=='i':value=[int(v) for v in value]
    struct.pack_into('<%d%s'%(len(value),fmt),raw,field['offset'],*value)
    return value


def _same_initial_float_value(left,right):
    """SetFloat/SetVector writes commute only when every float32 bit agrees."""
    if isinstance(left,(list,tuple)) or isinstance(right,(list,tuple)):
        return (isinstance(left,(list,tuple)) and isinstance(right,(list,tuple))
                and len(left)==len(right) and all(_same_initial_float_value(a,b) for a,b in zip(left,right)))
    return (isinstance(left,(int,float)) and isinstance(right,(int,float))
            and math.isfinite(left) and math.isfinite(right)
            and struct.pack('<f',left)==struct.pack('<f',right))


def prepare_scene_draws(root,scene_dir,scene,meshes,materials,out,vp,raw_ui=None,exclude_dynamic=True,harness_uniforms=None,raw_ui_targets=None,camera_depth_context=None):
    """Prepare all original visible L scene passes, including stencil/rarity.

    Return (draws, audit, records). Dynamic text may be supplied as the future
    raw_ui render target, or excluded while the caller prepares that pass.
    """
    root,scene_dir,out=Path(root).resolve(),Path(scene_dir).resolve(),Path(out).resolve()
    out.mkdir(parents=True,exist_ok=True)
    depth_context=frontal_depth_context(vp,camera_depth_context)
    if harness_uniforms and '_ZBufferParams' in harness_uniforms:
        if camera_depth_context is None or harness_uniforms['_ZBufferParams']!=depth_context['z_buffer_params']:
            raise CompilationError('Depth builtin override requires a coherent explicit camera context')
    textures={v['id']:v for v in scene['textures']};cubes={v['id']:v for v in scene.get('cubemaps',[])}
    shaders={v['id']:v for v in scene['shaders']};draws=[];audit=[];used_records={}
    from .mrr_initialization import renderer_overrides
    initial_blocks,initial_audit=renderer_overrides(root,scene_dir,scene,materials)
    from .uv_shadow_initialization import renderer_overrides as shadow_overrides
    from .mega_alt_initialization import renderer_overrides as mega_overrides
    for initializer in (shadow_overrides,mega_overrides):
        extra,details=initializer(root,scene_dir,scene,materials)
        for oid,values in extra.items():
            existing=initial_blocks.setdefault(oid,{})
            overlap=existing.keys()&values.keys()
            if any(not _same_initial_float_value(existing[k],values[k]) for k in overlap):
                raise CompilationError('Differing overlapping source initializers need original execution order: '
                    +oid+' '+str(sorted(overlap)))
            if overlap:
                for detail in details:
                    if detail['object']==oid:detail['coincident_float32_writes']=sorted(overlap)
            existing.update(values)
        initial_audit.extend(details)
    initial_sources={}
    for item in initial_audit:
        oid=item['object']
        if oid not in initial_sources:initial_sources[oid]=item
        else:
            prior=initial_sources[oid]
            initial_sources[oid]={'object':oid,'components':prior.get('components',[prior])+[item],
                'property_block':initial_blocks[oid],
                'scope':'Disjoint or bit-identical float32 source writes; differing overlaps are refused'}
    from .shiny_initialization import scene_initialization
    try:shiny_blocks,shiny_meshes,shiny_audit=scene_initialization(root,scene,meshes,materials)
    except ValueError as error:raise CompilationError(str(error)) from error
    shiny_sources={(v['object'],v['slot']):v for v in shiny_audit}
    from .flow_initialization import renderer_overrides as flow_overrides
    flow_blocks,flow_audit=flow_overrides(root,scene)
    for key,values in flow_blocks.items():
        if key in shiny_blocks:raise CompilationError('Overlapping original flow/shiny components need execution order')
        shiny_blocks[key]=values
    shiny_sources.update({(v['object'],v['slot']):v for v in flow_audit})
    from .marble_initialization import renderer_overrides as marble_overrides
    marble_blocks,marble_textures,marble_audit=marble_overrides(root,scene,out/'runtime-textures')
    for key,values in marble_blocks.items():
        if key in shiny_blocks:raise CompilationError('Overlapping original Marble/other slot initializers need execution order')
        shiny_blocks[key]=values
    shiny_sources.update({(v['object'],v['slot']):v for v in marble_audit})
    from .ancient_future_initialization import renderer_overrides as ancient_future_overrides
    category_blocks,category_audit=ancient_future_overrides(root,scene_dir,scene,materials)
    for key,values in category_blocks.items():
        if key in shiny_blocks:raise CompilationError('Overlapping original Ancient/Future slot initializers need execution order')
        shiny_blocks[key]=values
    shiny_sources.update({(v['object'],v['slot']):v for v in category_audit})
    camera=depth_context['camera_world_position']
    view=[[1,0,0,0],[0,1,0,0],[0,0,-1,camera[2]],[0,0,0,1]]
    for obj in scene['objects']:
        renderer=obj.get('renderer')
        if not renderer or not obj.get('active_in_hierarchy',True) or not renderer.get('enabled',True):continue
        mesh=shiny_meshes.get(obj['id'],meshes[renderer['mesh']])
        for slot,mid in enumerate(renderer['material_slots']):
            if slot>=len(mesh['submeshes']):
                slot=len(mesh['submeshes'])-1
            material=materials[mid];shader=shaders[material['shader_id']]
            property_block=dict(initial_blocks.get(obj['id'],{}))
            shiny_block=shiny_blocks.get((obj['id'],slot),{})
            if property_block.keys()&shiny_block.keys():raise CompilationError('Overlapping original component property blocks need execution order')
            property_block.update(shiny_block)
            if exclude_dynamic and shader['name']=='Lettuce/Common/CardNew/Text':continue
            # Side/back are real submeshes with opposite facing; preserve them.
            parsed=_read(_absolute(shader['parsed'],scene_dir));defaults=_defaults(parsed)
            builtins={'unity_ObjectToWorld':obj['world_matrix'],'unity_WorldToObject':_inverse(obj['world_matrix']),
                'unity_MatrixVP':vp,'unity_MatrixV':view,'unity_MatrixMV':_multiply(view,obj['world_matrix']),
                'unity_MatrixMVP':_multiply(vp,obj['world_matrix']),'_WorldSpaceCameraPos':camera,
                '_ZBufferParams':depth_context['z_buffer_params']}
            # P*V=VP for the declared harness. Projection alone is not VP when
            # the original shader has already transformed its vertex to view.
            builtins['glstate_matrix_projection']=_multiply(vp,_inverse(view))
            from .shader_clock import shader_clock,CLOCK_VARIABLES
            builtins.update(shader_clock())
            if harness_uniforms:
                if CLOCK_VARIABLES.intersection(harness_uniforms):
                    raise CompilationError('Static shader clock overrides require a coherent declared frame context')
                builtins.update(harness_uniforms)
            for pass_index,shader_pass in enumerate(parsed['m_SubShaders'][0]['m_Passes']):
                stages,records=_stages(root,shader,parsed,shader_pass,material.get('shader_keywords',[]))
                used_records.update({r['spirv_file']:r for r in records})
                reflections={stage:reflect_spirv(path) for stage,path in stages.items()}
                _restore_names(reflections,root,shader,parsed,shader_pass,material.get('shader_keywords',[]))
                prefix='general-'+re.sub(r'[^A-Za-z0-9_.-]','_',obj['id'])+'-'+str(slot)+'-'+str(pass_index)
                vertices,indices=_vertex_buffers(mesh,reflections['vertex'],out,prefix,slot,native_source_root=root)
                bindings=[];uniform_audit=[]
                for stage,reflection in reflections.items():
                    for block in reflection['uniforms']:
                        raw=bytearray(block['size'])
                        for field in block['fields']:
                            value,source=_uniform_value(field['name'],material,defaults,builtins,field,property_block,depth_context)
                            property_name=re.sub(r'^hlslcc_mtx\d+x\d+','',field['name'])
                            if property_name in initial_blocks.get(obj['id'],{}):
                                source='original source component initialization property block at declared frontal initial frame'
                            value=_pack_uniform(raw,field,value)
                            uniform_audit.append({'stage':stage,'block':block['name'],'name':field['name'],'offset':field['offset'],'values':value,'source':source})
                        file=out/(prefix+'.'+stage+'.'+str(block['set'])+'.'+str(block['binding'])+'.bin');file.write_bytes(raw)
                        bindings.append({'set':block['set'],'binding':block['binding'],'type':'uniform_buffer','file':str(file),'stages':[stage]})
                    for sample in reflection['samplers']:
                        name=sample['name'];texture_binding=next((v for v in material['textures'] if v['property']==name),None)
                        runtime_texture=marble_textures.get((obj['id'],slot),{}).get(name)
                        if runtime_texture is not None:
                            bindings.append(dict(runtime_texture,set=sample['set'],binding=sample['binding'],stages=[stage]))
                            uniform_audit.append({'name':name,'source':'Original component-generated runtime texture; source curve samples and native R16 conversion',
                                'texture_binding':runtime_texture})
                            continue
                        if name=='_DynamicUITex' and (raw_ui_targets or raw_ui):
                            if raw_ui_targets is not None:
                                from .dynamic_ui import renderer_dynamic_type
                                target=renderer_dynamic_type(obj)
                                if target not in raw_ui_targets:
                                    raise CompilationError('Original renderer DynamicUI target has no render texture: '+obj['name']+' '+str(target))
                                file=str(raw_ui_targets[target])
                            else:file=str(raw_ui)
                            kind='combined_image_sampler';sampler='linear';address='clamp-to-edge'
                        elif texture_binding and texture_binding['texture_id'] in textures:
                            tex=textures[texture_binding['texture_id']];file=str(_absolute(tex['png'],scene_dir));kind='combined_image_sampler'
                            settings=tex.get('unity_settings',{});sampler='nearest' if settings.get('filter_mode')==0 else 'linear';address='clamp-to-edge' if settings.get('wrap_u')==1 else 'repeat'
                        elif texture_binding and texture_binding['texture_id'] in cubes:
                            cube=cubes[texture_binding['texture_id']];file=out/(prefix+'.'+name+'.cube.json')
                            file.write_text(json.dumps({'faces':[str(_absolute(f['png'],scene_dir)) for f in sorted(cube['faces'],key=lambda v:v['index'])]}),encoding='utf8')
                            file=str(file);kind='combined_image_sampler_cube';sampler='linear';address='clamp-to-edge'
                        elif texture_binding and texture_binding['texture_id'] is None:
                            prop=next((p for p in parsed['m_PropInfo']['m_Props'] if p['m_Name']==name),None)
                            if not prop:raise CompilationError('Unbound original texture has no declared default: '+name)
                            default=prop['m_DefTexture']['m_DefaultName'];dimension=prop['m_DefTexture']['m_TexDim']
                            # Unity 6 ShaderLab defines an empty Texture2D
                            # default as gray too, just as an empty Cubemap.
                            # This is the declared engine default, not a
                            # recovered runtime texture override.
                            if dimension in (2,4) and default=='':default='gray'
                            from .engine_texture_defaults import default_texture_color
                            try:color=default_texture_color(root,default)
                            except (KeyError,ValueError,OSError) as error:
                                raise CompilationError('Unsupported original texture default '+repr(default)+' for '+name+': '+str(error)) from error
                            from PIL import Image
                            default_file=out/('builtin-'+default+'-'+''.join('%02x'%v for v in color)+'.png')
                            if not default_file.is_file():Image.new('RGBA',(1,1),color).save(default_file)
                            if dimension==4:
                                file=out/(prefix+'.'+name+'.default-cube.json')
                                file.write_text(json.dumps({'faces':[str(default_file)]*6}),encoding='utf8');kind='combined_image_sampler_cube'
                            elif dimension==2:file=default_file;kind='combined_image_sampler'
                            else:raise CompilationError('Unsupported original texture default dimension')
                            file=str(file);sampler='linear';address='clamp-to-edge'
                            uniform_audit.append({'name':name,'source':'Original shader property built-in default; absent saved texture PPtr',
                                'default':default,'reference':'https://docs.unity3d.com/6000.0/Documentation/Manual/SL-Properties.html',
                                'default_rgba':list(color),
                                'native_black_authority':'libunity.so 0x5d8b2c writes four zero bytes; source identity validated' if default=='black' else None,
                                'runtime_overrides_reproduced':False})
                        else:raise CompilationError('Original shader texture binding unresolved: '+name+' in '+material['name'])
                        bindings.append({'set':sample['set'],'binding':sample['binding'],'type':kind,'file':file,'stages':[stage],'sampler':sampler,'address_mode':address})
                queue=_queue(material,parsed,shader_pass)
                from .shader_clock import CLOCK_VARIABLES,clock_provenance
                draw={'name':obj['name']+' / '+material['name'],'queue':queue,'vertices':vertices,'indices':indices,
                    'shaders':stages,'bindings':bindings,'state':original_state(shader_pass['m_State'],material),
                    'provenance':{'original_object':obj,'material_id':mid,'original_shader':shader,'submesh_slot':slot,
                        'shader_pass':pass_index,'keywords':material.get('shader_keywords',[]),'uniform_audit':uniform_audit,
                        'vertex_default_channels':vertices.get('channel_defaults',[]),
                        'runtime_initialization':shiny_sources.get((obj['id'],slot),initial_sources.get(obj['id'])),
                        'shader_clock':clock_provenance() if any(u.get('name') in CLOCK_VARIABLES for u in uniform_audit) else None,
                        'camera_depth_context':depth_context if any(u.get('name')=='_ZBufferParams' for u in uniform_audit) else None,
                        'inactive_uniform_members':[dict(stage=stage,block=block['name'],set=block['set'],binding=block['binding'],members=block['inactive_declared_members'])
                            for stage,reflection in reflections.items() for block in reflection['uniforms'] if block.get('inactive_declared_members')],
                        'reflection':'Actual original SPIR-V selected by exact material keyword set; no shader rewriting',
                        'source_rasters_resized':False},'_sort':(queue,renderer.get('sorting_order',0),-obj['world_matrix'][2][3])}
                draws.append(draw);audit.append({'name':draw['name'],'shader':shader['name'],'queue':queue,'uniforms':uniform_audit})
    draws.sort(key=lambda v:v['_sort'])
    for draw in draws:draw.pop('_sort')
    return draws,audit,list(used_records.values())
