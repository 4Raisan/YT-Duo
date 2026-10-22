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
s+=card(864,242,368,126,'EXTERNAL MEDIA ACCESS','yt-dlp → media providers',['Fresh metadata + stream downloads','YouTube challenges → EJS + Deno'])
s+=card(48,422,296,110,'cli.py','Terminal interface',['Arguments, URLs and console events'],DIM)
s+=card(48,584,296,110,'config.py','Shared settings',['Private paths and preferences'],DIM)
s+=rect(432,434,324,244,'#2b171c','#a83344')
s+=text(454,465,'engine.py + selector.py',11,RED,700,spacing=1.2)
s+=text(454,502,'Shared download engine',21,WHITE,650)
for y,label in [(539,'Sequential queue + retries'),(568,'Rank video or music streams'),(597,'Dispatch download / processing'),(626,'Record exact-variant success')]: s+=text(454,y,label,15,BODY)
s+=text(454,655,'naming.py → safe output names',13,DIM)
s+=card(864,448,368,112,'EXTERNAL PROCESS','FFmpeg',['Video merge · audio copy / MP3'])
s+=card(864,610,368,112,'EXTERNAL PROCESS','FFprobe',['Validate streams, codec and duration'])
s+=card(48,774,296,192,'GUI BACKGROUND SERVICES','Preview + updates',[],CORAL)
s+=text(70,863,'Preview → engine.probe()',15,BODY)+text(70,889,'Update → GitHub + SHA-256',14,BODY)+text(70,925,'New version → update + cleanup.',13,DIM)+text(70,947,'Shell helper → folder / browser.',12,DIM)
s+=card(432,804,800,162,'LOCAL STORAGE','Verified media + application state',[],RED)
s+=text(454,895,'Media: chosen folder / Audio · State: private .ytduo/',16,BODY)
s+=text(454,927,'Verified sources removed · private recycle cleared on launch.',15,DIM)
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
s+=text(48,1009,'Windows: bundled Python, media tools + Deno. Unix: source + installed tools.',14,DIM)
(OUT/'architecture.svg').write_text(svg(1280,1050,'YT-Duo application architecture','The Tkinter GUI offers verified-paste Auto download and mutually exclusive Options and Terminal panels, using a worker and event bridge; the CLI calls the same engine. Shared settings and selectors drive yt-dlp access, FFmpeg processing, FFprobe validation and local completion records. Separate GUI services probe metadata and check GitHub releases automatically; a user-initiated updater verifies SHA-256 assets, replaces the launcher, restarts and removes inactive private artifacts while preserving settings, active runtimes and media. Windows layout fits the current monitor across DPI scales. Dashed arrows show service or event paths.',s),encoding='utf-8')

# Download workflow, showing the codec-copy / MP3 difference explicitly.
s=base(1240,'DOWNLOAD WORKFLOW','From a link to verified media.','Video keeps its encoded streams. Music can preserve the source codec or convert to MP3.')
s+=card(48,242,352,124,'01 / FRESH METADATA','Probe with yt-dlp',['Resolve selected streams and container.'])
s+=card(464,242,352,124,'02 / EXACT REPEATS','Check matching output',['Match streams, format and quality.','GUI: Again or Skip; 10s → Skip.'])
s+=card(880,242,352,124,'03 / DOWNLOAD TYPE','Choose Video or Music',['Use the chosen mode and quality.'])
s+=arrow('M400 304H464')+arrow('M816 304H880')
s+=arrow('M1056 366V403H320V448')+arrow('M1056 403H960V448',CORAL)
s+=rect(48,448,544,296,'#2b171c','#a83344')+rect(688,448,544,296,'#2b201d','#a3634d')
s+=text(76,487,'VIDEO',13,RED,750,spacing=2)+text(716,487,'MUSIC',13,CORAL,750,spacing=2)
s+=text(76,522,'Best quality · Smaller file · Most compatible',17,WHITE,600)
s+=text(716,522,'Original quality or MP3',17,WHITE,600)
for x in (76,716): s+=f'<path d="M{x} 543h488" stroke="{LINE}"/>'
for x,lines in [(76,[(578,'1  Select video + audio streams',WHITE),(605,'Use the resolution cap when offered.',BODY),(651,'2  Download the selected streams',WHITE),(696,'3  FFmpeg merge with stream copy',WHITE),(722,'No video or audio re-encoding.',DIM)]),(716,[(578,'1  Select audio-only; muxed fallback',WHITE),(605,'Download the source and keep only audio.',BODY),(651,'2  Copy original codec or encode MP3',WHITE),(696,'3  Best VBR or a fixed MP3 bitrate',WHITE),(722,'128 / 192 / 256 / 320 kbps',DIM)])]:
    for y,label,color in lines:s+=text(x,y,label,16,color,600 if color==WHITE else 400)
s+=f'<path d="M320 744v44h640v-44" fill="none" stroke="{RED}" stroke-width="2"/>'
s+=f'<circle cx="640" cy="788" r="4" fill="{RED}"/>'+arrow('M640 788V832')
s+=card(432,832,416,142,'04 / VERIFY','Inspect output with FFprobe',[])
s+=text(454,925,'Streams, codec and positive duration',15,BODY)+text(454,951,'Music: no video; check fixed MP3 bitrate',14,DIM)
s+=card(48,832,320,142,'IF VALIDATION FAILS','Report the failure',[], '#f29999')
s+=text(70,925,'Remove the invalid final output.',15,BODY)+text(70,951,'Do not record successful completion.',14,DIM)
s+=arrow('M432 895H368','#f29999')
s+=card(912,832,320,142,'PREVIEW ≠ DOWNLOAD','Probe again when starting',[],CORAL)
s+=text(934,925,'The input scan is advisory.',15,BODY)+text(934,951,'Download uses fresh stream URLs.',14,DIM)
s+=f'<path d="M640 974v30H320v40M640 1004h320v40" fill="none" stroke="{RED}" stroke-width="2" marker-end="url(#accent)"/>'
s+=arrow('M320 1028V1044')
s+=card(48,1044,544,118,'05 / KEEP THE RESULT','Keep verified media',['Videos in base folder · music in Audio/.'])
s+=card(688,1044,544,118,'06 / RECORD COMPLETION','Save exact-variant history',['Verified outputs only · private .ytduo/history/.'])
s+=text(48,1206,'Item failures do not stop the queue. Cancellation stops the batch. No upscaling or source-quality enhancement.',14,DIM)
(OUT/'download-flow.svg').write_text(svg(1280,1240,'YT-Duo video and music download workflow','Queue items are probed fresh with yt-dlp, checked for an existing exact selected variant, and routed to video or music. Video merges with stream copy. Music copies its original codec or encodes MP3. FFprobe verifies the result before completion is recorded. Invalid verified outputs are removed; preview metadata is not reused for the download.',s),encoding='utf-8')
print('Created architecture and workflow SVG assets in',OUT)
