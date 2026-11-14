package probe

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"github.com/HeaInSeo/k8s-transport-lb-lab/internal/echo"
	"io"
	"net/http"
	"time"
)

func runHTTP(ctx context.Context, c Config, reuse bool) Result {
	id, label := "http-new", "HTTP/1.1 · 요청마다 새 연결"
	if reuse {
		id, label = "http-reuse", "HTTP/1.1 · 단일 연결 재사용"
	}
	r := newResult(id, label, c.HTTPURL, c.Requests)
	d := &dialStats{}
	// No environment proxy, no HTTP/2, sequential calls, at most one H1 connection.
	tr := &http.Transport{DialContext: d.dial, DisableKeepAlives: !reuse, MaxConnsPerHost: 1, MaxIdleConns: 1, MaxIdleConnsPerHost: 1, IdleConnTimeout: 90 * time.Second, TLSNextProto: map[string]func(string, *tls.Conn) http.RoundTripper{}, ForceAttemptHTTP2: false}
	defer tr.CloseIdleConnections()
	client := &http.Client{Transport: tr, Timeout: c.RequestTimeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	call := func(callctx context.Context) (string, error) {
		req, err := http.NewRequestWithContext(callctx, http.MethodGet, c.HTTPURL, nil)
		if err != nil {
			return "", err
		}
		resp, err := client.Do(req)
		if err != nil {
			return "", err
		}
		defer resp.Body.Close()
		// Fully consume the small body so keep-alive reuse really occurs.
		body, err := io.ReadAll(io.LimitReader(resp.Body, 65537))
		if err != nil {
			return "", err
		}
		if len(body) > 65536 {
			return "", fmt.Errorf("oversized response")
		}
		if resp.StatusCode != http.StatusOK || resp.ProtoMajor != 1 {
			return "", fmt.Errorf("unexpected HTTP response: %s / %s", resp.Status, resp.Proto)
		}
		var reply echo.Reply
		if err := json.Unmarshal(body, &reply); err != nil {
			return "", err
		}
		if reply.Pod == "" {
			return "", fmt.Errorf("missing pod identity")
		}
		return reply.Pod, nil
	}
	warm, cancel := context.WithTimeout(ctx, c.WarmupTimeout)
	defer cancel()
	for {
		r.WarmupRequests++
		if _, err := call(warm); err == nil {
			break
		} else if warm.Err() != nil {
			return setupFailure(r, err, d)
		}
		if err := wait(warm, 100*time.Millisecond); err != nil {
			return setupFailure(r, err, d)
		}
	}
	before, start := d.opened.Load(), time.Now()
	for i := 0; i < c.Requests; i++ {
		t := time.Now()
		pod, err := call(ctx)
		r.record(t, pod, err)
		if ctx.Err() != nil {
			break
		}
	}
	r.finish(d, before, start, c.Replicas)
	return r
}
