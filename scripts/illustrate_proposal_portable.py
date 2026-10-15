"""Illustrate the proposal with an attributed photo and original design diagrams."""
from pathlib import Path
import re,json,shutil,hashlib
from copy import deepcopy
from PIL import Image
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle,FancyArrowPatch
from docx import Document
from docx.shared import Mm,Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

ROOT=Path(__file__).resolve().parents[1]
ASSET=ROOT/'output/researchwrite/t063_illustrated_proposal'
RAW=ROOT/'data/raw/t063_figures'
SOURCE=Path('/Volumes/out/Projects/开题报告/开题报告_便携式样机与协同控制修订版.docx')
OUTPUT=Path('/Volumes/out/Projects/开题报告/开题报告_便携样机图文完善版.docx')
FONT=Path('/Volumes/out/Projects/开题报告/tmp/fonts/NotoSansCJKsc-Regular.otf')
font_manager.fontManager.addfont(str(FONT))
mpl.rcParams.update({'font.family':'sans-serif','font.sans-serif':[font_manager.FontProperties(fname=str(FONT)).get_name(),'Arial','DejaVu Sans'],
 'font.size':8,'pdf.fonttype':42,'svg.fonttype':'none','axes.linewidth':.6})
INK='#243746';EDGE='#526575';BLUE='#eaf1f6';TEAL='#e8f2ef';GRAY='#f2f3f4'

def canvas(h):
 f=plt.figure(figsize=(160/25.4,h/25.4));a=f.add_axes([0,0,1,1]);a.set_xlim(0,160);a.set_ylim(0,h);a.axis('off');return f,a
def box(a,x,y,w,h,s,color=BLUE,size=8):
 a.add_patch(Rectangle((x,y),w,h,facecolor=color,edgecolor=EDGE,linewidth=.65))
 a.text(x+w/2,y+h/2,s,ha='center',va='center',fontsize=size,color=INK,linespacing=1.6)
def arrow(a,start,end,label=None,bi=False,style='-'):
 a.add_patch(FancyArrowPatch(start,end,arrowstyle='<->' if bi else '-|>',mutation_scale=7,linewidth=.7,color=EDGE,linestyle=style))
 if label:a.text((start[0]+end[0])/2+2,(start[1]+end[1])/2,label,ha='left',va='center',fontsize=7,color=INK)
def save(f,name):
 f.savefig(ASSET/f'{name}.png',dpi=600,facecolor='white')
 f.savefig(ASSET/f'{name}.svg',facecolor='white')
 f.savefig(ASSET/f'{name}.pdf',facecolor='white')
 plt.close(f)

def figures():
 ASSET.mkdir(parents=True,exist_ok=True)
 im=Image.open(RAW/'highres-4.png');w,h=im.size
 # Crop only the published photo (page 98, Fig. 5); no contrast or content edits.
 crop=(int(w*679/1273),int(h*771/1800),int(w*1164/1273),int(h*1134/1800))
 im.crop(crop).save(ASSET/'mobile_machine_reference.png')
 f,a=canvas(119)
 box(a,2,99,156,17,'管理界面  接入配置核对 / 作业监视 / 告警与历史追溯',GRAY)
 box(a,2,72,74,18,'云端控制服务\n时间戳预测 / 配方与模型管理')
 box(a,84,72,74,18,'数据与任务服务\n设备—接入点—任务 / 版本与事件',GRAY)
 arrow(a,(39,99),(39,90),bi=True);arrow(a,(121,99),(121,90),bi=True)
 box(a,2,54,156,10,'MQTT 3.1.1 / QoS 1   时间戳 · 任务标识 · 序号',GRAY)
 arrow(a,(39,72),(39,64),bi=True);arrow(a,(121,72),(121,64),bi=True)
 box(a,2,26,48,19,'边缘接入管理\n配置检查 / 任务初始化',TEAL)
 box(a,56,26,48,19,'边缘监督与控制\n双风险 / 三模式 / 公共PI',TEAL)
 box(a,110,26,48,19,'命令与设备适配\n幂等 / 陈旧拒绝 / 执行反馈',TEAL)
 arrow(a,(26,54),(26,45),bi=True);arrow(a,(80,54),(80,45),bi=True);arrow(a,(134,54),(134,45),bi=True)
 arrow(a,(50,35.5),(56,35.5));arrow(a,(104,35.5),(110,35.5))
 box(a,2,1,48,16,'状态采集\nEC / 温度 / 流量压力 / 液位',GRAY,size=7.5)
 box(a,56,1,48,16,'混肥与输配对象\n供水 / 母液 / 混合与管路',GRAY,size=7.5)
 box(a,110,1,48,16,'注肥执行与原机联锁\n接口动作 / 压力液位 / 人工停止',GRAY,size=7.5)
 a.plot([26,26,80],[17,21,21],color=EDGE,linewidth=.7)
 arrow(a,(80,21),(80,26));arrow(a,(134,26),(134,17))
 arrow(a,(110,9),(104,9));arrow(a,(56,9),(50,9))
 f.set_size_inches(160/25.4,90/25.4)
 save(f,'system_architecture')
 f,a=canvas(154)
 a.text(39,149,'a  转场后的新任务初始化',ha='center',fontsize=9,color=INK)
 a.text(120,149,'b  同一任务内的连续作业',ha='center',fontsize=9,color=INK)
 a.plot([79,79],[4,137],color='#c4cdd3',linewidth=.6,linestyle='--')
 steps=[('结束旧任务，停止注肥',125),('清除旧命令、PI状态与模型锚点',103),('绑定接入点、配方和模型版本\n检查传感与设备状态',78),('通水并确认可信观测\n从本地边缘控制启动',49)]
 for s,y in steps:box(a,2,y,73,17 if y!=78 and y!=49 else 22,s,TEAL,size=7.6)
 for y1,y2 in [(125,120),(103,100),(78,71)]:arrow(a,(38.5,y1),(38.5,y2))
 box(a,2,14,73,22,'初始化通过后进入连续监督\n未通过检查则保持停止注肥',GRAY,size=7.6)
 arrow(a,(38.5,49),(38.5,36))
 box(a,85,117,73,20,'采集观测、命令年龄与消息序号\n计算通信风险与观测风险',BLUE,size=7.6)
 box(a,85,83,73,25,'观测风险高 → M2 模型锚定降级\n观测可信、通信风险高 → M1 边缘\n通信与观测恢复正常 → M0 云端',BLUE,size=7.4)
 arrow(a,(121.5,117),(121.5,108))
 box(a,85,49,73,24,'模式切换：滞回 / 驻留 / 恢复确认\nPI积分反算与云边状态同步\n共用饱和、抗饱和及动作变化率约束',TEAL,size=7.2)
 arrow(a,(121.5,83),(121.5,73))
 box(a,85,14,73,24,'设备适配执行，采集EC与动作反馈\n记录模式、命令与事件\n持续监督至任务结束',GRAY,size=7.5)
 arrow(a,(121.5,49),(121.5,38))
 a.plot([156,159,159,156],[26,26,127,127],color=EDGE,linewidth=.7)
 arrow(a,(159,127),(158,127))
 a.plot([75,81,81],[25,25,127],color=EDGE,linewidth=.7)
 arrow(a,(81,127),(85,127))
 a.text(80,2,'监督与任务管理均受原机联锁及人工停止通道约束',ha='center',fontsize=7,color=INK)
 save(f,'task_control_flow')
 return crop,im.size

def after(p,text='',image=None,width=132):
 el=OxmlElement('w:p');p._p.addnext(el);n=Paragraph(el,p._parent)
 n.paragraph_format.line_spacing=1.15;n.paragraph_format.space_before=Pt(4);n.paragraph_format.space_after=Pt(5)
 n.paragraph_format.first_line_indent=Mm(0);n.paragraph_format.widow_control=True
 n.paragraph_format.left_indent=Mm(0);n.paragraph_format.right_indent=Mm(0)
 n.paragraph_format.keep_together=True
 grid=OxmlElement('w:snapToGrid');grid.set(qn('w:val'),'0');n._p.get_or_add_pPr().append(grid)
 if image:
  n.alignment=WD_ALIGN_PARAGRAPH.CENTER;n.paragraph_format.keep_with_next=True
  r=n.add_run();r.add_picture(str(image),width=Mm(width));
  for docpr in el.xpath('.//wp:docPr'):docpr.set('descr',text)
 else:
  n.alignment=WD_ALIGN_PARAGRAPH.CENTER
  r=n.add_run(text);r.font.size=Pt(10);r.font.name='Times New Roman';r._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'宋体')
 return n
def find(d,s):
 for t in d.tables:
  for row in t.rows:
   for c in row.cells:
    for p in c.paragraphs:
     if p.text.startswith(s):return p
 raise ValueError(s)
def main():
 crop,rawsize=figures();d=Document(SOURCE)
 p=find(d,'农业数字孪生部署研究')
 p.add_run('图1给出文献中的牵引式移动装备示例，用于说明移动水肥装备的集成形态。')
 p=after(p,'移动式果园灌溉施肥机文献示例',ASSET/'mobile_machine_reference.png',100)
 p=after(p,'图1  移动式果园灌溉施肥机示例（引自文献[37]图5）')
 p=after(p,'来源：左志宇等，2023。图示为文献装备，不代表本课题样机。')
 for r in p.runs:r.font.size=Pt(9)
 p=find(d,'系统总体架构由样机感知与执行');p.add_run('系统分层及数据、命令关系如图2所示。')
 p=after(p,'拟设计系统总体架构',ASSET/'system_architecture.png',122)
 after(p,'图2  便携式水肥一体机物联网协同管控系统总体架构（自绘）')
 p=find(d,'图1  便携式水肥一体机协同管控系统技术路线图')
 p.runs[0].text=p.runs[0].text.replace('图1','图3')
 p=find(d,'监督器负责控制位置');p.add_run('转场初始化与连续作业监督的衔接如图4所示。')
 p=after(p,'转场任务初始化与连续作业监督流程',ASSET/'task_control_flow.png')
 after(p,'图4  转场任务初始化与作业内协同控制流程（自绘）')
 find(d,'三、研究目标').paragraph_format.page_break_before=True
 find(d,'3. 拟解决的关键问题').paragraph_format.keep_with_next=True
 OUTPUT.parent.mkdir(parents=True,exist_ok=True);d.save(OUTPUT)
 shutil.copy2(OUTPUT,ROOT/'output/proposal_revision'/OUTPUT.name)
 texts=[];seen=set()
 for ti,t in enumerate(d.tables):
  for ri,row in enumerate(t.rows):
   for c in row.cells:
    if c._tc in seen:continue
    seen.add(c._tc)
    if ti==4 and ri==15:continue
    texts.extend(p.text.replace('‑','-') for p in c.paragraphs)
 order=[];maximum=0
 for m in re.finditer(r'\[([0-9,-]+)\]','\n'.join(texts)):
  nums=[]
  for x in m[1].split(','):
   if '-' in x:
    lo,hi=map(int,x.split('-'));nums.extend(range(lo,hi+1))
   else:nums.append(int(x))
  maximum=max(maximum,len(nums))
  for n in nums:
   if n not in order:order.append(n)
 assert order==list(range(1,38));assert maximum<=4
 assert len(d.inline_shapes)==3
 from zipfile import ZipFile
 with ZipFile(OUTPUT) as z:assert z.testzip() is None
 audit={'output':str(OUTPUT),'new_figures':3,'figures_total':4,'references':37,'books':11,'max_citation_group':maximum,'first_appearance':order,
 'photo_source':'https://www.aeeisp.com/njhyj/cn/article/pdf/preview/10.13427/j.cnki.njyi.2023.04.008.pdf',
 'photo_location':'印刷页98，原图5','photo_raw_size':rawsize,'photo_crop_pixels':crop,'photo_other_adjustments':None,
 'raw_pdf_sha256':hashlib.sha256((RAW/'mobile_fertigation_2023.pdf').read_bytes()).hexdigest(),
 'original_diagram_contract':'拟设计架构和流程，不含结果或未经测量的样机参数；160mm宽；字体至少7pt；Python绘制；SVG/PDF文字可编辑',
 'diagram_statistics':'不适用，均为方案示意图'}
 (ASSET/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(audit,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
