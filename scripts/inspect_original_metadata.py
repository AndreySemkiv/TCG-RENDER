"""Read-only ELF/metadata probe. No target execution, external processes or installs."""
from pathlib import Path
from collections import Counter
import bisect, hashlib, json, math, struct, zlib, lzma, bz2, re, time

ROOT=Path(__file__).resolve().parents[2] / 'pocket-extraction'
def sx(v,n): return v-(1<<n) if v&(1<<(n-1)) else v

class ELF:
 def __init__(self,path):
  self.path=path;self.data=path.read_bytes();d=self.data
  off=struct.unpack_from('<Q',d,40)[0];size,num,names=struct.unpack_from('<HHH',d,58)
  self.sections=[struct.unpack_from('<IIQQQQIIQQ',d,off+i*size) for i in range(num)]
  ns=self.sections[names];strings=d[ns[4]:ns[4]+ns[5]]
  self.named={strings[s[0]:strings.find(b'\0',s[0])].decode():s for s in self.sections}
  self.symbols=[]
  for s in self.sections:
   if s[1] not in (2,11):continue
   ns=self.sections[s[6]];strings=d[ns[4]:ns[4]+ns[5]]
   for o in range(s[4],s[4]+s[5],s[9]):
    n,info,other,ndx,value,size=struct.unpack_from('<IBBHQQ',d,o)
    self.symbols.append(dict(name=strings[n:strings.find(b'\0',n)].decode('utf8','replace'),value=value,size=size,defined=bool(ndx),type=info&15))
 def va(self,o):
  for s in self.sections:
   if s[1]!=8 and s[4]<=o<s[4]+s[5]:return s[3]+o-s[4]
 def off(self,a):
  for s in self.sections:
   if s[1]!=8 and s[3]<=a<s[3]+s[5]:return s[4]+a-s[3]
 def xrefs(self,targets):
  """Only local ADRP+ADD immediate pairs, max 8 instructions apart; bounded candidate refs."""
  targets=set(targets);out=[]
  for name,s in self.named.items():
   if not s[2]&4:continue
   words=memoryview(self.data)[s[4]:s[4]+s[5]].cast('I');regs={}
   for i,w in enumerate(words):
    pc=s[3]+4*i
    if w&0x9f000000==0x90000000:
     imm=sx(((w>>5)&0x7ffff)*4+((w>>29)&3),21)<<12
     regs[w&31]=((pc&~4095)+imm,i)
    elif w&0xff000000==0x91000000:
     rn=(w>>5)&31;rd=w&31
     if rn in regs and i-regs[rn][1]<=8:
      addr=regs[rn][0]+((w>>10)&4095)*(4096 if w&(1<<22) else 1)
      if addr in targets:out.append(dict(pc=pc,adrp_pc=s[3]+4*regs[rn][1],target=addr,register=rd))
     regs.pop(rd,None)
    elif w&0xfc000000 in (0x14000000,0x94000000):
     if w&0xfc000000==0x94000000:
      for r in range(19):regs.pop(r,None)
    elif w&0x9f000000==0x10000000:
     addr=pc+sx(((w>>5)&0x7ffff)*4+((w>>29)&3),21)
     if addr in targets:out.append(dict(pc=pc,target=addr,register=w&31,kind='ADR'))
  return out

def original_aes(e):
 """Reimplementation of the observed AES128 tables/round layout; no target calls."""
 tables=[struct.unpack_from('<256I',e.data,e.off(a)) for a in (0x2151508,0x2151908,0x2151d08,0x2152108)]
 sb=[(v>>8)&255 for v in tables[0]]
 def expand(key):
  ks=list(struct.unpack('>4I',key));rc=1
  for i in range(4,44):
   t=ks[-1]
   if i%4==0:
    t=(sb[(t>>16)&255]<<24)|(sb[(t>>8)&255]<<16)|(sb[t&255]<<8)|sb[t>>24]
    t^=rc<<24;rc=((rc<<1)^0x11b) if rc&128 else rc<<1
   ks.append(ks[i-4]^t)
  return ks
 def block(data,ks):
  s=[v^k for v,k in zip(struct.unpack('>4I',data),ks[:4])];t0,t1,t2,t3=tables
  for r in range(1,10):
   s=[t0[s[i]>>24]^t1[(s[(i+1)&3]>>16)&255]^t2[(s[(i+2)&3]>>8)&255]^t3[s[(i+3)&3]&255]^ks[r*4+i] for i in range(4)]
  out=[((sb[s[i]>>24]<<24)|(sb[(s[(i+1)&3]>>16)&255]<<16)|(sb[(s[(i+2)&3]>>8)&255]<<8)|sb[s[(i+3)&3]&255])^ks[40+i] for i in range(4)]
  return struct.pack('>4I',*out)
 assert block(bytes.fromhex('00112233445566778899aabbccddeeff'),expand(bytes.fromhex('000102030405060708090a0b0c0d0e0f'))).hex()=='69c4e0d86a7b0430d8cdb78070b4c55a'
 return expand,block

def recover_prefix(e,d,amount=16384):
 expand,block=original_aes(e)
 seed=struct.pack('<Q',0x2d79bbfe57c87ce8)
 pos=e.off(0x81846f5);key=bytes(b^seed[i%8] for i,b in enumerate(e.data[pos:pos+16]));ks=expand(key)
 n=struct.unpack_from('<I',e.data,e.off(0x818268c))[0]
 p=e.off(0x8182690);encrypted=e.data[p+16:p+16+n]+d[4:]
 out=bytearray()
 for i in range((amount+15)//16):
  stream=block(b'\0'*8+(i+1).to_bytes(8,'big'),ks)
  out.extend(a^b for a,b in zip(encrypted[16*i:16*i+16],stream))
 return bytes(out),dict(embedded_prefix_bytes=n,source_payload_bytes=len(d)-4,expected_output_bytes=n+len(d)-4,key_derivation='16 original bytes at VA 0x81846f5 XOR repeated little-endian 64-bit loader constant',counter='initial 16 zero bytes; increment big-endian final 64 bits before AES encryption',aes_nist_known_answer_test=True)
def elf_decode(self,a,count):
 out=[]
 for pc in range(a,a+count*4,4):
  o=self.off(pc)
  if o is None:break
  w=struct.unpack_from('<I',self.data,o)[0];t='?'
  if w&0x9f000000 in (0x90000000,0x10000000):
   imm=sx(((w>>5)&0x7ffff)*4+((w>>29)&3),21)
   t=f'ADRP x{w&31}, {hex((pc&~4095)+(imm<<12))}' if w&0x80000000 else f'ADR x{w&31}, {hex(pc+imm)}'
  elif w&0xfc000000 in (0x14000000,0x94000000):t=('BL ' if w&0x80000000 else 'B ')+hex(pc+(sx(w&0x3ffffff,26)<<2))
  elif w&0xff000000 in (0x91000000,0xd1000000,0xb1000000,0xf1000000,0x11000000,0x51000000,0x71000000):
   t=f'{"SUB" if w&(1<<30) else "ADD"}{"S" if w&(1<<29) else ""} {"x" if w&(1<<31) else "w"}{w&31}, r{(w>>5)&31}, #{((w>>10)&4095)*(4096 if w&(1<<22) else 1)}'
  elif w&0xffe0ffe0==0xaa0003e0:t=f'MOV x{w&31}, x{(w>>16)&31}'
  elif w&0x7f800000 in (0x52800000,0x72800000,0x12800000):t=f'MOVwide r{w&31}, #{hex((w>>5)&65535)}, shift{((w>>21)&3)*16}'
  elif w&0xffc00000 in (0xf9400000,0xf9000000,0xb9400000,0xb9000000,0x39400000,0x39000000):
   scale={0xf9:8,0xb9:4,0x39:1}[w>>24];t=f'{"LDR" if w&(1<<22) else "STR"} r{w&31}, [x{(w>>5)&31}, #{((w>>10)&4095)*scale}]'
  elif w&0xff000010==0x54000000:t=f'B.cond{w&15} {hex(pc+(sx((w>>5)&0x7ffff,19)<<2))}'
  elif w&0x7e000000==0x34000000:t=f'{"CBNZ" if w&(1<<24) else "CBZ"} r{w&31}, {hex(pc+(sx((w>>5)&0x7ffff,19)<<2))}'
  elif w&0xff201fe0==0x1e201000:
   imm=(w>>13)&255;b6=(imm>>6)&1;exp=((b6^1)<<7)|((31 if b6 else 0)<<2)|((imm>>4)&3);bits=((imm>>7)<<31)|(exp<<23)|((imm&15)<<19)
   t=f'FMOV s{w&31}, #{struct.unpack("<f",struct.pack("<I",bits))[0]}'
  elif w&0xfffffc00==0x1e204000:t=f'FMOV s{w&31}, s{(w>>5)&31}'
  elif w&0xfffffc00==0x1e220000:t=f'SCVTF s{w&31}, w{(w>>5)&31}'
  elif w&0xfffffc00==0x1e380000:t=f'FCVTZS w{w&31}, s{(w>>5)&31}'
  elif w&0xffe0fc00==0x1e200800:t=f'FMUL s{w&31}, s{(w>>5)&31}, s{(w>>16)&31}'
  elif w&0xffc00000 in (0xbd400000,0xfd400000):t=f'LDR {"s" if w>>24==0xbd else "d"}{w&31}, [x{(w>>5)&31}, #{((w>>10)&4095)*(4 if w>>24==0xbd else 8)}]'
  elif w==0xd65f03c0:t='RET'
  out.append(f'{pc:09x}: {w:08x} {t}')
 return out

ELF.decode=elf_decode

def map_modules(e,recovered,types):
 vals=struct.unpack_from('<64I',recovered);pairs=list(zip(vals[2::2],vals[3::2]));so,ss=pairs[2]
 def text(i):
  p=so+i;return recovered[p:recovered.find(b'\0',p)].decode('utf8','replace')
 imageoff,imagesize=pairs[20];images=[]
 for p in range(imageoff,imageoff+imagesize,40):
  v=struct.unpack_from('<10i',recovered,p);images.append(dict(name=text(v[0]),typeStart=v[2],typeCount=v[3]))
 s=e.named['.rela.dyn'];relocs={}
 for p in range(s[4],s[4]+s[5],24):
  addr,info,add=struct.unpack_from('<QQq',e.data,p)
  if info==1027:relocs[addr]=add
 def ptr(addr):
  if addr in relocs:return relocs[addr]
  p=e.off(addr);return struct.unpack_from('<Q',e.data,p)[0] if p is not None else 0
 modules={}
 for t in types:
  image=next(i for i in images if i['typeStart']<=t['index']<i['typeStart']+i['typeCount']);name=image['name']
  t['image']=name
  if name not in modules:
   string=e.data.find(name.encode()+b'\0');va=e.va(string) if string>=0 else None
   candidates=[a for a,v in relocs.items() if v==va] if va is not None else []
   modules[name]=None
   for a in candidates:
    off=e.off(a);n=struct.unpack_from('<I',e.data,off+8)[0];arr=ptr(a+16)
    if n>0 and e.off(arr) is not None:
     modules[name]=dict(va=a,methodPointerCount=n,methodPointerArray=arr);break
  module=modules[name]
  if module:
   for m in t['methods']:
    rid=int(m['token'],16)&0xffffff
    if 0<rid<=module['methodPointerCount']:m['nativeVA']=hex(ptr(module['methodPointerArray']+(rid-1)*8))
 return modules,ptr

def focus_existing():
 dest=ROOT/'original-render-pipeline/metadata/global-metadata.original-decoded.dat';recovered=dest.read_bytes();e=ELF(ROOT/'input/native-code/libil2cpp.so')
 report=ROOT/'reports/original-render-pipeline-metadata.md';r=json.loads(report.read_text(encoding='utf8').split('```json\n')[1].split('\n```')[0]);types=r['recovery']['focused_types']
 vals=struct.unpack_from('<64I',recovered);pairs=list(zip(vals[2::2],vals[3::2]));so,ss=pairs[2];to,ts=pairs[19];mo,ms=pairs[5]
 def text(i):
  p=so+i;return recovered[p:recovered.find(b'\0',p)].decode('utf8','replace')
 for i in (31153,31167,31220,31304,31392,31415,31426,31351,31352,42008,42063,42067,45520,45523):
  if any(t['index']==i for t in types):continue
  v=struct.unpack_from('<16i8H2I',recovered,to+i*88);methods=[]
  for j in range(v[9],v[9]+v[16]):
   mv=struct.unpack_from('<7i4H',recovered,mo+j*36)
   methods.append(dict(index=j,name=text(mv[0]),declaringType=mv[1],returnType=mv[2],parameterStart=mv[4],token=hex(mv[6]),flags=mv[7],slot=mv[9],parameterCount=mv[10]))
  types.append(dict(index=i,name=text(v[0]),namespace=text(v[1]),typeToken=hex(v[-1]),methodStart=v[9],methodCount=v[16],methods=methods))
 modules,ptr=map_modules(e,recovered,types)
 r['nativeModules']=modules
 focus=[t for t in types if t['name'] in ('DynamicUI','PokemonCardUIBehaviour','CaptureArea','BattleUITexture','CardTextView','CardTextConfig','CardBehaviour','CardUIBehaviour','UIRenderStudio','CardRenderer','DynamicUITypeExtensions','UICardViewSizeTypeExtensions','RenderStudio','CardQuality','CardQualitySettings','CardDimension','QualitySettings')]
 r['focusedNativeWindows']=[dict(type=t['name'],method=m['name'],nativeVA=m.get('nativeVA'),instructions=e.decode(int(m['nativeVA'],16),400)) for t in focus for m in t['methods'] if m.get('nativeVA') and (t['name']=='DynamicUI' or m['name'] in ('UpdateDynamicUIView','UpdateTextView','Setup','GetOrCreateRenderTexture','UpdateDynamicTexture','CaptureArea','CreateRenderTexture','RenderOneShot','Render','ToCardSize','GetRenderTextureName','PixelSize','SetQuality') or t['name'] in ('CardQuality','CardQualitySettings','CardDimension') and m['name'] in ('.cctor','.ctor','get_DynamicTextQuality','SetDynamicTextQuality'))]
 intro='''# Original metadata loader and rendering methods\n\nThe protected metadata has been recovered statically from the original copied game files. Its decoded header is standard IL2CPP version 31. All table offsets and lengths fit the recovered buffer. Type and method names map to original native pointers through image records and ELF relative relocations; these mappings identify bodies, not observed execution.\n\nThe metadata filename reference is at VA `0x324f094`; its loader is `0x3262d64`, which calls decoder `0x325a218` with constant `0x2d79bbfe57c87ce8`. The decoder combines 8192 bytes from original embedded data at `0x8182690 + 16` with the supplied metadata payload after its four-byte length. Its AES128 key derives from 16 original bytes at `0x81846f5` XOR the repeated little-endian 64-bit loader constant. The counter starts at zero and increments its big-endian last 64 bits before each AES block. The AES implementation uses the four original T tables at `0x2151508`, `0x2151908`, `0x2151d08`, `0x2152108` and passes a standard AES128 known-answer vector. No runtime or game code was executed.\n\nThe lightweight ARM64 decoder only recognizes listed instruction families and local ADRP+ADD/ADR references; `?` denotes undecoded instructions. Code addresses and call targets are useful static evidence, but unrecognized instructions are not silently treated as no-ops.\n\n```json\n'''
 findings='''## Original dynamic-text allocation recovered

`CardBehaviour.GetOrCreateRenderTexture` at `0x47d4670` allocates its dynamic UI texture using the original `CardDimension.PixelSize(sizeType)` multiplied by `CardQuality.DynamicTextQuality.Value`. Width and height are separately converted from float to integer before calling `RenderTexture` constructor at `0x6a73138`, with depth 24 and `RenderTextureFormat.Default` (numeric 7). The provider at static-fields offset +8 is `_dynamicTextQuality`; the recovered field declarations corroborate the native loads. The result is cached by `(FrameType, DynamicUIType)`.

`CardDimension..cctor` at `0x474cc44` stores `734×1024` as `s_largePixelSize` and `367×512` as `s_mediumPixelSize`; dictionary keys 3 and 2 match the recovered `CardSizeType.Large` and `Medium` enum declarations. These are now original code constants, independently corroborating the serialized UI dimensions.

`QualitySettings.SetQuality` at `0x4a6eec8` branches on the recovered quality enum and sets:

| Original quality | UI card quality | Dynamic text quality | Large dynamic-text allocation |
|---|---:|---:|---:|
| High (0) | 1.0 | 2.0 | 1468×2048 |
| Middle (1) | 0.8 | 1.0 | 734×1024 |
| Low (2) | 0.7 | 1.0 | 734×1024 |

The High branch loads immediate 2.0 into s8 (`0x4a6ef74`) for dynamic text and 1.0 into s9 for UI. The Middle and Low branches both load dynamic-text factor 1.0 (`0x4a6efdc`, `0x4a6f024`); UI float constants 0.7 and 0.8 are read at original VAs `0x1ce3228`, `0x1ce2fb0`. The common path calls `CardQuality.SetUiCardQuality` (`0x480b878`) then `SetDynamicTextQuality` (`0x480b910`). A bounded direct B/BL search found these call sites for the two setters. Other indirect invocation paths are not exhaustively excluded.

The reactive initial value is 1.5 (`CardQuality..cctor`, `0x480ba48`) before quality setup; the fallback `CardQualitySettings` constructor stores UI=1.0 and dynamic=2.0 from original literal at `0x1ce1ea0`. Actual applied settings in a running installation were not inspected.

Rendering the original text geometry/font at the original High allocation of 1468×2048 would follow the game's own allocation policy. It is not evidence of a stored complete card image at that size, and it does not enlarge an already extracted raster. Shader semantics and original text/layout steps still have to be reproduced from the original pipeline before delivering a faithful rendered card.

## Cross-checked dump declarations

The parent-generated `original-render-pipeline/metadata/il2cpp-dump.cs` corroborates: `CardBehaviour` cached RenderTextures/parameters at lines 1206461–1206514; `CardQuality._dynamicTextQuality` and `CardQualitySettings` at 1212882–1212938; `CardDimension` large/medium field declarations at 1544233–1544248; `CardSizeType` values at 1546027–1546034; quality enum at 1642209–1642215 and quality setup/setter RVAs at 1642244–1642278; `RenderTextureFormat.Default=7` at 888176. These declarations map metadata/fields; instruction observations establish the numeric assignments and allocation arithmetic.

## Reproducible observations

'''
 intro=intro.replace('```json\n',findings+'```json\n')
 report.write_text(intro+json.dumps(r,indent=2)+'\n```\n',encoding='utf8')
 print(json.dumps(dict(modules=modules,focus=focus),indent=2))

def main():
 m=ROOT/'input/unity-data/Managed/Metadata/global-metadata.dat';d=m.read_bytes();payload=d[4:]
 counts=Counter(payload);ent=-sum(n/len(payload)*math.log2(n/len(payload)) for n in counts.values())
 probes=[]
 for offset in (0,4,8,12,16,20,32,64):
  for name,func in [('zlib',zlib.decompress),('raw-deflate',lambda b:zlib.decompress(b,-15)),('gzip',lambda b:zlib.decompress(b,31)),('lzma',lzma.decompress),('bzip2',bz2.decompress)]:
   try:
    r=func(d[offset:]);probes.append(dict(offset=offset,codec=name,result_bytes=len(r),first16=r[:16].hex()))
   except Exception:pass
 e=ELF(ROOT/'input/native-code/libil2cpp.so')
 recovered,recipe=recover_prefix(e,d,len(d)-4+8192)
 assert struct.unpack_from('<II',recovered)==(0xfab11baf,31)
 dest=ROOT/'original-render-pipeline/metadata/global-metadata.original-decoded.dat';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(recovered)
 vals=struct.unpack_from('<64I',recovered)
 pairs=list(zip(vals[2::2],vals[3::2]));assert all(a+n<=len(recovered) for a,n in pairs)
 stringoff,stringsize=pairs[2]
 def text(index):
  if index<0 or index>=stringsize:return '<invalid>'
  p=stringoff+index;return recovered[p:recovered.find(b'\0',p)].decode('utf8','replace')
 typeoff,typesize=pairs[19];methodoff,methodsize=pairs[5]
 assert typesize%88==0 and methodsize%36==0
 matched=[]
 for i,p in enumerate(range(typeoff,typeoff+typesize,88)):
  v=struct.unpack_from('<16i8H2I',recovered,p);name=text(v[0]);namespace=text(v[1])
  if ('DynamicUI' in name or 'PokemonCardUI' in name or name=='TextMeshProUGUI' or name=='RenderTexture' or ('Card' in namespace and re.search('Text|Render|Capture|View',name))):
   methods=[]
   for j in range(v[9],v[9]+v[16]):
    mv=struct.unpack_from('<7i4H',recovered,methodoff+j*36)
    methods.append(dict(index=j,name=text(mv[0]),declaringType=mv[1],returnType=mv[2],parameterStart=mv[4],token=hex(mv[6]),flags=mv[7],slot=mv[9],parameterCount=mv[10]))
   matched.append(dict(index=i,name=name,namespace=namespace,typeToken=hex(v[-1]),methodStart=v[9],methodCount=v[16],methods=methods))
 recipe.update(decoded_file=str(dest.relative_to(ROOT)),decoded_bytes=len(recovered),decoded_sha256=hashlib.sha256(recovered).hexdigest(),header_version=vals[1],header_magic=hex(vals[0]),tables=pairs,typeDefinition_bytes=88,typeDefinition_count=typesize//88,methodDefinition_bytes=36,methodDefinition_count=methodsize//36,focused_types=matched)
 terms=[b'global-metadata.dat',b'DynamicUI',b'PokemonCardUI',b'UnityEngine.RenderTexture::GetTemporary',b'UnityEngine.RenderTexture::Internal_Create']
 hits=[]
 for term in terms:
  start=0
  while (o:=e.data.find(term,start))>=0:
   end=e.data.find(b'\0',o);hits.append(dict(term=term.decode(),offset=o,va=e.va(o),text=e.data[o:min(end,o+240)].decode('utf8','replace')));start=o+1
 x=e.xrefs([h['va'] for h in hits if h['va'] is not None])
 result=dict(recovery=recipe,metadata=dict(bytes=len(d),sha256=hashlib.sha256(d).hexdigest(),length_prefix=struct.unpack_from('<I',d)[0],first64=d[:64].hex(),payload_entropy=ent,codec_successes=probes),elf=dict(sha256=hashlib.sha256(e.data).hexdigest(),sections={n:dict(va=s[3],offset=s[4],size=s[5]) for n,s in e.named.items() if n in ('.text','.rodata','.eh_frame_hdr','.eh_frame','.plt','.rela.plt','il2cpp')},symbols=[s for s in e.symbols if s['name'] in ('il2cpp_init','il2cpp_init_utf16')]),strings=hits,xrefs=x,windows=[dict(xref=q,instructions=e.decode(q['pc']-48,60)) for q in x])
 report='# Original metadata loader: bounded static pass\n\nAll observations below come from copied bytes. No game execution, hooks, frame capture, installations or image operations occurred. The ARM64 decoder only recognizes listed instruction families and local ADRP+ADD/ADR references; it is not a complete disassembler or control-flow proof.\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n'
 (ROOT/'reports/original-render-pipeline-metadata.md').write_text(report,encoding='utf8')
 print(json.dumps(dict(decoded=recipe['decoded_file'],sha256=recipe['decoded_sha256'],matched_types=[dict(index=t['index'],name=t['namespace']+'.'+t['name'],methods=t['methodCount']) for t in matched]),indent=2))
if __name__=='__main__':
 import sys
 focus_existing() if '--focus-existing' in sys.argv else main()
