"""Decode the copied original master cache. No network or target execution.

Aladin variant structure cross-checked against shipped Acp code and aladump's MIT
documentation: https://github.com/SombrAbsol/aladump/blob/main/docs/aladin.md
"""
from pathlib import Path
import struct,hashlib,json
from inspect_original_metadata import ELF
ROOT=Path(__file__).resolve().parents[2] / 'pocket-extraction'
MASK=(1<<64)-1
def rot(v,n,bits=64):return ((v<<n)|(v>>(bits-n)))&((1<<bits)-1)
def xxh64(d):
 p1=0x9e3779b185ebca87;p2=0xc2b2ae3d27d4eb4f;p3=0x165667b19e3779f9;p4=0x85ebca77c2b2ae63;p5=0x27d4eb2f165667c5
 def rnd(a,v):return (rot((a+v*p2)&MASK,31)*p1)&MASK
 p=0
 if len(d)>=32:
  vs=[(p1+p2)&MASK,p2,0,(-p1)&MASK]
  while p<=len(d)-32:
   for j,v in enumerate(struct.unpack_from('<4Q',d,p)):vs[j]=rnd(vs[j],v)
   p+=32
  h=sum(rot(v,n) for v,n in zip(vs,(1,7,12,18)))&MASK
  for v in vs:h=((h^rnd(0,v))*p1+p4)&MASK
 else:h=p5
 h=(h+len(d))&MASK
 while p<=len(d)-8:
  h=(rot(h^rnd(0,struct.unpack_from('<Q',d,p)[0]),27)*p1+p4)&MASK;p+=8
 if p<=len(d)-4:
  h=(rot(h^((struct.unpack_from('<I',d,p)[0]*p1)&MASK),23)*p2+p3)&MASK;p+=4
 while p<len(d):h=(rot(h^((d[p]*p5)&MASK),11)*p1)&MASK;p+=1
 h^=h>>33;h=(h*p2)&MASK;h^=h>>29;h=(h*p3)&MASK;h^=h>>32
 return h

def chacha_original(body,key,iv):
 st=list(struct.unpack('<4I',b'A3AxwtfWD<PbxMx$'))+list(struct.unpack('<8I',key))+[0,iv&0xffffffff,iv>>32,0x63686368]
 step=(((st[9]+st[4])^st[11])+(st[15]^(st[14]+st[13])))&0xffffffff
 idx=(((step>>7)&2)^((step>>2)&1))|((step>>13)&4)|((step>>2)&8)
 turns=(6,5,6,5,5,6,5,6,6,6,5,5,5,6,6,5)[idx]
 def qr(x,a,b,c,d):
  x[a]=(x[a]+x[b])&0xffffffff;x[d]=rot(x[d]^x[a],16,32)
  x[c]=(x[c]+x[d])&0xffffffff;x[b]=rot(x[b]^x[c],12,32)
  x[a]=(x[a]+x[b])&0xffffffff;x[d]=rot(x[d]^x[a],8,32)
  x[c]=(x[c]+x[d])&0xffffffff;x[b]=rot(x[b]^x[c],7,32)
 out=bytearray(len(body))
 for pos in range(0,len(body),64):
  st[12]=(st[12]+1)&0xffffffff;x=st.copy()
  for j in range(turns):
   for q in ((0,4,8,12),(1,5,9,13),(2,6,10,14),(3,7,11,15),(0,5,10,15),(1,6,11,12),(2,7,8,13),(3,4,9,14)):qr(x,*q)
  block=struct.pack('<16I',*((a+b)&0xffffffff for a,b in zip(x,st)))
  out[pos:pos+64]=bytes(a^b for a,b in zip(body[pos:pos+64],block))
 return bytes(out),dict(round_table_index=idx,double_rounds=turns)

def index_entries(d):
 assert d[4:8]==b'ALI2'
 root=struct.unpack_from('<I',d)[0];vt=root-struct.unpack_from('<i',d,root)[0]
 def vector(slot):
  off=struct.unpack_from('<H',d,vt+slot)[0];p=root+off;p+=struct.unpack_from('<I',d,p)[0];n=struct.unpack_from('<I',d,p)[0];return p+4,n
 blobs,n=vector(4);hashes,n2=vector(6);strings,n3=vector(8);assert n==n2==n3
 entries=[]
 for i in range(n):
  ch,cs,bh,bs,key,crypted=struct.unpack_from('<QqQqQB',d,blobs+i*48);p=strings+i*4;p+=struct.unpack_from('<I',d,p)[0];length=struct.unpack_from('<I',d,p)[0];address=d[p+4:p+4+length].decode()
  ah=struct.unpack_from('<Q',d,hashes+i*8)[0];assert xxh64(address.encode())==ah
  entries.append(dict(address=address,address_hash=f'{ah:016x}',content_hash=f'{ch:016x}',content_size=cs,blob_hash=f'{bh:016x}',blob_size=bs,key_id=f'{key:016x}',crypted=crypted))
 return entries

class MessagePack:
 def __init__(self,data,pos=0):self.data=data;self.pos=pos
 def read(self,n):
  b=self.data[self.pos:self.pos+n];self.pos+=n
  if len(b)!=n:raise ValueError('Truncated MessagePack')
  return b
 def val(self):
  b=self.read(1)[0]
  if b<128:return b
  if b>=224:return b-256
  if 160<=b<192:return self.read(b&31).decode('utf8')
  if 144<=b<160:return [self.val() for i in range(b&15)]
  if 128<=b<144:return self.map(b&15)
  if b==192:return None
  if b in (194,195):return b==195
  fmts={202:'>f',203:'>d',204:'>B',205:'>H',206:'>I',207:'>Q',208:'>b',209:'>h',210:'>i',211:'>q'}
  if b in fmts:
   f=fmts[b];return struct.unpack(f,self.read(struct.calcsize(f)))[0]
  if b in (196,197,198,217,218,219,220,221,222,223,199,200,201):
   nbytes={196:1,197:2,198:4,217:1,218:2,219:4,220:2,221:4,222:2,223:4,199:1,200:2,201:4}[b];n=int.from_bytes(self.read(nbytes),'big')
   if b in (196,197,198):return self.read(n)
   if b in (217,218,219):return self.read(n).decode('utf8')
   if b in (220,221):return [self.val() for _ in range(n)]
   if b in (222,223):return self.map(n)
   typ=int.from_bytes(self.read(1),'big',signed=True);return dict(extension=typ,body=self.read(n))
  if 212<=b<=216:
   n=(1,2,4,8,16)[b-212];typ=int.from_bytes(self.read(1),'big',signed=True);return dict(extension=typ,body=self.read(n))
  raise ValueError(f'Unhandled MessagePack {hex(b)} at {self.pos-1}')
 def map(self,n):
  out={}
  for i in range(n):k=self.val();out[k]=self.val()
  return out

def inspect_tables():
 out=ROOT/'input/master-cache-decoded';d=(out/'MasterMemory.bytes').read_bytes();p=MessagePack(d);header=p.val();base=p.pos;print('headerbytes',base,'tables',len(header));print('numeric headers',[(k,v) for k,v in header.items() if k in ('PokemonCard','Pokemon','PokemonAttack','PokemonAttackName')])
 summaries={};parsed={}
 for name in ('PokemonCard','Pokemon','PokemonAttack','PokemonAttackName','ExpansionCollectionNumber','Expansion','Pokedex','Series'):
  off,size=header[name];table=d[base+off:base+off+size];r=MessagePack(table);rows=r.val();compression=None
  if isinstance(rows,dict) and rows.get('extension')==99:
   import lz4.block
   c=MessagePack(rows['body']);length=c.val();raw=lz4.block.decompress(rows['body'][c.pos:],uncompressed_size=length);assert len(raw)==length
   q=MessagePack(raw);rows=q.val();assert q.pos==len(raw);compression=dict(messagepack_extension=99,decompressed_bytes=length)
  summaries[name]=dict(bytes=size,parsed_bytes=r.pos,compression=compression,rows_type=type(rows).__name__,row_count=len(rows),first=rows[:2] if isinstance(rows,list) else rows)
  parsed[name]=rows
  (out/(name+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2,default=lambda b:dict(bytes=len(b),header=b[:32].hex())),encoding='utf8')
 (out/'TABLE_INDEX.json').write_text(json.dumps(dict(header_bytes=base,tables=header),indent=2),encoding='utf8')
 (out/'TABLE_STATS.json').write_text(json.dumps(summaries,indent=2,ensure_ascii=False),encoding='utf8')
 card=next(v for v in parsed['PokemonCard'] if v['IllustrationID']=='cPK_10_000010_00_FUSHIGIDANE_C')
 pokemon=next(v for v in parsed['Pokemon'] if v['PokemonID']==card['PokemonID'])
 attacks=[next(a for a in parsed['PokemonAttack'] if a['PokemonAttackID']==v) for v in pokemon['PokemonAttackIDs']]
 names=[next(n for n in parsed['PokemonAttackName'] if n['PokemonAttackNameID']==a['PokemonAttackNameID']) for a in attacks]
 energy_names={0:'UNSPECIFIED',1:'Colorless',2:'Grass',3:'Fire',4:'Water',5:'Lightning',6:'Psychic',7:'Fighting',8:'Darkness',9:'Metal',10:'Dragon'}
 record=dict(source='Original copied game cache; ALI2 index/blob/content/address hashes verified',art_id=card['IllustrationID'],card_id=card['CardID'],pokemon_id=pokemon['PokemonID'],hp=pokemon['HP'],stage=dict(value=pokemon['EvolutionStage'],label={0:'UNSPECIFIED',1:'Basic',2:'One',3:'Two'}[pokemon['EvolutionStage']]),types=[dict(value=v,label=energy_names[v]) for v in pokemon['PokemonTypes']],retreat_cost=pokemon['RetreatAmount'],weakness=dict(value=pokemon['WeaknessType'],label=energy_names[pokemon['WeaknessType']],operand=20,operand_source='Original native PokemonWeaknessValue.get_Operand VA 0x44ea7cc: MOV w0,#20; RET'),attacks=[dict(id=a['PokemonAttackID'],name_msid=n['NameMSID'],damage=a['Damage'],damage_symbol=a['DamageSymbol'],energy_cost=[dict(value=v,label=energy_names[v]) for v in a['AttackCost']],description_msid=a['DescriptionMSID'],logic_id=a['AttackLogicID'],logic_parameters=a['AttackLogicParameters']) for a,n in zip(attacks,names)],raw_records=dict(PokemonCard=card,Pokemon=pokemon,PokemonAttack=attacks,PokemonAttackName=names),enum_source='original IL2CPP metadata Lettuce.Muscle.EnergyType TypeDefIndex20018; EvolutionStage20019; DamageSymbol20021',master_sha256=hashlib.sha256(d).hexdigest())
 collection=[v for v in parsed['ExpansionCollectionNumber'] if v.get('CardID')==card['CardID']]
 record['raw_records']['ExpansionCollectionNumber']=collection
 if collection:record['raw_records']['Expansion']=[v for v in parsed['Expansion'] if any(x.get('ExpansionID')==v.get('ExpansionID') for x in collection)]
 a1=next(v for v in collection if v['ExpansionID']=='A1');exp=next(v for v in parsed['Expansion'] if v['ExpansionID']=='A1')
 record['collection_number']=dict(expansion_id='A1',numerator=a1['CollectionNumber'],denominator=int(exp['CollectionNumber']))
 record['raw_records']['Pokedex']=next(v for v in parsed['Pokedex'] if v['ID']==card['PokedexID'])
 e=ELF(ROOT/'input/native-code/libil2cpp.so');off=e.off(0x44ea7cc);insn,ret=struct.unpack_from('<II',e.data,off);assert insn&0xffe0001f==0x52800000 and ret==0xd65f03c0
 record['weakness']['operand']=(insn>>5)&0xffff
 (out/'Bulbasaur.original-numeric.json').write_text(json.dumps(record,indent=2,ensure_ascii=False),encoding='utf8')
 print(json.dumps(record,indent=2,ensure_ascii=False))

def main():
 src=ROOT/'input/master-cache-original';out=ROOT/'input/master-cache-decoded';out.mkdir(parents=True,exist_ok=True)
 md=(ROOT/'original-render-pipeline/metadata/global-metadata.original-decoded.dat').read_bytes();key=md[0x1211358:0x1211358+32];assert hashlib.sha256(key).hexdigest()=='d051b739cb6e783207fd98bd3e63203edb3922b161ba6f39c325461f215bc5db';assert xxh64(b'MasterData')==0x075b2dfa1a1e4302
 records=[]
 for p in (src/'index').rglob('*.aladin'):
  for entry in index_entries(p.read_bytes()):
   assert entry['key_id']=='075b2dfa1a1e4302'
   blob=src/'blob'/entry['blob_hash'][:2]/(entry['blob_hash']+'.aladin');raw=blob.read_bytes();assert len(raw)==entry['blob_size'];assert xxh64(raw)==int(entry['blob_hash'],16)
   decoded,settings=chacha_original(raw,key,int(entry['content_hash'],16)) if entry['crypted'] else (raw,{})
   assert len(decoded)==entry['content_size'];assert xxh64(decoded)==int(entry['content_hash'],16),(hex(xxh64(decoded)),entry)
   target=out/entry['address'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(decoded)
   records.append(dict(**entry,**settings,source_blob_sha256=hashlib.sha256(raw).hexdigest(),output=str(target.relative_to(ROOT)),sha256=hashlib.sha256(decoded).hexdigest(),header64=decoded[:64].hex()))
 (out/'PROVENANCE.json').write_text(json.dumps(records,indent=2),encoding='utf8');print(json.dumps(records,indent=2))
if __name__=='__main__':
 import sys
 inspect_tables() if '--tables' in sys.argv else main()
