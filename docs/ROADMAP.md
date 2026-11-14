# 단계별 확장

## v0.1: 구현된 기준선

- [x] kindnetd + kube-proxy iptables 전용 클러스터
- [x] HTTP/1.1 새 연결 / 단일 연결 재사용
- [x] gRPC ClusterIP / Headless + round_robin
- [x] 준비 장벽, 연결 계측, 오류와 Pod별 분포
- [x] 오프라인 HTML, Markdown, JSON과 실행 증거
- [x] 전용 kubeconfig, 소유권 확인, 보호 로직 테스트
- [x] GitHub Actions 실행 경로

체크는 구현 여부다. 특정 커밋의 실제 kind 통과 여부는 그 커밋의 Actions 결과를 확인한다.

## v0.2: 다음 후보 (아직 구현하지 않음)

- [ ] HTTP/2 일반 HTTP 대조군과 다중 연결/동시성 분리 실험
- [ ] 서버 3→5 증설: Headless DNS 갱신 지연과 새 백엔드 편입 시간 측정
- [ ] Pod 제거: 연결 복구와 RPC 오류, deadline, 중복 실행 위험 분리
- [ ] 장기 스트림: 새 스트림 분산과 진행 중 스트림의 고정성
- [ ] go-grpc-kit 버전을 고정한 선택적 재사용 경로

## v0.3: 선택적인 네트워크·보안 비교 (아직 구현하지 않음)

- [ ] Envoy L7: 단일 클라이언트 연결에서도 RPC별 분산
- [ ] 동일 kindnetd에서 kube-proxy nftables 비교
- [ ] 별도 클러스터에서 Cilium L4/L7을 구분한 비교
- [ ] mTLS/SAN/ServerName 검증과 인증서 교체

어떤 확장도 기본 `make demo`에 필수 메시/CNI/인증서 설치를 추가하지 않는다.
특정 CNI가 없어도 작동하는 경로를 검증하는 것과 런타임 자동 폴백 구현은 별개다.
