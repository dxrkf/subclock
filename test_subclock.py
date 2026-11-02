import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from subclock import Cue, parse, serialize, analyze, shift, reading_text, report_html, timestamp

class SubtitleTests(unittest.TestCase):
    def test_parse_unicode_bom_crlf(self):
        self.assertEqual(parse('\ufeff1\r\n00:00:01,000 --> 00:00:03,000\r\nمرحبا بالعالم\r\n')[0],Cue(1,1000,3000,'مرحبا بالعالم'))
    def test_roundtrip(self):
        cues=[Cue(1,1500,3500,'hello\nworld'),Cue(3,5000,7000,'<i>Text</i>')]
        self.assertEqual(parse(serialize(cues)),cues)
    def test_invalid_stamp(self):
        with self.assertRaises(ValueError):parse('1\n00:60:00,000 --> 00:00:03,000\nHello')
    def test_missing_number(self):
        with self.assertRaises(ValueError):parse('00:00:01,000 --> 00:00:02,000\nHello')
    def test_empty_input(self):
        with self.assertRaises(ValueError):parse('')
    def test_shift_exact(self):
        a=[Cue(1,1234,4567,'same')];b=shift(a,-234)
        self.assertEqual(b,[Cue(1,1000,4333,'same')]);self.assertEqual(a[0].start,1234)
    def test_negative_shift(self):
        with self.assertRaises(ValueError):shift([Cue(1,0,1000,'a')],-1)
    def test_invalid_duration(self):
        a=[Cue(1,1000,1000,'a')]
        self.assertEqual(analyze(a)['errors'],1)
        with self.assertRaises(ValueError):shift(a,100)
    def test_nested_overlap(self):
        a=[Cue(1,0,10000,'a'),Cue(2,1000,2000,'b'),Cue(3,3000,4000,'c')]
        self.assertEqual(sum(f['rule']=='OVERLAP' for f in analyze(a)['findings']),2)
    def test_touching_not_overlap(self):
        self.assertNotIn('OVERLAP',[f['rule'] for f in analyze([Cue(1,0,1000,'a'),Cue(2,1000,2000,'b')])['findings']])
    def test_source_order(self):
        self.assertIn('OUT_OF_ORDER',[f['rule'] for f in analyze([Cue(1,2000,3000,'a'),Cue(2,0,1000,'b')])['findings']])
    def test_cps_strip_markup(self):
        self.assertEqual(reading_text('<i>Hello</i> &amp; world'),'Hello & world')
        self.assertEqual(analyze([Cue(1,0,1000,'<i>Hello</i>')])['cues'][0]['cps'],5)
    def test_inline_tag_boundaries(self):self.assertEqual(reading_text('<i>Hel</i>lo'),'Hello')
    def test_fast(self):
        self.assertIn('FAST_READING',[f['rule'] for f in analyze([Cue(1,0,1000,'x'*30)])['findings']])
    def test_duplicate(self):
        a=[Cue(1,0,1000,'a'),Cue(1,2000,3000,'b')]
        self.assertEqual(analyze(a)['errors'],1)
        with self.assertRaises(ValueError):shift(a,1)
    def test_empty_visible(self):
        self.assertIn('EMPTY_TEXT',[f['rule'] for f in analyze([Cue(1,0,1000,'<i></i>')])['findings']])
    def test_gap(self):
        self.assertIn('LONG_GAP',[f['rule'] for f in analyze([Cue(1,0,1000,'a'),Cue(2,10000,11000,'b')])['findings']])
    def test_html_escape(self):
        self.assertNotIn('<script>',report_html(analyze([Cue(1,0,1000,'<script>alert(1)</script>')])) )
    def test_hour_overflow(self):self.assertEqual(timestamp(360000000),'100:00:00,000')
    def test_cli_no_overwrite_and_exit(self):
        script=str(Path(__file__).with_name('subclock.py'))
        with tempfile.TemporaryDirectory() as d:
            inp=Path(d)/'a.srt';out=Path(d)/'out.srt';inp.write_text(serialize([Cue(1,0,1000,'x'*30)]))
            a=subprocess.run([sys.executable,script,'check',str(inp),'--fail-on','warning','--json'],capture_output=True,text=True)
            self.assertEqual(a.returncode,1);self.assertEqual(json.loads(a.stdout)['warnings'],1)
            cmd=[sys.executable,script,'shift',str(inp),'--offset-ms','100','--output',str(out)]
            self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,0)
            self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,2)
            self.assertEqual(parse(inp.read_text())[0].start,0)
            inp.write_text(serialize([Cue(1,1000,1000,'a'),Cue(2,2000,1000,'b')]))
            self.assertEqual(subprocess.run([sys.executable,script,'check',str(inp)],capture_output=True).returncode,1)

if __name__=='__main__':unittest.main()
