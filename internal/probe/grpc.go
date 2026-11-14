package probe

import (
	"context"
	"fmt"
	"google.golang.org/grpc"
	_ "google.golang.org/grpc/balancer/roundrobin"
	"google.golang.org/grpc/credentials/insecure"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"
	"net"
	"time"
)

func runGRPC(ctx context.Context, c Config, roundRobin bool) Result {
	id, label, target, policy, needed := "grpc-clusterip", "gRPC · ClusterIP · 단일 연결", c.GRPCTarget, "pick_first", 1
	if roundRobin {
		id, label, target, policy, needed = "grpc-round-robin", "gRPC · Headless + round_robin", c.HeadlessTarget, "round_robin", c.Replicas
	}
	r := newResult(id, label, target, c.Requests)
	d := &dialStats{}
	conn, err := grpc.NewClient(target, grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithContextDialer(func(ctx context.Context, address string) (net.Conn, error) { return d.dial(ctx, "tcp", address) }), grpc.WithDisableRetry(), grpc.WithDisableServiceConfig(), grpc.WithDefaultServiceConfig(fmt.Sprintf(`{"loadBalancingConfig":[{%q:{}}]}`, policy)))
	if err != nil {
		return setupFailure(r, err, d)
	}
	defer conn.Close()
	client := healthpb.NewHealthClient(conn)
	call := func(parent context.Context) (string, error) {
		callctx, cancel := context.WithTimeout(parent, c.RequestTimeout)
		defer cancel()
		var headers metadata.MD
		resp, err := client.Check(callctx, &healthpb.HealthCheckRequest{}, grpc.Header(&headers))
		if err != nil {
			return "", err
		}
		if resp.Status != healthpb.HealthCheckResponse_SERVING {
			return "", fmt.Errorf("backend not SERVING")
		}
		ids := headers.Get("x-lab-pod")
		if len(ids) != 1 || ids[0] == "" {
			return "", fmt.Errorf("missing/ambiguous pod identity")
		}
		return ids[0], nil
	}
	// ClientConn READY does not imply every RR backend is ready. Observe all first.
	warm, cancel := context.WithTimeout(ctx, c.WarmupTimeout)
	defer cancel()
	seen := map[string]bool{}
	for {
		r.WarmupRequests++
		pod, err := call(warm)
		if err == nil {
			seen[pod] = true
		}
		if len(seen) >= needed {
			break
		}
		if err := wait(warm, 50*time.Millisecond); err != nil {
			return setupFailure(r, fmt.Errorf("observed %d/%d backends: %w", len(seen), needed, err), d)
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
