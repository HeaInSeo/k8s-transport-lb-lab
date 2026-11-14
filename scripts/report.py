#!/usr/bin/env python3
"""Render real probe output only. No fabricated fallback data; no dependencies."""
import html
import json
import pathlib
import re
import sys

IDS = {'http-new', 'http-reuse', 'grpc-clusterip', 'grpc-round-robin'}

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

def render(report, names, provenance):
    esc = lambda x: html.escape(str(x), quote=True)
    state = 'PASS' if report['passed'] else 'FAIL'
    md = ['# HTTP · gRPC 분산 실험', '', f'전체 판정: **{state}**', f'실행: {report["started_at"]}', '',
          '단일 연결의 한 Pod 집중은 대조 실험의 정상 결과다. 이것은 최대 처리량 벤치마크가 아니다.', '',
          '| 실험 | 성공 / 계획 | 오류 | TCP 전체¹ | 측정 중 신규 TCP | p50 / p95 / p99 (ms) |', '|---|---:|---:|---:|---:|---:|']
    cards = []
    for row in report['results']:
        badge = 'PASS' if row['passed'] else 'FAIL'
        times = ' / '.join(f'{row[k]:.3f}' for k in ('p50_ms','p95_ms','p99_ms'))
        md.append(f'| {row["label"]} ({badge}) | {row["success"]}/{row["requests"]} | {row["failures"]} | {row["tcp_connections_total"]} | {row["tcp_connections_during_measurement"]} | {times} |')
        bars = ''.join(f'<div class="bar"><code>{esc(name)}</code><meter min="0" max="{max(row["requests"],1)}" value="{row["pods"].get(name,0)}"></meter><b>{row["pods"].get(name,0)}</b></div>' for name in names)
        errors = '<br>'.join(esc(x) for x in row.get('error_samples',[]))
        cards.append(f'<section><span class="badge">{badge}</span><h2>{esc(row["label"])}</h2><p class="target">{esc(row["target"])}</p>{bars}<div class="numbers">성공 <b>{row["success"]}/{row["requests"]}</b> · 오류 <b>{row["failures"]}</b> · TCP 전체¹ <b>{row["tcp_connections_total"]}</b></div><p>p50 / p95 / p99: {times} ms<br>측정 중 새 TCP: {row["tcp_connections_during_measurement"]} · 준비 호출: {row["warmup_requests"]}</p><p>{esc(row["explanation"])}</p><p class="errors">{errors}</p></section>')
    md += ['', '¹ TCP 전체는 준비 단계 포함. 성공한 TCP 연결 수이며 동시에 열린 연결 수와는 다르다.', '', '## Pod별 실제 요청 수', '']
    for row in report['results']:
        md.append(f'- {row["label"]}: ' + ', '.join(f'{n}={row["pods"].get(n,0)}' for n in names))
    checks = report.get('environment_checks', {})
    md += ['', '## 환경 검증'] + [f'- {"PASS" if ok else "FAIL"}: {name}' for name,ok in checks.items()]
    md += ['', '## 재현 정보', '```text', provenance, '```', '', '미검증: Pod 증설/장애/장기 스트림, mTLS, Envoy, Cilium, nftables.']
    check_html = ''.join(f'<li>{"PASS" if ok else "FAIL"} — {esc(name)}</li>' for name,ok in checks.items())
    css = 'body{font:16px/1.65 system-ui,sans-serif;background:#f3f5f7;color:#203047;margin:0}main{max-width:1120px;margin:0 auto;padding:36px 24px}header{background:#172a45;color:white;padding:30px;border-radius:16px}h1{font-size:32px;line-height:1.3}h2{font-size:20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(390px,1fr));gap:20px;margin:24px 0}section{background:white;border:1px solid #dce3e9;padding:24px;border-radius:12px;break-inside:avoid}.badge{float:right;font-weight:700}.target,code,pre{font-size:12px;overflow-wrap:anywhere}pre{white-space:pre-wrap}.bar{display:grid;grid-template-columns:1fr 100px 40px;gap:10px;align-items:center}.numbers{border-top:1px solid #dce3e9;margin-top:20px;padding-top:16px}.errors{color:#a22}.note{padding:20px;background:#e8eff4;border-radius:12px}@media(max-width:500px){.grid{display:block}section{margin-bottom:20px}.bar{grid-template-columns:1fr 70px 30px}}@media print{body{background:white}.grid{display:block}header{background:white;color:black}section{margin-bottom:20px}}'
    page = f'<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HTTP · gRPC 분산 실험 — {state}</title><style>{css}</style><main><header><small>K8S TRANSPORT LB LAB · 실제 측정 결과</small><h1>연결이 바뀌면,<br>분산도 바뀐다.</h1><p>전체 판정 <strong>{state}</strong> · {esc(report["started_at"])}</p></header><p class="note">HTTP 새 연결 → 여러 Pod. HTTP 연결 재사용·기본 gRPC → 한 Pod. Headless + round_robin → RPC별 분산.<br>한 Pod 집중은 두 대조 실험에서 정상이며, 실패를 의미하지 않는다.</p><div class="grid">{"".join(cards)}</div><section><h2>환경 검증</h2><ul>{check_html}</ul><p>¹ TCP 전체는 준비 단계 포함 누적 성공 연결 수. 지연은 성공 요청만 집계한다. 네 실험은 순차 호출이며 최대 처리량이나 프로토콜 우열을 판단하는 벤치마크가 아니다.</p><p>미검증: Pod 증설·장애·장기 스트림·mTLS·Envoy·Cilium·nftables.</p></section><details><summary>재현 정보 · 원본 파일</summary><pre>{esc(provenance)}</pre><a href="results.json">results.json</a> · <a href="summary.md">summary.md</a> · <a href="job.log">job.log</a></details></main></html>'
    return '\n'.join(md)+'\n', page

def main():
    out = pathlib.Path(sys.argv[1])
    report = read_report((out/'job.log').read_text())
    names = environment_checks(out, report)
    md, page = render(report, names, (out/'environment.txt').read_text())
    (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (out/'summary.md').write_text(md)
    (out/'index.html').write_text(page)
    print(md)

if __name__ == '__main__':
    main()
