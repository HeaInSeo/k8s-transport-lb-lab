# k8s-transport-lb-lab

**HTTP와 gRPC 요청이 어느 Pod로 가는지, 직접 눈으로 확인하는 작은 실험실.**

코드를 읽을 시간이 없어도 된다. 실행한 뒤 **GitHub Actions 요약 또는 `index.html`**만 보면 된다.
이 저장소는 학습·동작 검증용이다. 운영용 네트워크 제품이나 최대 처리량 벤치마크가 아니다.

## 가장 편한 방법: 설치 없이 GitHub에서 실행

1. 이 저장소의 **Actions → Transport lab → Run workflow**를 누른다. `main`에 코드를 올려도 자동 실행된다.
2. 실행의 **Summary**에서 네 실험의 성공/실패, TCP 연결 수, Pod별 요청 수를 확인한다.
3. 같은 화면의 **Artifacts → transport-lab-report**를 받아 압축을 풀고, 실행 시각 폴더 안의 **`index.html`**을 연다. 인터넷 없이도 열린다.

실험이 실패하면 원본 로그도 같은 아티팩트에 남는다. 보고서가 없다고 정상 실행으로 판단하지 않는다.
GitHub Actions 사용량과 아티팩트 보관에는 계정의 사용 한도·요금 정책이 적용된다.

## 내 컴퓨터에서: `make demo`

준비물: Linux 또는 macOS, 실행 중인 Docker, kubectl, Python 3.9 이상, make, kind.
Docker에 메모리 6 GiB 이상을 할당하는 것을 권장하며, 작업 디스크 여유는 8 GiB 이상을 요구한다.
Go는 Docker 빌드 안에서 사용하므로 데모만 실행할 때 호스트에 설치하지 않아도 된다.

```bash
git clone https://github.com/HeaInSeo/k8s-transport-lb-lab.git
cd k8s-transport-lb-lab
make install-kind   # 처음 한 번. 저장소의 .lab/bin에만 설치; 시스템 kind는 바꾸지 않음.
make demo
```

이 저장소는 비공개로 생성되었으므로 clone에는 저장소 접근 권한이 필요하다.
`make demo`는 전용 kind 클러스터 생성 → 이미지 빌드 → 서버 3개 배포 → 클러스터 내부에서 실험 → 보고서 생성을 수행한다.
완료 후 **`reports/latest/index.html`**을 브라우저로 열면 된다.
서버 3개는 각각 HTTP와 gRPC를 함께 제공하므로 두 프로토콜이 같은 Pod 배치를 사용한다.

```bash
make doctor         # 설치·Docker·디스크 상태 확인
make smoke          # 이미 만든 실험 환경에서 다시 측정
REQUESTS=1200 make smoke
make clean          # 이 저장소가 만든 클러스터만 삭제; 보고서는 보존
make ci             # Go/race/vet + Python 보고서/안전장치 테스트 (호스트 Go 필요)
```

**Podman은 첫 버전의 지원 실행 경로가 아니다.** Docker가 없거나 디스크가 부족하면 서버를 변경하지 말고 위의 GitHub Actions 경로를 사용한다.

## 결과에서 이것만 보면 된다

| 실험 | 바꾸는 조건 | 정상적으로 관찰하려는 결과 |
|---|---|---|
| HTTP 새 연결 | 요청마다 새 TCP 연결 | 여러 Pod로 분산. 정확히 1/3일 필요는 없음 |
| HTTP 연결 재사용 | 순차 요청, TCP 연결 하나 | 같은 Pod가 계속 처리 |
| gRPC 기본 | ClusterIP + pick_first, ClientConn 하나 | 같은 Pod가 계속 처리 |
| gRPC 클라이언트 분산 | Headless + round_robin, ClientConn 하나 | 서버별 TCP 연결을 만들고 RPC를 분산 |

**한 Pod에 몰리는 것이 무조건 실패는 아니다.** 두 번째·세 번째는 그 현상을 재현하는 대조 실험이다.
한 개의 gRPC `ClientConn`이 반드시 한 개의 TCP 연결을 뜻하지도 않는다. 네 번째에서는 하나의 ClientConn이 서버 3개에 연결한다.

보고서에는 실제 Pod 이름, 처리 횟수, 실패 수, 누적 TCP 연결 수, p50/p95/p99 지연이 나온다.
준비 호출은 요청 분포에서 제외한다. TCP 전체 수는 준비 단계까지 포함한 **성공 연결의 누적 생성 수**다.
HTTP 새 연결은 기본 600회 측정 시 준비 연결 1개를 포함해 TCP 전체 601개가 정상이다.
기본 gRPC는 재시도 정책을 켜지 않으며, 실험 중 서버를 의도적으로 제거하지 않는다.

## kindnetd는 여기서 무슨 역할인가?

**kind는 연습용 Kubernetes를 만드는 도구, kindnetd는 그 안에서 Pod 사이의 길을 마련하는 기본 네트워크 프로그램**이다.
kindnetd가 HTTP나 gRPC 호출을 읽어 분산하는 것은 아니다.
이 실험에서는 기본 kube-proxy의 Service 경로와 gRPC 클라이언트의 선택을 구분한다.
`Headless`는 Service VIP 대신 Pod 주소를 발견하도록 하고, `round_robin`은 그 주소들에 연결해 RPC를 선택한다.

[처음 보는 사람을 위한 설명](docs/START_HERE.ko.md) · [실험 조건과 판정 기준](docs/EXPERIMENTS.ko.md) · [다음 단계](docs/ROADMAP.md)

## 안전 경계와 범위

기존 `go-grpc-kit`, `mutual-tls-in-go`를 수정하거나 복사하지 않는다. 첫 버전은 표준 grpc-go를 직접 사용한다.
기존 애플리케이션의 서비스 계약을 여기로 옮기지 않는다. gRPC 측정 메시지는 표준 `Health/Check` 단항 RPC이고, 응답 메타데이터로 Pod 이름을 받는다.

클러스터 이름은 `k8s-transport-lb-lab` 계열만 허용한다. 전용 kubeconfig와 로컬/클러스터 소유권 토큰을 대조하며,
소유권이 확인되지 않은 기존 클러스터는 재사용하거나 삭제하지 않는다. 기본 kubectl 컨텍스트는 바꾸지 않는다.
보고서에 kubeconfig나 인증 토큰을 넣지 않는다. 전용 클러스터 밖으로 포트를 노출하지 않는다.

처음부터 Cilium·Envoy·메시·mTLS를 요구하지 않는다. **첫 버전은 plaintext 단항 호출의 분산 의미 검증**이며 운영 보안 구성으로 사용하면 안 된다.
Pod 증설/삭제, DNS 재탐색, 스트리밍, mTLS, HTTP/2 대조군, 프록시와 CNI 교체 실험은 아직 구현하지 않았다.

## 고정 버전

실행 기준: `versions.env`의 kind v0.33.0, Kubernetes v1.36.4 노드 이미지(다이제스트 고정), Go 1.26.11.
gRPC는 `go.mod`의 v1.85.0이며 `go.sum`을 포함한다. 실제 실행 버전·커밋·이미지·EndpointSlice·Pod 전후 상태를 보고서와 함께 저장한다.
컨테이너 빌더는 Go 버전 태그로 고정되어 있지만 이미지 다이제스트까지 고정한 것은 아니므로 비트 단위 동일 빌드를 보장하지 않는다.

## 공식 참고 문서

- [kind 네트워크 구성](https://kind.sigs.k8s.io/docs/user/configuration/)
- [Kubernetes Service](https://kubernetes.io/docs/concepts/services-networking/service/)
- [gRPC 서비스 설정](https://grpc.io/docs/guides/service-config/)
- [gRPC load balancing](https://grpc.io/docs/guides/custom-load-balancing/)
