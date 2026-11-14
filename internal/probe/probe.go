// Package probe separates observations from expected steady-state behavior.
package probe

import (
	"context"
	"errors"
	"fmt"
	"math"
	"net"
	"sort"
	"sync/atomic"
	"time"
)

type Config struct {
	HTTPURL, GRPCTarget, HeadlessTarget string
	Requests, Replicas                  int
	RequestTimeout, WarmupTimeout       time.Duration
}
type Result struct {
	ID                     string         `json:"id"`
	Label                  string         `json:"label"`
	Target                 string         `json:"target"`
	Requests               int            `json:"requests"`
	Success                int            `json:"success"`
	Failures               int            `json:"failures"`
	Pods                   map[string]int `json:"pods"`
	DialAttempts           int64          `json:"dial_attempts_total"`
	TCPConnections         int64          `json:"tcp_connections_total"`
	MeasurementConnections int64          `json:"tcp_connections_during_measurement"`
	WarmupRequests         int            `json:"warmup_requests"`
	P50MS                  float64        `json:"p50_ms"`
	P95MS                  float64        `json:"p95_ms"`
	P99MS                  float64        `json:"p99_ms"`
	ElapsedMS              float64        `json:"elapsed_ms"`
	Passed                 bool           `json:"passed"`
	Explanation            string         `json:"explanation"`
	ErrorSamples           []string       `json:"error_samples,omitempty"`
	samples                []float64
}
type Report struct {
	SchemaVersion    int      `json:"schema_version"`
	StartedAt        string   `json:"started_at"`
	FinishedAt       string   `json:"finished_at"`
	ExpectedReplicas int      `json:"expected_replicas"`
	Passed           bool     `json:"passed"`
	Results          []Result `json:"results"`
}
type dialStats struct{ attempts, opened atomic.Int64 }

func (d *dialStats) dial(ctx context.Context, network, address string) (net.Conn, error) {
	d.attempts.Add(1)
	c, err := (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext(ctx, network, address)
	if err == nil {
		d.opened.Add(1)
	}
	return c, err
}
func (c Config) Validate() error {
	if c.Replicas < 2 || c.Replicas > 20 {
		return errors.New("replicas must be between 2 and 20")
	}
	if c.Requests < c.Replicas*30 || c.Requests > 100000 {
		return errors.New("requests must be >= replicas*30 and <= 100000")
	}
	if c.RequestTimeout <= 0 || c.WarmupTimeout <= 0 {
		return errors.New("timeouts must be positive")
	}
	if c.HTTPURL == "" || c.GRPCTarget == "" || c.HeadlessTarget == "" {
		return errors.New("all three targets are required")
	}
	return nil
}
func Run(ctx context.Context, c Config) (Report, error) {
	if err := c.Validate(); err != nil {
		return Report{}, err
	}
	out := Report{SchemaVersion: 1, StartedAt: time.Now().UTC().Format(time.RFC3339Nano), ExpectedReplicas: c.Replicas, Passed: true}
	out.Results = append(out.Results, runHTTP(ctx, c, false), runHTTP(ctx, c, true), runGRPC(ctx, c, false), runGRPC(ctx, c, true))
	for _, r := range out.Results {
		out.Passed = out.Passed && r.Passed
	}
	out.FinishedAt = time.Now().UTC().Format(time.RFC3339Nano)
	return out, nil
}
func newResult(id, label, target string, n int) Result {
	return Result{ID: id, Label: label, Target: target, Requests: n, Pods: map[string]int{}}
}
func (r *Result) noteError(err error) {
	if len(r.ErrorSamples) < 5 {
		r.ErrorSamples = append(r.ErrorSamples, err.Error())
	}
}
func (r *Result) record(start time.Time, pod string, err error) {
	if err != nil {
		r.Failures++
		r.noteError(err)
		return
	}
	if pod == "" {
		r.Failures++
		r.noteError(errors.New("empty backend identity"))
		return
	}
	r.Success++
	r.Pods[pod]++
	r.samples = append(r.samples, float64(time.Since(start).Microseconds())/1000)
}
func percentile(sorted []float64, p float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	return sorted[int(math.Ceil(p*float64(len(sorted))))-1]
}
func (r *Result) finish(d *dialStats, before int64, start time.Time, replicas int) {
	r.DialAttempts, r.TCPConnections = d.attempts.Load(), d.opened.Load()
	r.MeasurementConnections = r.TCPConnections - before
	r.ElapsedMS = float64(time.Since(start).Microseconds()) / 1000
	sort.Float64s(r.samples)
	r.P50MS, r.P95MS, r.P99MS = percentile(r.samples, .50), percentile(r.samples, .95), percentile(r.samples, .99)
	r.evaluate(replicas)
}
func (r *Result) evaluate(replicas int) {
	r.Passed = false
	if r.Success != r.Requests || r.Failures != 0 {
		r.Explanation = "요청 실패 또는 준비 실패: 분산 성공으로 판정하지 않음"
		return
	}
	switch r.ID {
	case "http-new":
		r.Passed = len(r.Pods) == replicas && r.MeasurementConnections == int64(r.Requests)
		r.Explanation = "매 요청 새 TCP 연결: 모든 서버 도달, 측정 연결 수 = 요청 수. 정확한 균등 분포는 요구하지 않음."
	case "http-reuse", "grpc-clusterip":
		r.Passed = len(r.Pods) == 1 && r.TCPConnections == 1
		r.Explanation = "안정된 단일 TCP 연결: 하나의 Pod에 집중되는 것이 이 대조 실험의 정상 결과."
	case "grpc-round-robin":
		r.Passed = len(r.Pods) == replicas && r.TCPConnections == int64(replicas)
		expected := float64(r.Requests) / float64(replicas)
		for _, n := range r.Pods {
			if math.Abs(float64(n)-expected) > expected*.20 {
				r.Passed = false
			}
		}
		r.Explanation = "전체 Pod 준비 후 호출: 모든 Pod 도달, Pod별 TCP 연결 1개, 요청 수가 균등 기대치의 ±20% 이내."
	default:
		r.Explanation = "알 수 없는 실험"
	}
}
func wait(ctx context.Context, d time.Duration) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(d):
		return nil
	}
}
func setupFailure(r Result, err error, d *dialStats) Result {
	r.noteError(fmt.Errorf("warmup/setup: %w", err))
	r.DialAttempts, r.TCPConnections = d.attempts.Load(), d.opened.Load()
	r.Explanation = "준비 실패: 측정 미실행. 성공 결과나 균등 분포를 만들어내지 않음."
	return r
}
