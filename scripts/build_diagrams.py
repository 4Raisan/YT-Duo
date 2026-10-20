"""Local authoring helper; the checked-in assets are editable, self-contained SVG."""
from pathlib import Path
from html import escape
import base64

OUT = Path(__file__).resolve().parents[1] / 'docs/assets'
BG, PANEL, LINE = '#18191b', '#202426', '#443239'
WHITE, BODY, DIM, RED, CORAL = '#fff5f6', '#d2c5ca', '#b0a0a7', '#ff4052', '#ff9a81'
ICON = '<image width="256" height="256" href="data:image/png;base64,' + base64.b64encode((OUT/'yt-duo-icon.png').read_bytes()).decode('ascii') + '"/>'


def text(x,y,value,size=16,color=BODY,weight=400,anchor=None,spacing=None):
    attrs=f'x="{x}" y="{y}" fill="{color}" font-size="{size}" font-weight="{weight}"'
    if anchor: attrs+=f' text-anchor="{anchor}"'
    if spacing: attrs+=f' letter-spacing="{spacing}"'
    return f'<text {attrs}>{escape(value)}</text>'

def rect(x,y,w,h,fill=PANEL,stroke=LINE,r=16):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" stroke="{stroke}"/>'

def arrow(path,color=RED,dashed=False):
    marker='accent' if color==RED else 'coral' if color==CORAL else 'gray' if color==DIM else 'red'
    return f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"'+(' stroke-dasharray="5 6"' if dashed else '')+f' marker-end="url(#{marker})"/>'

def svg(w,h,title,description,contents,defs=''):
    markers=''
    for name,color in [('accent',RED),('coral',CORAL),('gray',DIM),('red','#f29999')]:
        markers+=f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 10 5 0 10Z" fill="{color}"/></marker>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>
<defs>{markers}{defs}</defs><g font-family="Segoe UI,Arial,sans-serif">{contents}</g></svg>'''

def icon(x,y,size):
    return f'<g transform="translate({x} {y}) scale({size/256})">{ICON}</g>'

def base(h,kind,title,subtitle):
    return rect(1,1,1278,h-2,BG,'#443239',24)+icon(48,36,64)+text(132,66,'YT-Duo',28,WHITE,700)+text(132,94,'Video + music. One download workflow.',15,DIM)+text(1232,68,kind,12,RED,700,'end',2)+text(48,157,title,34,WHITE,650)+text(48,192,subtitle,17,BODY)

def card(x,y,w,h,label,title,lines=(),color=RED):
    s=rect(x,y,w,h)+text(x+22,y+29,label,11,color,700,spacing=1.4)+text(x+22,y+60,title,21,WHITE,650)
    for i,line in enumerate(lines): s+=text(x+22,y+88+25*i,line,15,BODY)
    return s

# System overview: solid connectors are download work; dashed connectors are UI services/events.
s=base(1050,'APPLICATION ARCHITECTURE','One engine. Two ways to download.','Desktop and terminal interfaces share selection, processing and verification.')
s+=text(48,220,'INTERFACES &amp; SETTINGS'.replace('&amp;','&'),11,DIM,700,spacing=1.5)
s+=text(432,220,'SHARED APPLICATION CORE',11,DIM,700,spacing=1.5)
s+=text(864,220,'MEDIA DEPENDENCIES',11,DIM,700,spacing=1.5)
s+=card(48,242,296,126,'gui.py','Desktop / Tkinter',['Auto / live queue · Ⅱ / ▶ / ■','Options or Terminal panel'])
s+=card(432,242,324,126,'gui_worker.py','Worker + event bridge',['Download work off the UI thread','Events return to the Tk event pump'])
s+=card(864,242,368,126,'EXTERNAL MEDIA ACCESS','yt-dlp → media providers',['Fresh metadata + stream downloads','YouTube and supported sources'])
s+=card(48,422,296,110,'cli.py','Terminal interface',['Arguments, URLs and console events'],DIM)
s+=card(48,584,296,110,'config.py','Shared settings',['Paths, modes and local preferences'],DIM)
s+=rect(432,434,324,244,'#2b171c','#a83344')
s+=text(454,465,'engine.py + selector.py',11,RED,700,spacing=1.2)
s+=text(454,502,'Shared download engine',21,WHITE,650)
for y,label in [(539,'Sequential queue + retries'),(568,'Rank video or music streams'),(597,'Dispatch download / processing'),(626,'Record verified completion')]: s+=text(454,y,label,15,BODY)
s+=text(454,655,'naming.py → safe output names',13,DIM)
s+=card(864,448,368,112,'EXTERNAL PROCESS','FFmpeg',['Video merge · audio copy / MP3'])
s+=card(864,610,368,112,'EXTERNAL PROCESS','FFprobe',['Validate streams, codec and duration'])
s+=card(48,774,296,192,'GUI BACKGROUND SERVICES','Preview + updates',[],CORAL)
s+=text(70,863,'Preview → engine.probe()',15,BODY)+text(70,889,'Update check → GitHub release API',14,BODY)+text(70,925,'Results return through UI events.',13,DIM)+text(70,947,'Shell helper → folder / browser.',12,DIM)
s+=card(432,804,800,162,'LOCAL STORAGE','Verified media + application state',[],RED)
s+=text(454,895,'Chosen output folder · config.json · media / format / bitrate archives',16,BODY)
s+=text(454,927,'Verified sources removed · legacy recycle cleared on launch.',15,DIM)
s+=arrow('M344 291H432')+arrow('M432 334H344',DIM,True)
s+=arrow('M594 368V434')+text(609,406,'run_batch()',13,DIM)
s+=arrow('M344 479H432')+arrow('M344 639H432',DIM)
s+=arrow('M756 487H800V306H864')+text(812,410,'probe / fetch',12,DIM)
s+=arrow('M756 548H832V504H864')
s+=arrow('M1048 560V610')+text(1062,590,'output file',12,DIM)
s+=arrow('M864 672H800V647H756',DIM,True)
s+=arrow('M594 678V804')+text(610,750,'verified completion',14,RED)
s+=arrow('M48 323H24V864H48',CORAL,True)
s+=arrow('M344 856H390V663H432',CORAL,True)+text(349,794,'probe',12,CORAL)
s+=text(48,986,'Solid: work / data · Dashed: background services / returned results',12,DIM)
s+=text(48,1009,'Windows: portable EXE + per-user Setup. Unix: embedded source + existing Python.',14,DIM)
(OUT/'architecture.svg').write_text(svg(1280,1050,'YT-Duo application architecture','The Tkinter GUI offers verified-paste Auto download and mutually exclusive Options and Terminal panels, using a worker and event bridge; the CLI calls the same engine. Shared settings and selectors drive yt-dlp access, FFmpeg processing, FFprobe validation and local completion records. Separate GUI services probe metadata and check GitHub releases. Dashed arrows show service or event paths.',s),encoding='utf-8')

