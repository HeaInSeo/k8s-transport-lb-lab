# 실험 계약과 증거

## 공통 조건

kind 1 control-plane + 2 worker, IPv4, kindnetd, kube-proxy iptables.
서버 Deployment 3 replicas, 두 worker에 분산 배치. 서버 한 Pod가 HTTP 8080과 gRPC 50051을 함께 제공한다.
클라이언트는 클러스터 안의 Job에서 실행한다. port-forward, NodePort, Ingress를 테스트 경로로 사용하지 않는다.
ClusterIP Service의 sessionAffinity는 None, internalTrafficPolicy는 Cluster다. Headless는 준비된 서버 주소만 노출한다.

각 실험은 기본 600회 순차 호출이며 별도의 연결 관리 객체를 사용한다. HTTP/1.1을 명시적으로 유지하고 환경 HTTP 프록시를 사용하지 않는다.
HTTP 연결 재사용 실험은 본문을 끝까지 읽고 닫으며 최대 TCP 연결 수를 1로 제한한다.
gRPC는 pick_first/round_robin 정책을 명시한다. 자동 서비스 설정으로 실험 정책이 변경되지 않도록 한다.
채널 단위 재시도 정책은 비활성화한다. gRPC가 제공하는 일부 투명 재시도까지 전부 없다고 주장하지 않는다.

준비 단계에서 HTTP/기본 gRPC는 성공 응답을 확인하고, round_robin은 서로 다른 서버 3개의 응답을 모두 확인한다.
준비 제한 시간은 실험당 30초, 요청 제한 시간은 2초, Job 전체 제한은 180초다. 준비 요청은 측정 통계에 포함하지 않는다.

## 통과 기준

| 항목 | 판정 |
|---|---|
| 모든 측정 | 예정한 요청 모두 성공, 오류 0 |
| HTTP 새 연결 | 서버 3개 모두 도달 + 측정 중 새 연결 수 = 요청 수 |
| HTTP 재사용 | 응답 Pod 1개 + 준비 포함 TCP 성공 연결 생성 1회 |
| gRPC 기본 | 응답 Pod 1개 + 준비 포함 TCP 성공 연결 생성 1회 |
| gRPC round_robin | 응답 Pod 3개 + TCP 연결 3개 + 각 Pod 요청 수가 기대치 ±20% 이내 |
| 환경 | 측정 전후 Pod UID·배치·Ready·재시작 상태 동일, 재시작 0, 최소 두 노드, kindnet Ready, kube-proxy iptables |

HTTP 새 연결에는 정확한 1/3 분배를 요구하지 않는다. L4 연결 선택이 같은 Pod를 연달아 선택할 수 있다.
round_robin의 ±20%는 안정된 단항 데모용 허용치이며 CPU나 작업량 균등성 보장이 아니다.
서버 3개가 Ready라고 해서 클라이언트가 이미 모두 연결했음을 뜻하지 않으므로 별도 준비 장벽을 둔다.
테스트 중 기존 연결이 끊기면 일부 시나리오의 엄격한 연결 수 조건이 실패할 수 있다. 이를 숨기거나 정상 분산으로 둔갑시키지 않는다.

## 저장되는 증거

`job.log`: stdout/stderr 원본. `results.json`: 측정값과 판정. `summary.md`, `index.html`: 읽기 쉬운 표현.
`environment.txt`: 커밋, 변경 여부, kind, 노드/앱 이미지와 앱 이미지 ID.
`kubernetes-version.json`, `kindnet.json`, `kube-proxy.json`: 실제 환경 정보.
`pods-before.json`, `pods-after.json`, `endpoints-before.json`, `endpoints-after.json`: 실행 전후 상태.
`pods.txt`, `events.txt`, `job.yaml`: 진단과 실험 설정. kubeconfig와 소유권 토큰은 포함하지 않는다.

지연 백분위는 성공 요청만으로 nearest-rank 방식으로 계산한다. 실패는 별도 횟수와 최대 5개 오류 샘플로 남긴다.
TCP 전체는 누적 성공 생성 수다. 활성 연결 수, gRPC stream 수, ClientConn 수와 혼동하지 않는다.
최대 처리량·다중 동시 클라이언트·HTTP/2 일반 HTTP 대조·재시도 안전성·장기 스트림 이동은 이 버전의 검증 범위가 아니다.

## 안전한 정리

`make clean`은 이름 범위, 전용 kubeconfig, 양쪽 소유권 토큰을 확인한 클러스터만 제거한다.
소유권 파일을 지웠거나 클러스터 생성 중 중단되어 토큰 생성까지 완료되지 않았다면 자동 삭제를 거부한다.
그 경우 실제 컨테이너와 이름을 사람이 확인한 뒤 별도로 정리한다. Docker 전체 prune, 다른 클러스터 삭제는 하지 않는다.
실행 락이 남았다면 먼저 해당 실험 프로세스가 종료되었는지 확인한다. 실행 중 락을 지우지 않는다.
