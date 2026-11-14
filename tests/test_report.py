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
    for ident in report.ORDER:
        distributed = ident in ('http-new', 'grpc-round-robin')
        pods = {'pod-a': 30, 'pod-b': 30, 'pod-c': 30} if distributed else {'pod-a': 90}
        tcp = {'http-new': 91, 'http-reuse': 1, 'grpc-clusterip': 1, 'grpc-round-robin': 3}[ident]
        rows.append(dict(id=ident, label=ident, target='local-unit-test', requests=90,
                         success=90, failures=0, pods=pods, tcp_connections_total=tcp,
                         tcp_connections_during_measurement=90 if ident == 'http-new' else 0,
                         warmup_requests=1, p50_ms=0.1, p95_ms=0.2, p99_ms=0.3,
                         passed=True, explanation='test only'))
    return dict(schema_version=1, started_at='2026-11-14T11:39:27.749616677Z',
                passed=True, results=rows, expected_replicas=3)


class ReportTests(unittest.TestCase):
    def render(self, data=None, provenance=''):
        return report.render(data or fixture(), ['pod-a', 'pod-b', 'pod-c'], provenance)

    def test_reads_json_and_preserves_failure_log(self):
        x = fixture()
        self.assertEqual(report.read_report(json.dumps(x)+'\nextra stderr\n'), x)

    def test_rejects_missing_json(self):
        with self.assertRaises(ValueError):
            report.read_report('image pull failed')

    def test_rejects_duplicate_reports(self):
        text = json.dumps(fixture())
        with self.assertRaises(ValueError):
            report.read_report(text+'\n'+text)

    def test_rejects_inconsistent_counts(self):
        x = fixture()
        x['results'][0]['pods']['pod-a'] = 29
        with self.assertRaises(ValueError):
            report.validate(x)

    def test_rejects_missing_scenario(self):
        x = fixture()
        x['results'].pop()
        with self.assertRaises(ValueError):
            report.validate(x)

    def test_rejects_unexecuted_pass(self):
        x = fixture()
        x['results'][0].update(success=0, pods={})
        with self.assertRaises(ValueError):
            report.validate(x)

    def test_escapes_html_and_does_not_fetch_assets(self):
        x = fixture()
        x['results'][0]['label'] = '<script>bad()</script>'
        md, page = self.render(x)
        for text in (md, page):
            self.assertNotIn('<script>', text)
            self.assertIn('&lt;script&gt;', text)
        self.assertNotIn('<script src=', page)
        self.assertNotIn('<link ', page)
        self.assertNotIn('@import', page)
        self.assertIn('TCP', md)

    def test_raw_environment_diagnostics_are_not_rendered(self):
        # Deliberately generic sentinel, not a real internal location or secret.
        provenance = '<secret-like-text>\ninternal-mount-location\nkind v0.33.0 go1.26.7 linux/amd64\n'
        for text in self.render(provenance=provenance):
            self.assertNotIn('secret-like-text', text)
            self.assertNotIn('internal-mount-location', text)
            self.assertIn('kind v0.33.0', text)

    def test_provenance_allowlist_is_whole_line_only(self):
        revision = 'a' * 40
        self.assertEqual(report.public_provenance(revision), [('측정에 사용한 소스 커밋', revision)])
        self.assertEqual(report.public_provenance(revision + ' private-notes'), [])
        self.assertEqual(report.public_provenance('node_image=untrusted.example/private:latest'), [])

    def test_korean_time_and_original_nanoseconds(self):
        for text in self.render():
            self.assertIn('2026-11-14 20:39:27', text)
            self.assertIn('2026-11-14T11:39:27.749616677Z', text)

    def test_time_rolls_over_to_next_day(self):
        self.assertIn('2026-11-15 02:00:00', report.measured_time('2026-11-14T17:00:00Z'))

    def test_invalid_time_is_not_invented(self):
        self.assertIn('시각 변환 불가', report.measured_time('unit-test'))

    def test_narrative_contains_real_counts_not_default_600(self):
        for text in self.render():
            self.assertIn('계획 360건 중 성공 360건', text)
            self.assertIn('Pod A 30번 / Pod B 30번 / Pod C 30번', text)
            self.assertNotIn('600번', text)
            self.assertIn('91개는 준비 단계 1개 + 측정 중 90개', text)

    def test_render_preserves_data_and_verdict(self):
        x = fixture()
        before = copy.deepcopy(x)
        self.render(x)
        self.assertEqual(x, before)

    def test_stable_scenario_and_pod_order(self):
        x = fixture()
        x['results'].reverse()
        result = report.render(x, ['pod-c', 'pod-a', 'pod-b'], '')
        for text in result:
            positions = [text.index(report.SCENARIOS[k][0]) for k in report.ORDER]
            self.assertEqual(positions, sorted(positions))
            self.assertIn('Pod A 30번 / Pod B 30번 / Pod C 30번', text)

    def test_setup_failure_is_unexecuted_not_fast_or_successful(self):
        x = fixture()
        x['passed'] = False
        for row in x['results']:
            row.update(success=0, failures=0, pods={}, passed=False,
                       p50_ms=0, p95_ms=0, p99_ms=0,
                       tcp_connections_total=0, tcp_connections_during_measurement=0,
                       explanation='준비 실패', error_samples=['setup failed'])
        for text in self.render(x):
            self.assertIn('미실행 360건', text)
            self.assertIn('측정 미실행', text)
            self.assertIn('측정값 없음', text)
            self.assertNotIn('0.000', text)
            self.assertNotIn('이번에 유지된 서버 구성에서', text)
            self.assertIn('setup failed', text)

    def test_partial_requests_and_failures_are_not_hidden(self):
        x = fixture()
        x['passed'] = False
        x['results'][0].update(success=30, failures=5, pods={'pod-a': 30}, passed=False)
        for text in self.render(x):
            self.assertIn('오류 5건, 미실행 55건', text)
            self.assertIn('성공 응답만 집계; 전체 성공 아님', text)

    def test_all_errors_do_not_claim_pod_distribution(self):
        x = fixture()
        x['passed'] = False
        x['results'][0].update(success=0, failures=90, pods={}, passed=False)
        for text in self.render(x):
            self.assertIn('성공 응답 없음', text)
            self.assertIn('측정값 없음', text)

    def test_environment_failure_overrides_positive_overall_narrative(self):
        x = fixture()
        x['passed'] = False
        x['environment_checks'] = {'kindnet 준비 확인': False}
        for text in self.render(x):
            self.assertIn('환경 검증이 실패하면 전체는 FAIL', text)
            self.assertNotIn('이번에 유지된 서버 구성에서', text)

    def test_absent_environment_is_not_claimed_verified(self):
        for text in self.render():
            self.assertIn('환경 검증 기록이 없다', text)

    def test_unexpected_response_pod_is_not_silently_discarded(self):
        x = fixture()
        x['passed'] = False
        x['results'][0].update(pods={'unexpected-pod': 90}, passed=False)
        for text in self.render(x):
            self.assertIn('unexpected-pod', text)
            self.assertIn('Pod D 90번', text)

    def test_failed_distribution_does_not_claim_criterion_met(self):
        x = fixture()
        x['passed'] = False
        for row in x['results']:
            row['passed'] = False
        for text in self.render(x):
            self.assertNotIn('PASS — 위 기준을 충족했다', text)
            self.assertIn('기대한 동작이 입증되지 않았다', text)

    def test_markdown_evidence_cannot_inject_details_or_tables(self):
        x = fixture()
        x['results'][0]['explanation'] = '</details>\n| invented | [link](bad)'
        md, _ = self.render(x)
        self.assertIn('&lt;/details&gt;', md)
        self.assertIn('\\| invented \\|', md)
        self.assertNotIn('\n| invented |', md)

    def test_successful_one_pod_control_is_explained_as_normal(self):
        for text in self.render():
            self.assertIn('한 Pod 집중이 정상', text)
            self.assertIn('한 Pod에 집중', text)
            self.assertIn('최대 처리량', text)
            self.assertIn('미검증은 실패가 아니라', text)


if __name__ == '__main__':
    unittest.main()
