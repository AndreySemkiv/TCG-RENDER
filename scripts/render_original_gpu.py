"""Bounded headless proof executing unchanged original Vulkan SPIR-V.

No game process, screen capture, source raster resizing, or shader math rewrite.
"""
from pathlib import Path
import sys, json, struct, hashlib, traceback, argparse
ROOT=Path(__file__).resolve().parents[2] / 'pocket-extraction'
sys.path.insert(0,str(ROOT/'tools/wgpu'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pocket_renderer.texture_upload import texture_upload_data
from PIL import Image
import vulkan as vk

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--additional-draws',type=Path,help='JSON pass manifest; paths relative to that file')
parser.add_argument('--skip-base',action='store_true',help='Only execute supplied additional passes')
parser.add_argument('--skip-wgpu',action='store_true')
parser.add_argument('--width',type=int,default=700)
parser.add_argument('--height',type=int,default=976)
parser.add_argument('--output',type=Path)
parser.add_argument('--rgba',action='store_true',help='Retain original alpha attachment readback')
parser.add_argument('--clear',type=float,nargs=4,default=[.15,.15,.15,1],metavar=('R','G','B','A'))
parser.add_argument('--report',type=Path)
parser.add_argument('--verify-raw-texture-upload',action='store_true',help='Read raw R16 GPU images back byte-for-byte for transport verification')
parser.add_argument('--assets-dir',type=Path,default=ROOT/'offline-render/bulbasaur-original',help='Prepared original scene.json/materials.json and native textures')
parser.add_argument('--decoded-shaders',type=Path,default=ROOT/'original-render-pipeline/shaders/decode-manifest.json',help='Original SPIR-V decode manifest (paths relative to project root)')
args=parser.parse_args()
OUT=args.output.resolve().parent if args.output else ROOT/'original-render-pipeline/gpu-proof'
OUT.mkdir(parents=True,exist_ok=True)
ASSET=args.assets_dir.resolve()
W,H=args.width,args.height
assert W>0 and H>0
scene=json.loads((ASSET/'scene.json').read_text(encoding='utf-8'))
materials={m['id']:m for m in json.loads((ASSET/'materials.json').read_text(encoding='utf-8'))}
textures={t['id']:t for t in scene['textures']}
meshes={m['id']:json.loads((ASSET/m['file']).read_text(encoding='utf-8')) for m in scene['meshes']}
decoded=json.loads(args.decoded_shaders.resolve().read_text(encoding='utf-8'))['decoded_programs']
report={'status':'started','output_pixels':[W,H],'backend':'direct Vulkan Python binding, original driver','wgpu_probe':{},'shader_sources':[],'draws':[],
 'limits':['Base proof only: illustration, grass background, frame and panel; no localized DynamicUI texture, rarity shader, backside or lighting pass.',
 'Frontal orthographic projection is a harness choice because the original camera/projection are runtime code, not established by saved scene transforms.',
 'Closest sampling is a harness choice to honor no-upscale intent; source texture filter_mode1 indicates a different original sampler choice.',
 'PNG textures upload as native RGBA8 UNORM; explicitly described raw R16 textures retain all 16 bits. Complete original Unity color-management/render-target configuration is not established.',
 'PNG row orientation converted in upload memory for original Unity bottom-left UVs; original PNG files untouched.',
 'RGB readback retains shader RGB; alpha attachment channel is not exported because original blend alpha factors have separate mask behavior.'],
 'no_resize':True,'no_game_execution':True,'no_shader_rewrite':True,'assets_dir':str(ASSET),'decoded_shaders':str(args.decoded_shaders.resolve())}
if args.skip_base:
 report['limits']=[s for s in report['limits'] if not s.startswith(('Base proof only:','Frontal orthographic projection','Closest sampling'))]
 report['limits'].append('Additional pass geometry, matrices, shader selection and UBO values originate in the supplied manifest; consult its provenance and limitations.')
if args.rgba:
 report['limits']=[s for s in report['limits'] if not s.startswith('RGB readback')]

def save_report():
 dest=args.report or (OUT/(args.output.stem+'.report.json' if args.output else 'report.json'))
 dest.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')

def shader_pair(fragment):
 rows=[r for r in decoded if r['shader_bundle'].endswith(fragment)]
 return {r['entry_points'][0]['execution_model']:ROOT/r['spirv_file'] for r in rows}

def probe_wgpu():
 try:
  import wgpu
  a=wgpu.gpu.request_adapter_sync(power_preference='high-performance');d=a.request_device_sync()
  results=[]
  for stage,path in shader_pair('Card_Illust.shader_bundles').items():
   try:d.create_shader_module(code=path.read_bytes());results.append({'execution_model':stage,'status':'accepted'})
   except Exception as exc:results.append({'execution_model':stage,'status':'rejected','error':str(exc)})
  report['wgpu_probe']={'version':wgpu.__version__,'adapter':dict(a.info),'unchanged_original_stages':results,'reason_for_direct_vulkan':'Original combined image sampler resource interface cannot be expressed as a WebGPU combined binding; no shader/resource rewrite performed.'}
 except Exception as exc:report['wgpu_probe']={'error':str(exc)}

if not args.skip_wgpu:probe_wgpu()
inst=None;device=None
try:
 inst=vk.vkCreateInstance(vk.VkInstanceCreateInfo(pApplicationInfo=vk.VkApplicationInfo(pApplicationName='Pocket original shader proof',apiVersion=vk.VK_API_VERSION_1_0)),None)
 physical=vk.vkEnumeratePhysicalDevices(inst)[0]
 props=vk.vkGetPhysicalDeviceProperties(physical)
 report['adapter']={'device':props.deviceName,'vendor_id':props.vendorID,'device_id':props.deviceID,'api_version':props.apiVersion}
 qfamilies=vk.vkGetPhysicalDeviceQueueFamilyProperties(physical)
 qindex=next(i for i,q in enumerate(qfamilies) if q.queueFlags&vk.VK_QUEUE_GRAPHICS_BIT)
 device=vk.vkCreateDevice(physical,vk.VkDeviceCreateInfo(pQueueCreateInfos=[vk.VkDeviceQueueCreateInfo(queueFamilyIndex=qindex,pQueuePriorities=[1.0])]),None)
 queue=vk.vkGetDeviceQueue(device,qindex,0)
 memprops=vk.vkGetPhysicalDeviceMemoryProperties(physical)
 keep=[]
 def memtype(bits,flags):
  return next(i for i in range(memprops.memoryTypeCount) if bits&(1<<i) and (memprops.memoryTypes[i].propertyFlags&flags)==flags)
 def buffer(data=None,size=None,usage=0):
  size=size or len(data)
  b=vk.vkCreateBuffer(device,vk.VkBufferCreateInfo(size=size,usage=usage,sharingMode=vk.VK_SHARING_MODE_EXCLUSIVE),None)
  req=vk.vkGetBufferMemoryRequirements(device,b)
  mem=vk.vkAllocateMemory(device,vk.VkMemoryAllocateInfo(allocationSize=req.size,memoryTypeIndex=memtype(req.memoryTypeBits,vk.VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT|vk.VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)),None)
  vk.vkBindBufferMemory(device,b,mem,0)
  if data is not None:
   mapped=vk.vkMapMemory(device,mem,0,len(data),0);mapped[:]=data;vk.vkUnmapMemory(device,mem)
  keep.append(('buffer',b,mem));return b,mem
 def image(width,height,fmt,usage,aspect,layers=1,cube=False):
  im=vk.vkCreateImage(device,vk.VkImageCreateInfo(flags=vk.VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT if cube else 0,imageType=vk.VK_IMAGE_TYPE_2D,format=fmt,extent=vk.VkExtent3D(width=width,height=height,depth=1),mipLevels=1,arrayLayers=layers,samples=vk.VK_SAMPLE_COUNT_1_BIT,tiling=vk.VK_IMAGE_TILING_OPTIMAL,usage=usage,sharingMode=vk.VK_SHARING_MODE_EXCLUSIVE,initialLayout=vk.VK_IMAGE_LAYOUT_UNDEFINED),None)
  req=vk.vkGetImageMemoryRequirements(device,im)
  mem=vk.vkAllocateMemory(device,vk.VkMemoryAllocateInfo(allocationSize=req.size,memoryTypeIndex=memtype(req.memoryTypeBits,vk.VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)),None)
  vk.vkBindImageMemory(device,im,mem,0)
  view=vk.vkCreateImageView(device,vk.VkImageViewCreateInfo(image=im,viewType=vk.VK_IMAGE_VIEW_TYPE_CUBE if cube else vk.VK_IMAGE_VIEW_TYPE_2D,format=fmt,subresourceRange=vk.VkImageSubresourceRange(aspectMask=aspect,baseMipLevel=0,levelCount=1,baseArrayLayer=0,layerCount=layers)),None)
  keep.append(('image',im,mem,view));return im,view
 pool=vk.vkCreateCommandPool(device,vk.VkCommandPoolCreateInfo(queueFamilyIndex=qindex,flags=vk.VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT),None)
 def begin():
  cmd=vk.vkAllocateCommandBuffers(device,vk.VkCommandBufferAllocateInfo(commandPool=pool,level=vk.VK_COMMAND_BUFFER_LEVEL_PRIMARY,commandBufferCount=1))[0]
  vk.vkBeginCommandBuffer(cmd,vk.VkCommandBufferBeginInfo(flags=vk.VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT));return cmd
 def submit(cmd):
  vk.vkEndCommandBuffer(cmd);vk.vkQueueSubmit(queue,1,[vk.VkSubmitInfo(pCommandBuffers=[cmd])],vk.VK_NULL_HANDLE);vk.vkQueueWaitIdle(queue)
 def transition(cmd,im,old,new,srcstage,dststage,srcaccess,dstaccess,layers=1):
  barrier=vk.VkImageMemoryBarrier(srcAccessMask=srcaccess,dstAccessMask=dstaccess,oldLayout=old,newLayout=new,srcQueueFamilyIndex=vk.VK_QUEUE_FAMILY_IGNORED,dstQueueFamilyIndex=vk.VK_QUEUE_FAMILY_IGNORED,image=im,subresourceRange=vk.VkImageSubresourceRange(aspectMask=vk.VK_IMAGE_ASPECT_COLOR_BIT,baseMipLevel=0,levelCount=1,baseArrayLayer=0,layerCount=layers))
  vk.vkCmdPipelineBarrier(cmd,srcstage,dststage,0,0,None,0,None,1,[barrier])
 uploaded={}
 report['texture_uploads']=[]
 def upload_texture(tid):
  if tid in uploaded:return uploaded[tid]
  t=textures[tid];path=Path(t['png']) if Path(t['png']).is_absolute() else ASSET/t['png']
  loaded=texture_upload_data(path,t.get('image_format'),[t['width'],t['height']],t.get('row_origin'))
  width,height=loaded['width'],loaded['height'];pixels=loaded['pixels']
  raw_r16=loaded['format']=='r16-unorm'
  fmt=vk.VK_FORMAT_R16_UNORM if raw_r16 else vk.VK_FORMAT_R8G8B8A8_UNORM
  if raw_r16:
   features=vk.vkGetPhysicalDeviceFormatProperties(physical,fmt).optimalTilingFeatures
   required=vk.VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT
   if t.get('sampler')=='linear':required|=vk.VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT
   if features&required!=required:raise ValueError('The Vulkan device cannot sample the declared R16 UNORM texture/filter')
  stage,_=buffer(data=pixels,usage=vk.VK_BUFFER_USAGE_TRANSFER_SRC_BIT)
  verify=raw_r16 and args.verify_raw_texture_upload
  usage=vk.VK_IMAGE_USAGE_TRANSFER_DST_BIT|vk.VK_IMAGE_USAGE_SAMPLED_BIT
  if verify:usage|=vk.VK_IMAGE_USAGE_TRANSFER_SRC_BIT
  im,view=image(width,height,fmt,usage,vk.VK_IMAGE_ASPECT_COLOR_BIT)
  cmd=begin();transition(cmd,im,vk.VK_IMAGE_LAYOUT_UNDEFINED,vk.VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,vk.VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,vk.VK_PIPELINE_STAGE_TRANSFER_BIT,0,vk.VK_ACCESS_TRANSFER_WRITE_BIT)
  region=vk.VkBufferImageCopy(bufferOffset=0,bufferRowLength=0,bufferImageHeight=0,imageSubresource=vk.VkImageSubresourceLayers(aspectMask=vk.VK_IMAGE_ASPECT_COLOR_BIT,mipLevel=0,baseArrayLayer=0,layerCount=1),imageOffset=vk.VkOffset3D(0,0,0),imageExtent=vk.VkExtent3D(width,height,1))
  vk.vkCmdCopyBufferToImage(cmd,stage,im,vk.VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,1,[region])
  if verify:
   readbuf,readmem=buffer(size=len(pixels),usage=vk.VK_BUFFER_USAGE_TRANSFER_DST_BIT)
   transition(cmd,im,vk.VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,vk.VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,vk.VK_PIPELINE_STAGE_TRANSFER_BIT,vk.VK_PIPELINE_STAGE_TRANSFER_BIT,vk.VK_ACCESS_TRANSFER_WRITE_BIT,vk.VK_ACCESS_TRANSFER_READ_BIT)
   vk.vkCmdCopyImageToBuffer(cmd,im,vk.VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,readbuf,1,[region])
   barrier=vk.VkBufferMemoryBarrier(srcAccessMask=vk.VK_ACCESS_TRANSFER_WRITE_BIT,dstAccessMask=vk.VK_ACCESS_HOST_READ_BIT,srcQueueFamilyIndex=vk.VK_QUEUE_FAMILY_IGNORED,dstQueueFamilyIndex=vk.VK_QUEUE_FAMILY_IGNORED,buffer=readbuf,offset=0,size=len(pixels))
   vk.vkCmdPipelineBarrier(cmd,vk.VK_PIPELINE_STAGE_TRANSFER_BIT,vk.VK_PIPELINE_STAGE_HOST_BIT,0,0,None,1,[barrier],0,None)
   transition(cmd,im,vk.VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,vk.VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,vk.VK_PIPELINE_STAGE_TRANSFER_BIT,vk.VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT,vk.VK_ACCESS_TRANSFER_READ_BIT,vk.VK_ACCESS_SHADER_READ_BIT)
  else:transition(cmd,im,vk.VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,vk.VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,vk.VK_PIPELINE_STAGE_TRANSFER_BIT,vk.VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT,vk.VK_ACCESS_TRANSFER_WRITE_BIT,vk.VK_ACCESS_SHADER_READ_BIT)
  submit(cmd)
  upload_report={'file':str(path),'image_format':loaded['format'],'image_size':[width,height],
   'source_row_origin':loaded['source_row_origin'],'gpu_row_origin':loaded['gpu_row_origin'],
   'component_bits':loaded['component_bits'],'mip_levels':1,'conversion':loaded['conversion'],
   'rows_flipped_in_upload_memory':loaded['rows_flipped_in_upload_memory'],
   'upload_sha256':hashlib.sha256(pixels).hexdigest(),'source_file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
  if verify:
   mapped=vk.vkMapMemory(device,readmem,0,len(pixels),0);returned=bytes(mapped[:len(pixels)]);vk.vkUnmapMemory(device,readmem)
   if returned!=pixels:raise ValueError('R16 UNORM GPU upload/readback changed original component bytes')
   upload_report.update(gpu_readback_sha256=hashlib.sha256(returned).hexdigest(),gpu_readback_identical=True)
  report['texture_uploads'].append(upload_report)
  uploaded[tid]=(view,t);return view,t
 def upload_extra_texture(path,binding):
  identity={key:binding[key] for key in ('image_format','image_size','row_origin','sampler') if key in binding}
  tid='extra:'+str(path)+':'+json.dumps(identity,sort_keys=True)
  if binding.get('image_format')=='r16-unorm':
   size=binding.get('image_size')
   if not isinstance(size,(list,tuple)) or len(size)!=2:raise ValueError('Raw R16 binding requires image_size')
   width,height=size
  else:
   with Image.open(path) as img:width,height=img.size
  textures[tid]={'png':str(path),'width':width,'height':height,'name':path.name,**identity}
  return upload_texture(tid)
 def upload_cube(path):
  desc=json.loads(path.read_text(encoding='utf8'));faces=[(path.parent/f).resolve() for f in desc['faces']]
  assert len(faces)==6
  imgs=[Image.open(f).convert('RGBA') for f in faces];width,height=imgs[0].size
  assert width==height and all(im.size==(width,height) for im in imgs)
  pixels=b''.join(im.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes() for im in imgs)
  stage,_=buffer(data=pixels,usage=vk.VK_BUFFER_USAGE_TRANSFER_SRC_BIT)
  im,view=image(width,height,vk.VK_FORMAT_R8G8B8A8_UNORM,vk.VK_IMAGE_USAGE_TRANSFER_DST_BIT|vk.VK_IMAGE_USAGE_SAMPLED_BIT,vk.VK_IMAGE_ASPECT_COLOR_BIT,layers=6,cube=True)
  cmd=begin();transition(cmd,im,vk.VK_IMAGE_LAYOUT_UNDEFINED,vk.VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,vk.VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,vk.VK_PIPELINE_STAGE_TRANSFER_BIT,0,vk.VK_ACCESS_TRANSFER_WRITE_BIT,6)
  regions=[vk.VkBufferImageCopy(bufferOffset=i*width*height*4,bufferRowLength=0,bufferImageHeight=0,imageSubresource=vk.VkImageSubresourceLayers(aspectMask=vk.VK_IMAGE_ASPECT_COLOR_BIT,mipLevel=0,baseArrayLayer=i,layerCount=1),imageOffset=vk.VkOffset3D(0,0,0),imageExtent=vk.VkExtent3D(width,height,1)) for i in range(6)]
  vk.vkCmdCopyBufferToImage(cmd,stage,im,vk.VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,6,regions)
  transition(cmd,im,vk.VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,vk.VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,vk.VK_PIPELINE_STAGE_TRANSFER_BIT,vk.VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT,vk.VK_ACCESS_TRANSFER_WRITE_BIT,vk.VK_ACCESS_SHADER_READ_BIT,6);submit(cmd)
  return view,[{'file':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'native_dimensions':[width,height]} for f in faces]
 color_format=vk.VK_FORMAT_R8G8B8A8_UNORM
 color_images=[image(W,H,color_format,vk.VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT|vk.VK_IMAGE_USAGE_TRANSFER_SRC_BIT,vk.VK_IMAGE_ASPECT_COLOR_BIT) for _ in range(2)]
 depth_format=vk.VK_FORMAT_D32_SFLOAT_S8_UINT
 depth_image=image(W,H,depth_format,vk.VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT,vk.VK_IMAGE_ASPECT_DEPTH_BIT|vk.VK_IMAGE_ASPECT_STENCIL_BIT)
 attachments=[vk.VkAttachmentDescription(format=color_format,samples=vk.VK_SAMPLE_COUNT_1_BIT,loadOp=vk.VK_ATTACHMENT_LOAD_OP_CLEAR,storeOp=vk.VK_ATTACHMENT_STORE_OP_STORE,stencilLoadOp=vk.VK_ATTACHMENT_LOAD_OP_DONT_CARE,stencilStoreOp=vk.VK_ATTACHMENT_STORE_OP_DONT_CARE,initialLayout=vk.VK_IMAGE_LAYOUT_UNDEFINED,finalLayout=vk.VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL) for _ in range(2)]
 attachments.append(vk.VkAttachmentDescription(format=depth_format,samples=vk.VK_SAMPLE_COUNT_1_BIT,loadOp=vk.VK_ATTACHMENT_LOAD_OP_CLEAR,storeOp=vk.VK_ATTACHMENT_STORE_OP_DONT_CARE,stencilLoadOp=vk.VK_ATTACHMENT_LOAD_OP_CLEAR,stencilStoreOp=vk.VK_ATTACHMENT_STORE_OP_DONT_CARE,initialLayout=vk.VK_IMAGE_LAYOUT_UNDEFINED,finalLayout=vk.VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL))
 subpass=vk.VkSubpassDescription(pipelineBindPoint=vk.VK_PIPELINE_BIND_POINT_GRAPHICS,pColorAttachments=[vk.VkAttachmentReference(attachment=i,layout=vk.VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL) for i in range(2)],pDepthStencilAttachment=vk.VkAttachmentReference(attachment=2,layout=vk.VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL))
 deps=[vk.VkSubpassDependency(srcSubpass=vk.VK_SUBPASS_EXTERNAL,dstSubpass=0,srcStageMask=vk.VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,dstStageMask=vk.VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT|vk.VK_PIPELINE_STAGE_EARLY_FRAGMENT_TESTS_BIT,srcAccessMask=0,dstAccessMask=vk.VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT|vk.VK_ACCESS_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT),vk.VkSubpassDependency(srcSubpass=0,dstSubpass=vk.VK_SUBPASS_EXTERNAL,srcStageMask=vk.VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,dstStageMask=vk.VK_PIPELINE_STAGE_TRANSFER_BIT,srcAccessMask=vk.VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,dstAccessMask=vk.VK_ACCESS_TRANSFER_READ_BIT)]
 renderpass=vk.vkCreateRenderPass(device,vk.VkRenderPassCreateInfo(pAttachments=attachments,pSubpasses=[subpass],pDependencies=deps),None)
 framebuffer=vk.vkCreateFramebuffer(device,vk.VkFramebufferCreateInfo(renderPass=renderpass,pAttachments=[x[1] for x in color_images]+[depth_image[1]],width=W,height=H,layers=1),None)
 sampler=vk.vkCreateSampler(device,vk.VkSamplerCreateInfo(magFilter=vk.VK_FILTER_NEAREST,minFilter=vk.VK_FILTER_NEAREST,mipmapMode=vk.VK_SAMPLER_MIPMAP_MODE_NEAREST,addressModeU=vk.VK_SAMPLER_ADDRESS_MODE_REPEAT,addressModeV=vk.VK_SAMPLER_ADDRESS_MODE_REPEAT,addressModeW=vk.VK_SAMPLER_ADDRESS_MODE_REPEAT,maxLod=0),None)
 linear_sampler=vk.vkCreateSampler(device,vk.VkSamplerCreateInfo(magFilter=vk.VK_FILTER_LINEAR,minFilter=vk.VK_FILTER_LINEAR,mipmapMode=vk.VK_SAMPLER_MIPMAP_MODE_NEAREST,addressModeU=vk.VK_SAMPLER_ADDRESS_MODE_REPEAT,addressModeV=vk.VK_SAMPLER_ADDRESS_MODE_REPEAT,addressModeW=vk.VK_SAMPLER_ADDRESS_MODE_REPEAT,maxLod=0),None)
 clamp_samplers={name:vk.vkCreateSampler(device,vk.VkSamplerCreateInfo(magFilter=filt,minFilter=filt,mipmapMode=vk.VK_SAMPLER_MIPMAP_MODE_NEAREST,addressModeU=vk.VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,addressModeV=vk.VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,addressModeW=vk.VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,maxLod=0),None) for name,filt in [('linear',vk.VK_FILTER_LINEAR),('nearest',vk.VK_FILTER_NEAREST)]}
 descriptor_pool=vk.vkCreateDescriptorPool(device,vk.VkDescriptorPoolCreateInfo(maxSets=512,pPoolSizes=[vk.VkDescriptorPoolSize(type=vk.VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,descriptorCount=512),vk.VkDescriptorPoolSize(type=vk.VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,descriptorCount=1024)]),None)
 tex_layout=vk.vkCreateDescriptorSetLayout(device,vk.VkDescriptorSetLayoutCreateInfo(pBindings=[vk.VkDescriptorSetLayoutBinding(binding=0,descriptorType=vk.VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,descriptorCount=1,stageFlags=vk.VK_SHADER_STAGE_FRAGMENT_BIT)]),None)
 shader_modules={}
 def modules(kind):
  if kind not in shader_modules:
   pairs=shader_pair('Card_Illust.shader_bundles' if kind=='illust' else 'Card_Frame.shader_bundles')
   result={}
   for stage,path in pairs.items():
    raw=path.read_bytes();result[stage]=vk.vkCreateShaderModule(device,vk.VkShaderModuleCreateInfo(codeSize=len(raw),pCode=raw),None)
    report['shader_sources'].append({'file':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(raw).hexdigest(),'execution_model':stage,'modified':False})
   shader_modules[kind]=result
  return shader_modules[kind]
 def columns(m):return [float(m[r][c]) for c in range(4) for r in range(4)]
 xmin,xmax=-0.3150000274181366,0.31507012248039246;ymin,ymax=-0.4400000274181366,0.4400000274181366
 vp=[[2/(xmax-xmin),0,0,-(xmax+xmin)/(xmax-xmin)],[0,2/(ymax-ymin),0,-(ymax+ymin)/(ymax-ymin)],[0,0,1,.5],[0,0,0,1]]
 report['projection']={'kind':'harness frontal orthographic','bounds':[xmin,xmax,ymin,ymax],'matrix':vp,'front_face':'clockwise','positive_viewport_height':True}
 commands=[]
 wanted=set() if args.skip_base else {'L_Pokemon_Window_Image','L_Pokemon_Full_EnergyType_BG','Card_F','L_UI_Panel_Pokemon'}
 for obj in scene['objects']:
  if obj['name'] not in wanted:continue
  rend=obj['renderer'];mesh=meshes[rend['mesh']]
  slot=0;mid=rend['material_slots'][slot];mat=materials[mid]
  kind='illust' if mat['shader_id'].endswith('__6715688836837633753') else 'frame'
  tid=mat['textures'][0]['texture_id'];view,tex=upload_texture(tid)
  verts=[]
  for i,pos in enumerate(mesh['vertices']):
   uv0=mesh['uv0'][i] if mesh['uv0'] else [0,0];uv1=mesh['uv1'][i] if mesh['uv1'] else uv0
   verts.extend(pos+[1]+uv0[:2]+uv1[:2])
  vbuf,_=buffer(data=struct.pack('<'+'f'*len(verts),*verts),usage=vk.VK_BUFFER_USAGE_VERTEX_BUFFER_BIT)
  sub=mesh['submeshes'][slot];indices=sub['indices'] if 'indices'in sub else [v+sub['base_vertex'] for v in sub['raw_indices']]
  ibuf,_=buffer(data=struct.pack('<'+'I'*len(indices),*indices),usage=vk.VK_BUFFER_USAGE_INDEX_BUFFER_BIT)
  matrix_bytes=struct.pack('<32f',*(columns(obj['world_matrix'])+columns(vp)))
  ubov,_=buffer(data=matrix_bytes+(struct.pack('<i',int(mat['floats'].get('_UseUv',0)))+bytes(12) if kind=='illust' else b''),usage=vk.VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT)
  pbindings=[vk.VkDescriptorSetLayoutBinding(binding=0,descriptorType=vk.VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,descriptorCount=1,stageFlags=vk.VK_SHADER_STAGE_VERTEX_BIT if kind=='illust' else vk.VK_SHADER_STAGE_FRAGMENT_BIT)]
  if kind=='frame':pbindings.append(vk.VkDescriptorSetLayoutBinding(binding=1,descriptorType=vk.VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,descriptorCount=1,stageFlags=vk.VK_SHADER_STAGE_VERTEX_BIT))
  uniform_layout=vk.vkCreateDescriptorSetLayout(device,vk.VkDescriptorSetLayoutCreateInfo(pBindings=pbindings),None)
  pipeline_layout=vk.vkCreatePipelineLayout(device,vk.VkPipelineLayoutCreateInfo(pSetLayouts=[tex_layout,uniform_layout]),None)
  sets=vk.vkAllocateDescriptorSets(device,vk.VkDescriptorSetAllocateInfo(descriptorPool=descriptor_pool,pSetLayouts=[tex_layout,uniform_layout]))
  writes=[vk.VkWriteDescriptorSet(dstSet=sets[0],dstBinding=0,descriptorCount=1,descriptorType=vk.VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,pImageInfo=[vk.VkDescriptorImageInfo(sampler=sampler,imageView=view,imageLayout=vk.VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL)])]
  if kind=='illust':writes.append(vk.VkWriteDescriptorSet(dstSet=sets[1],dstBinding=0,descriptorCount=1,descriptorType=vk.VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,pBufferInfo=[vk.VkDescriptorBufferInfo(buffer=ubov,offset=0,range=144)]))
  else:
   ubof,_=buffer(data=struct.pack('<f',mat['floats'].get('_EmitMasking',0))+bytes(12),usage=vk.VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT)
   writes.extend([vk.VkWriteDescriptorSet(dstSet=sets[1],dstBinding=0,descriptorCount=1,descriptorType=vk.VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,pBufferInfo=[vk.VkDescriptorBufferInfo(buffer=ubof,offset=0,range=16)]),vk.VkWriteDescriptorSet(dstSet=sets[1],dstBinding=1,descriptorCount=1,descriptorType=vk.VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,pBufferInfo=[vk.VkDescriptorBufferInfo(buffer=ubov,offset=0,range=128)])])
  vk.vkUpdateDescriptorSets(device,len(writes),writes,0,None)
  sm=modules(kind)
  stages=[vk.VkPipelineShaderStageCreateInfo(stage=stage,module=sm[model],pName='main') for stage,model in [(vk.VK_SHADER_STAGE_VERTEX_BIT,0),(vk.VK_SHADER_STAGE_FRAGMENT_BIT,4)]]
  attrs=[vk.VkVertexInputAttributeDescription(location=0,binding=0,format=vk.VK_FORMAT_R32G32B32A32_SFLOAT,offset=0),vk.VkVertexInputAttributeDescription(location=1,binding=0,format=vk.VK_FORMAT_R32G32_SFLOAT,offset=16)]
  if kind=='illust':attrs.append(vk.VkVertexInputAttributeDescription(location=2,binding=0,format=vk.VK_FORMAT_R32G32_SFLOAT,offset=24))
  vi=vk.VkPipelineVertexInputStateCreateInfo(pVertexBindingDescriptions=[vk.VkVertexInputBindingDescription(binding=0,stride=32,inputRate=vk.VK_VERTEX_INPUT_RATE_VERTEX)],pVertexAttributeDescriptions=attrs)
  ia=vk.VkPipelineInputAssemblyStateCreateInfo(topology=vk.VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST)
  viewport=vk.VkPipelineViewportStateCreateInfo(pViewports=[vk.VkViewport(x=0,y=0,width=W,height=H,minDepth=0,maxDepth=1)],pScissors=[vk.VkRect2D(offset=vk.VkOffset2D(0,0),extent=vk.VkExtent2D(W,H))])
  raster=vk.VkPipelineRasterizationStateCreateInfo(polygonMode=vk.VK_POLYGON_MODE_FILL,cullMode=vk.VK_CULL_MODE_BACK_BIT,frontFace=vk.VK_FRONT_FACE_CLOCKWISE,lineWidth=1.0)
  ms=vk.VkPipelineMultisampleStateCreateInfo(rasterizationSamples=vk.VK_SAMPLE_COUNT_1_BIT)
  stencil=vk.VkStencilOpState(failOp=vk.VK_STENCIL_OP_KEEP,passOp=vk.VK_STENCIL_OP_KEEP,depthFailOp=vk.VK_STENCIL_OP_KEEP,compareOp=vk.VK_COMPARE_OP_EQUAL if kind=='illust' else vk.VK_COMPARE_OP_ALWAYS,compareMask=0,writeMask=0,reference=0)
  depth=vk.VkPipelineDepthStencilStateCreateInfo(depthTestEnable=bool(mat['floats'].get('_ZTest',0)),depthWriteEnable=bool(mat['floats'].get('_ZWrite',0)),depthCompareOp=vk.VK_COMPARE_OP_LESS_OR_EQUAL,stencilTestEnable=True,front=stencil,back=stencil,minDepthBounds=0,maxDepthBounds=1)
  blend=vk.VkPipelineColorBlendAttachmentState(blendEnable=True,srcColorBlendFactor=vk.VK_BLEND_FACTOR_SRC_ALPHA if kind=='illust' else vk.VK_BLEND_FACTOR_ONE,dstColorBlendFactor=vk.VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA,colorBlendOp=vk.VK_BLEND_OP_ADD,srcAlphaBlendFactor=vk.VK_BLEND_FACTOR_ZERO,dstAlphaBlendFactor=vk.VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA,alphaBlendOp=vk.VK_BLEND_OP_ADD,colorWriteMask=15)
  blendstate=vk.VkPipelineColorBlendStateCreateInfo(pAttachments=[blend,blend])
  pipeline=vk.vkCreateGraphicsPipelines(device,vk.VK_NULL_HANDLE,1,[vk.VkGraphicsPipelineCreateInfo(pStages=stages,pVertexInputState=vi,pInputAssemblyState=ia,pViewportState=viewport,pRasterizationState=raster,pMultisampleState=ms,pDepthStencilState=depth,pColorBlendState=blendstate,layout=pipeline_layout,renderPass=renderpass,subpass=0)],None)[0]
  commands.append((mat['custom_render_queue'],pipeline,pipeline_layout,sets,vbuf,ibuf,len(indices)))
  report['draws'].append({'object':obj['name'],'mesh_id':rend['mesh'],'submesh_slot':slot,'index_count':len(indices),'material_id':mid,'material':mat['name'],'shader_kind':kind,'queue':mat['custom_render_queue'],'texture':tex['name'],'source_png':tex['png'],'source_png_sha256':hashlib.sha256((ASSET/tex['png']).read_bytes()).hexdigest(),'source_dimensions':[tex['width'],tex['height']],'world_matrix_unchanged':True,'use_uv':mat['floats'].get('_UseUv')})
 # Optional original-shader passes. Geometry, UBO bytes and bindings are supplied by
 # an evidence-producing caller; this harness never implements shader mathematics.
 if args.additional_draws:
  manifest_path=args.additional_draws.resolve()
  extra=json.loads(manifest_path.read_text(encoding='utf-8'))
  def resolve_extra(path):return (manifest_path.parent/path).resolve()
  def vkname(prefix,name):return getattr(vk,prefix+name.upper())
  formats={'float2':vk.VK_FORMAT_R32G32_SFLOAT,'float3':vk.VK_FORMAT_R32G32B32_SFLOAT,'float4':vk.VK_FORMAT_R32G32B32A32_SFLOAT,'unorm8x4':vk.VK_FORMAT_R8G8B8A8_UNORM}
  stage_bits={'vertex':vk.VK_SHADER_STAGE_VERTEX_BIT,'fragment':vk.VK_SHADER_STAGE_FRAGMENT_BIT}
  report['additional_manifest']={'file':str(manifest_path),'sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),'provenance':extra.get('provenance')}
  if 'limits' in extra:report['limits'].extend(extra['limits'])
  if extra.get('texture_upload_probes') and not args.verify_raw_texture_upload:
   raise ValueError('Texture upload probes require explicit --verify-raw-texture-upload')
  for probe in extra.get('texture_upload_probes',[]):
   if probe.get('image_format')!='r16-unorm':raise ValueError('Transport probes require an explicit raw R16 format')
   upload_extra_texture(resolve_extra(probe['file']),probe)
  for draw in extra['draws']:
   raw_vertices=resolve_extra(draw['vertices']['file']).read_bytes()
   raw_indices=resolve_extra(draw['indices']['file']).read_bytes()
   vbuf,_=buffer(data=raw_vertices,usage=vk.VK_BUFFER_USAGE_VERTEX_BUFFER_BIT)
   ibuf,_=buffer(data=raw_indices,usage=vk.VK_BUFFER_USAGE_INDEX_BUFFER_BIT)
   assert len(raw_indices)%4==0, 'Index data is little-endian uint32 only'
   attrs=[vk.VkVertexInputAttributeDescription(location=a['location'],binding=0,format=formats[a['format']],offset=a['offset']) for a in draw['vertices']['attributes']]
   vi=vk.VkPipelineVertexInputStateCreateInfo(pVertexBindingDescriptions=[vk.VkVertexInputBindingDescription(binding=0,stride=draw['vertices']['stride'],inputRate=vk.VK_VERTEX_INPUT_RATE_VERTEX)],pVertexAttributeDescriptions=attrs)
   bindings=draw['bindings'];nsets=max(b['set'] for b in bindings)+1
   layouts=[]
   for iset in range(nsets):
    bs=[]
    for b in bindings:
     if b['set']!=iset:continue
     dtype=vk.VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER if b['type']=='uniform_buffer' else vk.VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER
     flags=sum(stage_bits[s] for s in b.get('stages',['fragment']))
     bs.append(vk.VkDescriptorSetLayoutBinding(binding=b['binding'],descriptorType=dtype,descriptorCount=1,stageFlags=flags))
    layouts.append(vk.vkCreateDescriptorSetLayout(device,vk.VkDescriptorSetLayoutCreateInfo(pBindings=bs),None))
   pipeline_layout=vk.vkCreatePipelineLayout(device,vk.VkPipelineLayoutCreateInfo(pSetLayouts=layouts),None)
   sets=vk.vkAllocateDescriptorSets(device,vk.VkDescriptorSetAllocateInfo(descriptorPool=descriptor_pool,pSetLayouts=layouts))
   writes=[];binding_report=[]
   for b in bindings:
    path=resolve_extra(b['file']);raw=path.read_bytes()
    if b['type']=='uniform_buffer':
     ub,_=buffer(data=raw,usage=vk.VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT)
     writes.append(vk.VkWriteDescriptorSet(dstSet=sets[b['set']],dstBinding=b['binding'],descriptorCount=1,descriptorType=vk.VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,pBufferInfo=[vk.VkDescriptorBufferInfo(buffer=ub,offset=0,range=len(raw))]))
    else:
     face_report=None
     if b['type']=='combined_image_sampler_cube':view,face_report=upload_cube(path)
     else:
      view,_=upload_extra_texture(path,b)
     selected_sampler=clamp_samplers[b.get('sampler','nearest')] if b.get('address_mode')=='clamp-to-edge' else linear_sampler if b.get('sampler')=='linear' else sampler
     writes.append(vk.VkWriteDescriptorSet(dstSet=sets[b['set']],dstBinding=b['binding'],descriptorCount=1,descriptorType=vk.VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,pImageInfo=[vk.VkDescriptorImageInfo(sampler=selected_sampler,imageView=view,imageLayout=vk.VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL)]))
    binding_report.append({'set':b['set'],'binding':b['binding'],'type':b['type'],'file':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'sampler':b.get('sampler'),'address_mode':b.get('address_mode','repeat')})
    if b['type']=='combined_image_sampler_cube':binding_report[-1]['faces']=face_report
    if b.get('image_format'):
     binding_report[-1].update(image_format=b['image_format'],image_size=b.get('image_size'),row_origin=b.get('row_origin'))
   vk.vkUpdateDescriptorSets(device,len(writes),writes,0,None)
   stages=[]
   for stage in ('vertex','fragment'):
    path=resolve_extra(draw['shaders'][stage]);raw=path.read_bytes()
    sm=vk.vkCreateShaderModule(device,vk.VkShaderModuleCreateInfo(codeSize=len(raw),pCode=raw),None)
    stages.append(vk.VkPipelineShaderStageCreateInfo(stage=stage_bits[stage],module=sm,pName='main'))
    report['shader_sources'].append({'file':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'execution_model':0 if stage=='vertex' else 4,'modified':False})
   state=draw.get('state',{})
   ia=vk.VkPipelineInputAssemblyStateCreateInfo(topology=vk.VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST)
   viewport=vk.VkPipelineViewportStateCreateInfo(pViewports=[vk.VkViewport(x=0,y=0,width=W,height=H,minDepth=0,maxDepth=1)],pScissors=[vk.VkRect2D(offset=vk.VkOffset2D(0,0),extent=vk.VkExtent2D(W,H))])
   bias=state.get('depth_bias',{})
   raster=vk.VkPipelineRasterizationStateCreateInfo(polygonMode=vk.VK_POLYGON_MODE_FILL,cullMode=vkname('VK_CULL_MODE_',state.get('cull','none')) if state.get('cull','none')=='none' else vkname('VK_CULL_MODE_',state['cull']+'_bit'),frontFace=vkname('VK_FRONT_FACE_',state.get('front_face','clockwise')),lineWidth=1.0,depthBiasEnable=bool(bias),depthBiasConstantFactor=bias.get('constant',0),depthBiasSlopeFactor=bias.get('slope',0),depthBiasClamp=bias.get('clamp',0))
   ms=vk.VkPipelineMultisampleStateCreateInfo(rasterizationSamples=vk.VK_SAMPLE_COUNT_1_BIT)
   stencil_state=state.get('stencil',{})
   def stencil_face(face):
    spec=stencil_state.get(face,stencil_state.get('front',{}))
    return vk.VkStencilOpState(failOp=vkname('VK_STENCIL_OP_',spec.get('fail','keep')),passOp=vkname('VK_STENCIL_OP_',spec.get('pass','keep')),depthFailOp=vkname('VK_STENCIL_OP_',spec.get('depth_fail','keep')),compareOp=vkname('VK_COMPARE_OP_',spec.get('compare','always')),compareMask=stencil_state.get('read_mask',255),writeMask=stencil_state.get('write_mask',255),reference=stencil_state.get('reference',0))
   depth=vk.VkPipelineDepthStencilStateCreateInfo(depthTestEnable=state.get('depth_test',False),depthWriteEnable=state.get('depth_write',False),depthCompareOp=vkname('VK_COMPARE_OP_',state.get('depth_compare','always')),stencilTestEnable=stencil_state.get('enabled',False),front=stencil_face('front'),back=stencil_face('back'),minDepthBounds=0,maxDepthBounds=1)
   bs=state.get('blend',{})
   blend=vk.VkPipelineColorBlendAttachmentState(blendEnable=bs.get('enabled',True),srcColorBlendFactor=vkname('VK_BLEND_FACTOR_',bs.get('src_color','one')),dstColorBlendFactor=vkname('VK_BLEND_FACTOR_',bs.get('dst_color','one_minus_src_alpha')),colorBlendOp=vkname('VK_BLEND_OP_',bs.get('color_op','add')),srcAlphaBlendFactor=vkname('VK_BLEND_FACTOR_',bs.get('src_alpha','one')),dstAlphaBlendFactor=vkname('VK_BLEND_FACTOR_',bs.get('dst_alpha','one_minus_src_alpha')),alphaBlendOp=vkname('VK_BLEND_OP_',bs.get('alpha_op','add')),colorWriteMask=state.get('color_write_mask',15))
   blendstate=vk.VkPipelineColorBlendStateCreateInfo(pAttachments=[blend,blend])
   pipeline=vk.vkCreateGraphicsPipelines(device,vk.VK_NULL_HANDLE,1,[vk.VkGraphicsPipelineCreateInfo(pStages=stages,pVertexInputState=vi,pInputAssemblyState=ia,pViewportState=viewport,pRasterizationState=raster,pMultisampleState=ms,pDepthStencilState=depth,pColorBlendState=blendstate,layout=pipeline_layout,renderPass=renderpass,subpass=0)],None)[0]
   commands.append((draw.get('queue',3000),pipeline,pipeline_layout,sets,vbuf,ibuf,len(raw_indices)//4))
   report['draws'].append({'object':draw['name'],'queue':draw.get('queue',3000),'index_count':len(raw_indices)//4,'bindings':binding_report,'geometry_provenance':draw.get('provenance'),'state':state,'vertex_sha256':hashlib.sha256(raw_vertices).hexdigest(),'index_sha256':hashlib.sha256(raw_indices).hexdigest()})
 cmd=begin()
 report['draw_execution_order']=[d['object'] for d in sorted(report['draws'],key=lambda d:d['queue'])]
 clears=[vk.VkClearValue(color=vk.VkClearColorValue(float32=args.clear)),vk.VkClearValue(color=vk.VkClearColorValue(float32=[0,0,0,0])),vk.VkClearValue(depthStencil=vk.VkClearDepthStencilValue(depth=1,stencil=0))]
 vk.vkCmdBeginRenderPass(cmd,vk.VkRenderPassBeginInfo(renderPass=renderpass,framebuffer=framebuffer,renderArea=vk.VkRect2D(offset=vk.VkOffset2D(0,0),extent=vk.VkExtent2D(W,H)),pClearValues=clears),vk.VK_SUBPASS_CONTENTS_INLINE)
 for _,pipeline,layout,sets,vbuf,ibuf,nindices in sorted(commands,key=lambda x:x[0]):
  vk.vkCmdBindPipeline(cmd,vk.VK_PIPELINE_BIND_POINT_GRAPHICS,pipeline)
  vk.vkCmdBindDescriptorSets(cmd,vk.VK_PIPELINE_BIND_POINT_GRAPHICS,layout,0,len(sets),sets,0,None)
  vk.vkCmdBindVertexBuffers(cmd,0,1,[vbuf],[0]);vk.vkCmdBindIndexBuffer(cmd,ibuf,0,vk.VK_INDEX_TYPE_UINT32)
  vk.vkCmdDrawIndexed(cmd,nindices,1,0,0,0)
 vk.vkCmdEndRenderPass(cmd)
 readbuf,readmem=buffer(size=W*H*4,usage=vk.VK_BUFFER_USAGE_TRANSFER_DST_BIT)
 region=vk.VkBufferImageCopy(bufferOffset=0,bufferRowLength=0,bufferImageHeight=0,imageSubresource=vk.VkImageSubresourceLayers(aspectMask=vk.VK_IMAGE_ASPECT_COLOR_BIT,mipLevel=0,baseArrayLayer=0,layerCount=1),imageOffset=vk.VkOffset3D(0,0,0),imageExtent=vk.VkExtent3D(W,H,1))
 vk.vkCmdCopyImageToBuffer(cmd,color_images[0][0],vk.VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,readbuf,1,[region]);submit(cmd)
 mapped=vk.vkMapMemory(device,readmem,0,W*H*4,0);pixels=bytes(mapped);vk.vkUnmapMemory(device,readmem)
 image_out=Image.frombytes('RGBA',(W,H),pixels)
 if not args.rgba:image_out=image_out.convert('RGB')
 dest=args.output.resolve() if args.output else OUT/'bulbasaur-original-shaders-base.png';image_out.save(dest)
 report['status']='original shader passes executed; incomplete card' if args.additional_draws else 'original shader base executed; incomplete card'
 report['attachment_readback']={'format':image_out.mode,'clear':args.clear,'color_attachments':2}
 report['output']=str(dest);report['output_sha256']=hashlib.sha256(dest.read_bytes()).hexdigest();report['pixel_extrema']=image_out.getextrema()
 print(json.dumps({'status':report['status'],'output':str(dest),'adapter':report['adapter'],'pixel_extrema':report['pixel_extrema']},indent=2))
except Exception as exc:
 report['status']='blocked';report['error_type']=type(exc).__name__;report['error']=str(exc);report['traceback']=traceback.format_exc();print(report['traceback'])
finally:
 save_report()
 if device is not None:vk.vkDeviceWaitIdle(device);vk.vkDestroyDevice(device,None)
 if inst is not None:vk.vkDestroyInstance(inst,None)
if report['status']=='blocked':raise SystemExit(1)
