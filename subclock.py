#!/usr/bin/env python3
"""Offline SRT timing review and explicit millisecond shifts."""
import argparse
import html
import json
import math
import re
import sys
from dataclasses import dataclass, asdict, replace
from html.parser import HTMLParser
from pathlib import Path

STAMP = r'([0-9]{2,}):([0-5][0-9]):([0-5][0-9]),([0-9]{3})'
TIMING = re.compile(r'^' + STAMP + r'\s+-->\s+' + STAMP + r'$')

@dataclass(frozen=True)
class Cue:
    number: int
    start: int
    end: int
    text: str


def millis(groups):
    h,m,s,ms=map(int,groups)
    return ((h*60+m)*60+s)*1000+ms


def timestamp(ms):
    if ms<0:raise ValueError('negative timestamp')
    seconds,ms=divmod(ms,1000);minutes,seconds=divmod(seconds,60);hours,minutes=divmod(minutes,60)
    return f'{hours:02}:{minutes:02}:{seconds:02},{ms:03}'


def parse(text):
    text=text.lstrip('\ufeff').replace('\r\n','\n').replace('\r','\n').strip('\n')
    if not text.strip():raise ValueError('empty SRT')
    cues=[]
    for block_number,block in enumerate(re.split(r'\n[ \t]*\n',text),1):
        lines=block.split('\n')
        if len(lines)<2 or not re.fullmatch(r'[0-9]+',lines[0].strip()):
            raise ValueError(f'block {block_number}: expected numeric cue number and timing')
        number=int(lines[0].strip())
        timing=TIMING.fullmatch(lines[1].strip())
        if not timing or number<1:raise ValueError(f'block {block_number}: invalid SRT timing or cue number')
        cues.append(Cue(number,millis(timing.groups()[:4]),millis(timing.groups()[4:]),'\n'.join(lines[2:])))
    return cues


class TextOnly(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
    def handle_data(self,data):self.parts.append(data)


def reading_text(text):
    p=TextOnly();p.feed(text);p.close()
    return re.sub(r'\s+',' ',''.join(p.parts)).strip()


def analyze(cues,max_cps=20.0,min_ms=500,gap_ms=5000):
    findings=[];seen=set();stats=[]
    def add(cue,level,rule,detail):findings.append(dict(cue=cue.number,severity=level,rule=rule,detail=detail))
    for pos,c in enumerate(cues):
        if c.number in seen:add(c,'error','DUPLICATE_NUMBER','Cue number already used')
        seen.add(c.number)
        if pos and c.start<cues[pos-1].start:add(c,'warning','OUT_OF_ORDER','Start is earlier than the preceding file entry')
        duration=c.end-c.start
        visible=reading_text(c.text)
        cps=len(visible)*1000/duration if duration>0 else None
        stats.append(dict(cue=c.number,start_ms=c.start,end_ms=c.end,duration_ms=duration,characters=len(visible),cps=round(cps,2) if cps is not None else None,text=c.text))
        if duration<=0:add(c,'error','INVALID_DURATION','End must be after start')
        elif duration<min_ms:add(c,'warning','SHORT_DURATION',f'Duration {duration} ms is below {min_ms} ms')
        if cps is not None and cps>max_cps:add(c,'warning','FAST_READING',f'{cps:.2f} characters/s exceeds {max_cps:g}')
        if not visible:add(c,'warning','EMPTY_TEXT','No visible text after HTML-style tag stripping')
    # Running maximum detects overlap against long earlier cues, not just neighbors.
    frontier=None
    for c in sorted((c for c in cues if c.end>c.start),key=lambda c:(c.start,c.end,c.number)):
        if frontier:
            if c.start<frontier.end:
                add(c,'warning','OVERLAP',f'Overlaps cue {frontier.number} by {min(c.end,frontier.end)-c.start} ms')
            elif c.start-frontier.end>gap_ms:
                add(c,'info','LONG_GAP',f'{c.start-frontier.end} ms since prior covered interval')
        if frontier is None or c.end>frontier.end:frontier=c
    return dict(format='subclock-report-v1',cue_count=len(cues),errors=sum(f['severity']=='error' for f in findings),warnings=sum(f['severity']=='warning' for f in findings),findings=findings,cues=stats,thresholds=dict(max_cps=max_cps,min_ms=min_ms,gap_ms=gap_ms))


def shift(cues,offset):
    if any(c.start+offset<0 or c.end<=c.start for c in cues):
        raise ValueError('shift would produce negative timing or input has invalid duration')
    if len({c.number for c in cues})!=len(cues):raise ValueError('cannot shift duplicate cue numbers')
    return [replace(c,start=c.start+offset,end=c.end+offset) for c in cues]


def serialize(cues):
    return '\n\n'.join(f'{c.number}\n{timestamp(c.start)} --> {timestamp(c.end)}\n{c.text}' for c in cues)+'\n'


def report_html(report):
    rows=[]
    for f in report['findings']:
        rows.append('<tr>'+''.join('<td>'+html.escape(str(f[k]))+'</td>' for k in ('cue','severity','rule','detail'))+'</tr>')
    details=html.escape(json.dumps(report,ensure_ascii=False,indent=2))
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>SubClock timing review</title><style>body{font:17px system-ui;background:#101827;color:#e2e8f0;max-width:1100px;margin:50px auto;padding:24px}h1{color:#5eead4}table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #334155;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#1b293e;padding:20px;border-radius:12px}</style><h1>SubClock / subtitle timing review</h1><p>''' + f"{report['cue_count']} cues · {report['errors']} errors · {report['warnings']} warnings" + '</p><table><tr><th>Cue</th><th>Level</th><th>Rule</th><th>Detail</th></tr>'+''.join(rows)+'</table><h2>Full report</h2><pre>'+details+'</pre></html>'


def write_new(path,text):
    with Path(path).open('x',encoding='utf-8',newline='') as f:f.write(text)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest='command',required=True)
    check=subs.add_parser('check');check.add_argument('input');check.add_argument('--json',action='store_true');check.add_argument('--html');check.add_argument('--max-cps',type=float,default=20);check.add_argument('--min-ms',type=int,default=500);check.add_argument('--gap-ms',type=int,default=5000);check.add_argument('--fail-on',choices=['error','warning','never'],default='error')
    edit=subs.add_parser('shift');edit.add_argument('input');edit.add_argument('--offset-ms',type=int,required=True);edit.add_argument('--output',required=True)
    args=p.parse_args()
    try:
        cues=parse(Path(args.input).read_text(encoding='utf-8-sig'))
        if args.command=='shift':
            result=shift(cues,args.offset_ms);write_new(args.output,serialize(result));print(f'Shifted {len(result)} cues by {args.offset_ms:+d} ms. Created a new SRT.');return 0
        if not math.isfinite(args.max_cps) or args.max_cps<=0 or args.min_ms<0 or args.gap_ms<0:raise ValueError('thresholds must be finite and positive (durations may be zero)')
        report=analyze(cues,args.max_cps,args.min_ms,args.gap_ms)
        if args.html:write_new(args.html,report_html(report))
        if args.json:print(json.dumps(report,ensure_ascii=True,indent=2))
        else:
            print(f"SubClock: {report['cue_count']} cues, {report['errors']} errors, {report['warnings']} warnings")
            for f in report['findings']:print(f"{f['severity'].upper():7} cue {f['cue']} | {f['rule']} | {f['detail']}")
            print('Input unchanged. Review thresholds depend on language and audience.')
        return int(bool(args.fail_on!='never' and (report['errors'] or (args.fail_on=='warning' and report['warnings']))))
    except (OSError,ValueError,UnicodeError) as exc:
        print('error: '+str(exc),file=sys.stderr);return 2


if __name__=='__main__':sys.exit(main())
