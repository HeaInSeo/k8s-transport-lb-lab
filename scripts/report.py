#!/usr/bin/env python3
"""Explain real probe output in Korean without changing measurements or verdicts."""
import datetime
import html
import json
import pathlib
import re
import sys

ORDER = ('http-new', 'http-reuse', 'grpc-clusterip', 'grpc-round-robin')
IDS = set(ORDER)


def validate(report):
    if report.get('schema_version') != 1:
        raise ValueError('unsupported report schema')
    rows = report.get('results', [])
    if len(rows) != 4 or {r['id'] for r in rows} != IDS:
        raise ValueError('missing or duplicate scenarios')
    for row in rows:
        if row['success'] != sum(row['pods'].values()):
            raise ValueError('pod counts do not match successes')
        counts = [row['requests'], row['success'], row['failures'], *row['pods'].values()]
        if any(type(n) is not int or n < 0 for n in counts):
            raise ValueError('invalid request counts')
        if row['success'] + row['failures'] > row['requests']:
            raise ValueError('more observations than requested')
        if row['passed'] and (row['success'] != row['requests'] or row['failures']):
            raise ValueError('failed or unexecuted requests hidden by PASS')
    if report['passed'] != all(row['passed'] for row in rows):
        raise ValueError('inconsistent overall verdict')


def read_report(text):
    candidates = [json.loads(line) for line in text.splitlines() if line.startswith('{')]
    if len(candidates) != 1:
        raise ValueError('expected exactly one JSON report in job.log; see original log')
    validate(candidates[0])
    return candidates[0]


def snapshot(items):
    return {p['metadata']['name']: (p['metadata']['uid'], p['spec']['nodeName'],
            tuple((c['name'], c.get('restartCount', 0), c.get('ready', False))
                  for c in p.get('status', {}).get('containerStatuses', []))) for p in items}


def environment_checks(out, report):
    before = snapshot(json.loads((out/'pods-before.json').read_text())['items'])
    after = snapshot(json.loads((out/'pods-after.json').read_text())['items'])
    proxy = json.loads((out/'kube-proxy.json').read_text())['data']['config.conf']
    daemon = json.loads((out/'kindnet.json').read_text())
    checks = {
        '측정 전후 같은 Pod · 재시작 없음': before == after and all(all(c[1] == 0 for c in p[2]) for p in after.values()),
        '서버 3개 Ready · 두 노드 이상 배치': len(after) == report['expected_replicas'] and len({p[1] for p in after.values()}) >= 2 and all(p[2] and all(c[2] for c in p[2]) for p in after.values()),
        '응답 Pod가 실제 배포된 서버와 일치': all(set(r['pods']).issubset(after) for r in report['results']),
        'kube-proxy iptables 기준선': bool(re.search(r'mode:\s*[\"\x27]?iptables', proxy)),
        'kindnet 준비 확인': daemon.get('status', {}).get('numberReady', 0) == daemon.get('status', {}).get('desiredNumberScheduled', -1) and daemon.get('status', {}).get('numberReady', 0) > 0,
    }
    report['environment_checks'] = checks
    report['probe_passed'] = report['passed']
    report['passed'] = report['passed'] and all(checks.values())
    return sorted(after)


SCENARIOS = {
    'http-new': (
        'HTTP — 요청마다 새로 연결하기',
        '질문할 때마다 전화를 새로 걸면, 여러 상담원이 나눠 받을까?',
        'HTTP/1.1 요청을 한 번 보낼 때마다 새 TCP 연결을 만든다.',
        '이번 기본 Service 경로는 새 TCP 연결을 만들 때 백엔드 Pod를 선택한다. '
        '새 연결이 많아지면 여러 Pod가 선택될 기회도 생긴다.',
        '모든 서버가 요청을 받고, 측정 중 새 TCP 수가 계획한 요청 수와 같아야 한다. '
        '각 서버가 정확히 같은 횟수를 처리할 필요는 없다.'),
    'http-reuse': (
        'HTTP — 연결 하나를 계속 쓰기',
        '전화 한 통으로 계속 질문하면, 같은 상담원이 답할까?',
        'HTTP/1.1 연결 하나를 유지하면서 요청을 순서대로 보낸다.',
        '이미 선택된 Pod로 이어지는 연결을 재사용한다. '
        'HTTP 요청이 새로 생겼다고 이 연결의 목적지가 다른 Pod로 바뀌지는 않는다.',
        '한 Pod가 모든 요청을 처리하고, 성공적으로 만든 TCP 연결도 전체 1개여야 한다. '
        '이 대조 실험에서는 한 Pod 집중이 정상 결과다.'),
    'grpc-clusterip': (
        'gRPC — 기본 Service로 연결하기',
        '서버를 여러 개 띄우기만 하면, gRPC 호출도 저절로 나뉠까?',
        'ClusterIP라는 하나의 Service 주소에 연결하고, 같은 gRPC 연결로 호출을 보낸다.',
        'gRPC는 HTTP/2 연결을 재사용해 여러 호출을 보낼 수 있다. '
        '이번처럼 실제 TCP 연결 하나를 유지하면 같은 Pod가 계속 처리한다.',
        '한 Pod가 모든 호출을 처리하고, 성공적으로 만든 TCP 연결도 전체 1개여야 한다. '
        '서버 복제본 수와 한 클라이언트의 호출 분산은 별개임을 확인하는 실험이다.'),
    'grpc-round-robin': (
        'gRPC — 서버 주소를 알고 번갈아 보내기',
        '상담원들의 번호를 알고 각각 연결하면, 질문을 나눠 보낼 수 있을까?',
        'Headless Service로 Pod 주소들을 찾고, round_robin으로 준비된 서버 연결을 번갈아 선택한다.',
        'Headless는 주소를 알려주고, 실제 호출 분배는 gRPC 클라이언트가 맡는다. '
        '클라이언트 프로그램이나 ClientConn을 여러 개 만드는 방식이 아니다. '
        '논리적인 ClientConn 하나가 서버별 TCP 연결을 관리한다.',
        '모든 서버가 호출을 받고, TCP 전체 수가 서버 수와 같아야 한다. '
        '각 Pod의 처리 횟수는 균등 기대치의 ±20% 이내여야 한다. 정확히 같은 횟수일 필요는 없다.'),
}
CHECK_MEANINGS = {
    '측정 전후 같은 Pod · 재시작 없음': '전후 스냅샷의 Pod 식별값·노드·컨테이너 상태가 같고, 재시작 기록이 없는지 확인한다.',
    '서버 3개 Ready · 두 노드 이상 배치': '예정한 서버들이 준비됐고, 적어도 두 Kubernetes 노드에 나뉘었는지 확인한다.',
    '응답 Pod가 실제 배포된 서버와 일치': '응답에 나온 Pod 이름이 실제 실험용 서버 목록에 포함되는지 확인한다.',
    'kube-proxy iptables 기준선': '비교 기준으로 정한 iptables Service 처리 모드인지 확인한다.',
    'kindnet 준비 확인': 'Pod 사이의 네트워크를 구성하는 기본 프로그램이 준비됐는지 확인한다.',
}
LIMITS = [
    ('Pod 증설', '서버를 늘리면 새 서버를 언제 발견하고 요청을 보내는가?'),
    ('Pod 장애', '서버 하나가 종료되면 몇 번 실패하고 언제 정상 복구되는가?'),
    ('장기 스트림', '긴 통신의 시작·유지·종료와 새 스트림의 분산은 어떻게 되는가?'),
    ('mTLS', '서로 인증서를 확인하는 보안 연결에서도 같은 결과가 나오는가?'),
    ('Envoy', '중간 프록시가 호출을 분배하면 결과가 어떻게 달라지는가?'),
    ('Cilium / nftables', '네트워크 또는 Service 구현을 바꿔도 같은 현상이 재현되는가?'),
]


def md_escape(value):
    """Keep arbitrary evidence from becoming Markdown/HTML syntax."""
    text = str(value).replace('\r', ' ').replace('\n', ' ')
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'([\\`*_{}\[\]|])', r'\\\1', text)


class Document:
    """One content stream for GitHub Markdown and the offline HTML report."""
    def __init__(self):
        self.md, self.web = [], []

    def heading(self, level, text, anchor=None):
        self.md.extend(['#' * level + ' ' + md_escape(text), ''])
        ident = ' id="{}"'.format(html.escape(anchor, quote=True)) if anchor else ''
        self.web.append('<h{0}{1}>{2}</h{0}>'.format(level, ident, html.escape(text)))

    def p(self, text):
        self.md.extend([md_escape(text), ''])
        self.web.append('<p>{}</p>'.format(html.escape(text)))

    def fact(self, label, text):
        self.md.extend(['**{}** {}'.format(md_escape(label), md_escape(text)), ''])
        self.web.append('<p><strong>{}</strong> {}</p>'.format(html.escape(label), html.escape(text)))

    def callout(self, text, status='note'):
        self.md.extend(['> ' + md_escape(text), ''])
        self.web.append('<aside class="{}">{}</aside>'.format(status, html.escape(text)))

    def table(self, headings, rows):
        self.md.extend(['| ' + ' | '.join(map(md_escape, headings)) + ' |',
                        '|' + '|'.join('---' for _ in headings) + '|'])
        self.md.extend('| ' + ' | '.join(map(md_escape, row)) + ' |' for row in rows)
        self.md.append('')
        head = ''.join('<th scope="col">{}</th>'.format(html.escape(str(h))) for h in headings)
        body = ''.join('<tr>' + ''.join('<td>{}</td>'.format(html.escape(str(v))) for v in row) + '</tr>' for row in rows)
        self.web.append('<div class="table-scroll"><table><thead><tr>{}</tr></thead><tbody>{}</tbody></table></div>'.format(head, body))

    def details(self, title):
        self.md.extend(['<details>', '<summary>{}</summary>'.format(html.escape(title)), ''])
        self.web.append('<details><summary>{}</summary>'.format(html.escape(title)))

    def end_details(self):
        self.md.extend(['</details>', ''])
        self.web.append('</details>')


def measured_time(value):
    """Use an explicit timezone; preserve the source timestamp, including nanoseconds."""
    try:
        match = re.fullmatch(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?Z', value)
        if not match:
            raise ValueError('not RFC3339 UTC')
        utc = datetime.datetime.strptime(match.group(1), '%Y-%m-%dT%H:%M:%S')
        kst = utc + datetime.timedelta(hours=9)
        return kst.strftime('%Y-%m-%d %H:%M:%S') + ' (한국 시간, UTC+9)'
    except (ValueError, TypeError):
        return '시각 변환 불가 — 원본: ' + str(value)


def public_provenance(text):
    """Only recognized reproducibility identifiers; never echo raw host diagnostics."""
    patterns = [
        ('kind 버전', r'kind v[0-9][0-9A-Za-z.+-]* go[0-9.]+ [a-z0-9]+/[a-z0-9]+'),
        ('Kubernetes 노드 이미지', r'node_image=kindest/node:v[0-9.]+@sha256:[0-9a-f]{64}'),
        ('실험 앱 이미지', r'app_image=k8s-transport-lb-lab:[0-9a-f]{16,64}'),
        ('측정에 사용한 소스 커밋', r'[0-9a-f]{40}'),
        ('앱 이미지 식별값', r'sha256:[0-9a-f]{64}'),
    ]
    found = []
    for line in text.splitlines():
        for title, pattern in patterns:
            if re.fullmatch(pattern, line):
                found.append((title, line.split('=', 1)[-1]))
                break
    return found


def alias(index):
    return 'Pod ' + (chr(65 + index) if index < 26 else str(index + 1))


def counts_text(row, names):
    return ' / '.join('{} {}번'.format(alias(i), row['pods'].get(name, 0)) for i, name in enumerate(names))


def observation(row):
    observed = row['success'] + row['failures']
    active = sum(n > 0 for n in row['pods'].values())
    if observed == 0:
        return '측정 미실행 — 분산 여부를 판단할 자료 없음'
    if row['success'] == 0:
        return '성공 응답 없음 — 분산 성공으로 판단할 수 없음'
    result = '한 Pod에 집중' if active == 1 else '{}개 Pod가 나눠 처리'.format(active)
    if observed < row['requests'] or row['failures']:
        result += ' (성공 응답만 집계; 전체 성공 아님)'
    return result


def latency(row, key):
    return '{:.3f}'.format(row[key]) if row['success'] else '측정값 없음'


CSS = '''
:root{color-scheme:light;--ink:#203047;--muted:#526577;--line:#dce4eb;--accent:#155e75}
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:var(--ink);font:16px/1.8 system-ui,-apple-system,"Noto Sans KR",sans-serif}
main{max-width:1040px;margin:auto;padding:40px 28px 72px}header{padding:30px 32px;background:#172a45;color:white;border-radius:16px;margin-bottom:28px}
header p{margin:5px 0}header .eyebrow{font-size:13px;letter-spacing:.12em}h1{font-size:32px;line-height:1.35;margin:12px 0}
nav{display:flex;flex-wrap:wrap;gap:10px 22px;padding:16px 0 24px}a{color:var(--accent)}nav a{font-weight:650;text-underline-offset:5px}
h2{font-size:24px;line-height:1.45;margin:42px 0 18px;scroll-margin-top:24px}h3{font-size:20px;margin:8px 0 18px;line-height:1.5}
p{margin:12px 0 18px}strong{font-weight:750}.card{background:white;border:1px solid var(--line);border-radius:12px;padding:25px 28px;margin:18px 0;break-inside:avoid}
aside{padding:18px 22px;border-left:4px solid var(--accent);border-radius:6px;background:#e9f1f5;margin:20px 0}
aside.pass{border-color:#1a7550;background:#e9f4ee}aside.fail{border-color:#b42332;background:#fff0f1}
.table-scroll{overflow-x:auto;margin:18px 0 24px;border:1px solid var(--line);border-radius:10px;background:white}
table{width:100%;border-collapse:collapse;font-size:14px;line-height:1.65}th,td{padding:13px 15px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line);overflow-wrap:anywhere}th{background:#eaf0f5;font-weight:750}tr:last-child td{border-bottom:0}
details{background:white;border:1px solid var(--line);border-radius:10px;padding:18px 22px;margin:22px 0}summary{font-weight:700;cursor:pointer}details table{font-size:13px}footer{font-size:13px;color:var(--muted);border-top:1px solid var(--line);padding-top:20px;margin-top:36px}
@media(max-width:640px){main{padding:18px 16px 40px}header{padding:22px}h1{font-size:27px}h2{font-size:22px}.card{padding:20px}table{min-width:570px}th,td{padding:11px 12px}}
@media print{body{background:white;font-size:11pt}main{max-width:none;padding:0}header{background:white;color:black;border:1px solid var(--line);padding:18px}nav{display:none}.card{padding:18px}h2{break-after:avoid}.table-scroll{overflow:visible}table{font-size:10pt;min-width:0}details{break-inside:avoid}a{color:inherit}}
'''


def render(report, names, provenance):
    by_id = {row['id']: row for row in report['results']}
    rows = [by_id[ident] for ident in ORDER]
    # Keep unexpected response identities visible, especially on failed environment checks.
    names = sorted(set(names).union(*(set(row['pods']) for row in rows)))
    checks = report.get('environment_checks', {})
    state = 'PASS' if report['passed'] else 'FAIL'
    total = sum(row['requests'] for row in rows)
    success = sum(row['success'] for row in rows)
    failures = sum(row['failures'] for row in rows)
    pending = total - success - failures
    d = Document()
    d.heading(1, 'HTTP · gRPC 분산 실험 — 쉽게 읽는 결과')
    d.fact('전체 판정:', state)
    d.fact('측정 시각:', measured_time(report['started_at']))
    d.p('원본 시각: ' + report['started_at'])
    d.callout('질문: 서버를 여러 개 띄웠는데, 왜 어떤 연결에서는 한 Pod만 일하고 어떤 연결에서는 나눠서 일할까?')
    d.fact('이번에 실제로 한 일:', '계획 {:,}건 중 성공 {:,}건, 오류 {:,}건, 미실행 {:,}건이다. 준비 호출은 이 합계에서 제외했다.'.format(total, success, failures, pending))
    if report['passed']:
        d.callout('PASS는 각 실험이 기대한 동작을 확인했다는 뜻이다. 모든 실험이 균등 분산됐다는 뜻은 아니다. 두 대조 실험에서는 한 Pod 집중이 정상이다.', 'pass')
    else:
        d.callout('FAIL: 일부 실험 또는 환경 검증이 기준을 충족하지 못했다. 아래에서 실제 관측과 기대한 동작을 구분해 읽어야 한다. 오류 0건이어도 준비 실패로 측정하지 못했을 수 있다.', 'fail')
    d.heading(2, '1. 먼저 이것만 보기', 'overview')
    d.p('Pod는 여기서 요청에 답하는 서버 한 개라고 생각하면 된다. 긴 이름 대신 A·B·C처럼 부르며, 같은 글자는 이 보고서 안에서 같은 Pod를 뜻한다. 실제 이름은 맨 아래에 있다.')
    d.table(['실험', '성공 / 계획', 'Pod별 실제 처리 횟수', '관찰한 모습', '판정'], [
        (SCENARIOS[r['id']][0], '{}/{}'.format(r['success'], r['requests']), counts_text(r, names), observation(r), 'PASS' if r['passed'] else 'FAIL') for r in rows])
    d.callout('TCP 연결은 전화를 거는 것, 요청은 통화 중 질문하는 것에 가깝다. 전화를 한 번 걸어 질문을 여러 번 하는 것과, 질문할 때마다 새로 전화하는 것은 다르다.')
    d.heading(2, '2. 왜 이런 결과가 나왔을까?', 'stories')
    for number, row in enumerate(rows, 1):
        title, question, method, why, criterion = SCENARIOS[row['id']]
        d.web.append('<section class="card">')
        d.heading(3, '{}) {}'.format(number, title))
        d.fact('알아보려던 것:', question)
        d.fact('시험 방법:', method)
        d.fact('실제 결과:', counts_text(row, names) + '. ' + observation(row) + '.')
        remaining = row['requests'] - row['success'] - row['failures']
        d.fact('요청 확인:', '성공 {}건 / 오류 {}건 / 미실행 {}건.'.format(row['success'], row['failures'], remaining))
        d.fact('동작 원리:', why)
        d.fact('PASS 기준:', criterion)
        if row['passed']:
            d.fact('이번 판정:', 'PASS — 위 기준을 충족했다. 이 결론은 이번 실험 조건에 한정된다.')
        else:
            d.fact('이번 판정:', 'FAIL — 기대한 동작이 입증되지 않았다. 준비·요청·연결·분포 조건을 원본 기록과 함께 확인해야 한다.')
        d.web.append('</section>')
    d.heading(2, '3. 연결 수는 이렇게 읽기', 'connections')
    d.p('TCP 전체는 준비 단계부터 성공적으로 새로 만든 연결의 누적 개수다. 동시에 열린 연결 수가 아니다. 측정 중 신규 TCP가 0이면 준비 때 만든 연결을 그대로 사용했을 수 있다.')
    d.table(['실험', '준비 단계의 TCP', '측정 중 신규 TCP', 'TCP 전체'], [
        (SCENARIOS[r['id']][0], r['tcp_connections_total'] - r['tcp_connections_during_measurement'], r['tcp_connections_during_measurement'], r['tcp_connections_total']) for r in rows])
    first = by_id['http-new']
    if first['passed']:
        d.p('이번 HTTP 새 연결의 {}개는 준비 단계 {}개 + 측정 중 {}개다. 요청 성공 수와 연결 수의 집계 구간이 달라 숫자가 다를 수 있다.'.format(first['tcp_connections_total'], first['tcp_connections_total'] - first['tcp_connections_during_measurement'], first['tcp_connections_during_measurement']))
    d.p('준비 호출 횟수와 준비 단계의 연결 수는 같은 개념이 아니다. 연결 하나로 준비 호출을 여러 번 할 수도 있다. 하나의 gRPC ClientConn 역시 TCP 연결 하나와 같은 뜻이 아니다.')
    d.heading(2, '4. 응답 시간은 참고로 읽기', 'latency')
    d.p('p50은 빠른 순서로 세웠을 때 약 절반 지점의 시간이다. p95·p99는 약 95%·99% 지점이다. 평균이나 최대값이 아니다. 1ms는 0.001초다.')
    d.table(['실험', 'p50: 약 50% 지점 (ms)', 'p95: 약 95% 지점 (ms)', 'p99: 약 99% 지점 (ms)'], [
        (SCENARIOS[r['id']][0], latency(r, 'p50_ms'), latency(r, 'p95_ms'), latency(r, 'p99_ms')) for r in rows])
    rr = by_id['grpc-round-robin']
    if rr['success']:
        d.p('마지막 실험의 p95 = {:.3f}ms는 성공 요청의 약 95%가 그 시간 안에 끝났다는 뜻이다.'.format(rr['p95_ms']))
    d.callout('지연 시간은 성공 요청만 집계한다. 성공 응답이 없으면 0ms가 아니라 측정값 없음으로 표시한다. 작은 요청을 순차 호출한 실험이므로, 이 숫자로 HTTP와 gRPC의 성능 우열이나 운영 최대 처리량을 판단하면 안 된다.')
    d.heading(2, '5. 실험 조건도 확인했을까?', 'environment')
    if checks:
        d.table(['확인한 항목', '판정', '쉽게 말하면'], [
            (name, 'PASS' if ok else 'FAIL', CHECK_MEANINGS.get(name, '해당 원본 검증 기록을 확인한다.')) for name, ok in checks.items()])
    else:
        d.callout('환경 검증 기록이 없다. 실험 환경이 확인됐다고 해석하면 안 된다.', 'fail')
    d.p('kindnetd는 Pod 사이의 길을 구성한다. 기본 Service 연결 선택은 kube-proxy의 규칙이, 마지막 실험의 호출별 선택은 gRPC 클라이언트가 담당한다. kind의 여러 노드는 컨테이너이므로 물리 서버 여러 대에서 검증했다는 뜻은 아니다.')
    if checks and not all(checks.values()):
        d.callout('개별 요청 실험이 PASS여도 환경 검증이 실패하면 전체는 FAIL이다. 결과를 정상 기준선의 증거로 사용하지 않는다.', 'fail')
    d.heading(2, '6. 여기까지 확인했고, 여기부터는 아직이다', 'limits')
    if report['passed']:
        d.fact('확인한 것:', '이번에 유지된 서버 구성에서 연결 방식에 따른 한 Pod 집중과 클라이언트 측 분산의 차이를 재현했다.')
    else:
        d.fact('확인 범위:', '이번 실행은 전체 검증을 통과하지 못했다. 각 실험의 관측값은 남기되, 분산 방식 전체가 검증됐다고 결론 내리지 않는다.')
    d.p('미검증은 실패가 아니라 아직 시험하지 않았다는 뜻이다.')
    d.table(['아직 시험하지 않은 조건', '다음에 답해야 할 질문'], LIMITS)
    d.p('진행 중인 긴 스트림을 다른 서버로 옮기는 것과 새 호출·새 스트림의 서버를 선택하는 것은 다르다. 이번 결과는 전자를 검증하지 않는다.')
    d.heading(2, '7. 필요할 때만 보는 상세 기록', 'details')
    d.details('실제 Pod 이름 · 원본 지표 · 판정 근거')
    d.table(['별칭', '실제 Pod 이름'], [(alias(i), name) for i, name in enumerate(names)])
    d.table(['실험', '준비 호출 수', '측정 프로그램의 원본 설명'], [
        (r['label'], r['warmup_requests'], r['explanation']) for r in rows])
    for row in rows:
        if row.get('error_samples'):
            d.fact('오류 기록 — ' + SCENARIOS[row['id']][0] + ':', ' | '.join(row['error_samples']))
    d.end_details()
    d.details('같은 실험을 다시 확인할 때 필요한 버전')
    safe = public_provenance(provenance)
    if safe:
        d.table(['기록', '실제 값'], safe)
    else:
        d.p('표시할 수 있는 버전 식별값이 없다. 원본 환경 기록을 별도로 확인해야 한다.')
    d.p('이 표의 커밋은 측정 당시 소스 버전이다. 기존 측정값으로 설명만 다시 생성했다면 보고서 작성 코드의 최신 커밋과 다를 수 있다. 원본 환경 로그 전체는 이 화면에 그대로 붙이지 않는다.')
    d.end_details()
    d.p('원본 수치와 실행 시각은 results.json, 읽기용 문서는 summary.md에 있다. 진단 파일을 외부에 공유하기 전에는 내부 주소·경로·인증정보가 없는지 별도로 점검해야 한다.')
    title = 'HTTP · gRPC 분산 실험 — ' + state
    nav = '<nav aria-label="보고서 목차">' + ''.join('<a href="#{}">{}</a>'.format(a, t) for a, t in [('overview', '결과 먼저'), ('stories', '왜 이런가?'), ('connections', '연결 수'), ('latency', '응답 시간'), ('limits', '확인 범위')]) + '</nav>'
    page = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{}</title><style>{}</style></head><body><main><header><p class="eyebrow">K8S TRANSPORT LB LAB</p><h1>서버는 여러 개인데,<br>왜 한 곳만 일할까?</h1><p>숫자를 먼저 보고, 그 이유를 차례로 읽는 실험 보고서</p></header>{}{}<footer>실제 측정값으로 작성 · 외부 스크립트와 폰트 없이 열리는 보고서 · 설명 형식 v2</footer></main></body></html>'.format(html.escape(title), CSS, nav, ''.join(d.web))
    return '\n'.join(d.md) + '\n', page


def main():
    out = pathlib.Path(sys.argv[1])
    report = read_report((out/'job.log').read_text(encoding='utf-8'))
    names = environment_checks(out, report)
    md, page = render(report, names, (out/'environment.txt').read_text(encoding='utf-8'))
    (out/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (out/'summary.md').write_text(md, encoding='utf-8')
    (out/'index.html').write_text(page, encoding='utf-8')
    print(md)


if __name__ == '__main__':
    main()
