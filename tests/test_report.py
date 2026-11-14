import copy
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('report', ROOT/'scripts/report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

def fixture():
    # Synthetic unit-test input, never presented as an actual Kubernetes result.
    rows = []
    for ident in sorted(report.IDS):
        rows.append(dict(id=ident,label=ident,target='local-unit-test',requests=90,success=90,
                         failures=0,pods={'pod-a':90},tcp_connections_total=1,
                         tcp_connections_during_measurement=0,warmup_requests=1,
                         p50_ms=0.1,p95_ms=0.2,p99_ms=0.3,passed=True,explanation='test only'))
    return dict(schema_version=1,started_at='unit-test',passed=True,results=rows)

class ReportTests(unittest.TestCase):
    def test_reads_json_and_preserves_failure_log(self):
        x = fixture()
        self.assertEqual(report.read_report(json.dumps(x)+'\nextra stderr\n'),x)
    def test_rejects_missing_json(self):
        with self.assertRaises(ValueError): report.read_report('image pull failed')
    def test_rejects_duplicate_reports(self):
        text=json.dumps(fixture())
        with self.assertRaises(ValueError): report.read_report(text+'\n'+text)
    def test_rejects_inconsistent_counts(self):
        x=fixture(); x['results'][0]['pods']['pod-a']=89
        with self.assertRaises(ValueError): report.validate(x)
    def test_rejects_missing_scenario(self):
        x=fixture(); x['results'].pop()
        with self.assertRaises(ValueError): report.validate(x)
    def test_rejects_unexecuted_pass(self):
        x=fixture(); x['results'][0].update(success=0,pods={})
        with self.assertRaises(ValueError): report.validate(x)
    def test_escapes_html_and_does_not_fetch_assets(self):
        x=fixture(); x['results'][0]['label']='<script>bad()</script>'
        md,page=report.render(x,['pod-a'],'<secret-like-text>')
        self.assertNotIn('<script>',page)
        self.assertIn('&lt;script&gt;',page)
        self.assertIn('&lt;secret-like-text&gt;',page)
        self.assertNotIn('<script src=',page)
        self.assertIn('TCP',md)

if __name__=='__main__': unittest.main()
