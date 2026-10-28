"""Original Unity missing-NORMAL stream for the inspected native profile.

MeshVertexFormatManager C5F700 assigns semantic1 offset8, format2/UNorm8,
dimension4. C5FDF8 binds default buffer0, created by CBC7D8 from native
constant190C30; bytes at offset8 are 00 00 FF 00. Vulkan table1F61BC=37
maps this descriptor to VK_FORMAT_R8G8B8A8_UNORM. Thus NORMAL.xyz=(0,0,1).
Evidence: reports/vertex-default-normal-20261022/evidence.json.
"""
from pathlib import Path
from .unity_contracts import validate_engine_contract


def normal_default(root):
    if root is None:raise ValueError('Original NORMAL default requires a verified native source profile')
    root=Path(root).resolve();engine=validate_engine_contract(root)
    return [0.0,0.0,1.0,0.0],{
        'semantic':'NORMAL','source':'Original Unity default vertex stream; absent source mesh channel',
        'native_profile':{'libunity.so':engine['player_sha256']},
        'native_contract':engine['contract'],
        'default_buffer':0,'offset':8,'original_format':'UNorm8','original_dimension':4,
        'original_bytes_hex':'0000ff00','values':[0.0,0.0,1.0,0.0],
        'mesh_normals_recalculated':False,
        'evidence':'reports/vertex-default-normal-20261022/evidence.json'}


def uv_default(root,mesh,channel):
    """Native C5F700 scans every earlier mesh UV, including unused inputs.

    A missing requested UV inherits the last earlier present descriptor; a
    generated zero descriptor does not replace that predecessor. With no
    predecessor, buffer0 offset0/UNorm8x4 supplies all four zeros.
    """
    if root is None:raise ValueError('Original UV default requires a verified native source profile')
    root=Path(root).resolve();engine=validate_engine_contract(root)
    if not 0<=channel<8:raise ValueError('Original UV semantic is outside the recovered eight channels')
    previous=next((n for n in range(channel-1,-1,-1) if mesh.get('uv'+str(n))),None)
    source={'semantic':'TEXCOORD'+str(channel),'source_mesh':mesh.get('source'),
        'native_profile':{'libunity.so':engine['player_sha256']},
        'native_contract':engine['contract'],
        'evidence':'reports/vertex-default-normal-20261022/missing_channel_descriptor_standard.asm',
        'source_mesh_channel_modified':False}
    if previous is not None:
        values=mesh['uv'+str(previous)]
        if (len(values)!=len(mesh['vertices']) or not values or not 1<=len(values[0])<=4
            or any(len(row)!=len(values[0]) for row in values)):
            raise ValueError('Original inherited UV source channel has an invalid vertex count/dimension')
        source.update(source='Original Unity missing UV reuses the last earlier present mesh UV descriptor',
            mode='inherited_source_channel',source_channel='uv'+str(previous),
            original_dimension=len(values[0]),selection='All earlier declared mesh UVs, independent of shader requests')
        # A bound source descriptor retains its component count. Vulkan vertex
        # fetch expands missing xyz components with0 and an absent w with1.
        return values,[0.0,0.0,0.0,1.0],source
    source.update(source='Original Unity missing UV default buffer0; no earlier mesh UV descriptor',
        mode='default_zero_stream',default_buffer=0,offset=0,original_format='UNorm8',
        original_dimension=4,original_bytes_hex='00000000',values=[0.0,0.0,0.0,0.0])
    return [[0.0,0.0,0.0,0.0]]*len(mesh['vertices']),[0.0,0.0,0.0,0.0],source
