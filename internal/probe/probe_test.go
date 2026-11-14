package probe

import (
	"context"
	"fmt"
	"github.com/HeaInSeo/k8s-transport-lb-lab/internal/echo"
	"google.golang.org/grpc/resolver"
	"google.golang.org/grpc/resolver/manual"
	"testing"
	"time"
)

func backend(t *testing.T, id string) *echo.Server {
	t.Helper()
	s, e := echo.Start("127.0.0.1:0", "127.0.0.1:0", id)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		s.Shutdown(ctx)
	})
	return s
}
func config() Config {
	return Config{HTTPURL: "http://example.invalid/echo", GRPCTarget: "passthrough:///127.0.0.1:1", HeadlessTarget: "passthrough:///127.0.0.1:1", Requests: 90, Replicas: 3, RequestTimeout: time.Second, WarmupTimeout: 5 * time.Second}
}
func TestHTTPConnectionControl(t *testing.T) {
	s := backend(t, "one")
	c := config()
	c.HTTPURL = "http://" + s.HTTPAddress + "/echo"
	for _, reuse := range []bool{false, true} {
		t.Run(fmt.Sprint(reuse), func(t *testing.T) {
			r := runHTTP(context.Background(), c, reuse)
			if r.Success != 90 || r.Failures != 0 {
				t.Fatalf("%+v", r)
			}
			if reuse {
				if r.TCPConnections != 1 || r.MeasurementConnections != 0 || !r.Passed {
					t.Fatalf("keepalive not controlled: %+v", r)
				}
			} else {
				if r.TCPConnections != 91 || r.MeasurementConnections != 90 {
					t.Fatalf("new connections not controlled: %+v", r)
				}
				if r.Passed {
					t.Fatal("single backend must not pass a three-backend distribution check")
				}
			}
		})
	}
}
func TestGRPCSingleConnection(t *testing.T) {
	s := backend(t, "one")
	c := config()
	c.GRPCTarget = "passthrough:///" + s.GRPCAddress
	r := runGRPC(context.Background(), c, false)
	if !r.Passed || r.Success != 90 || r.TCPConnections != 1 {
		t.Fatalf("%+v", r)
	}
}
func TestGRPCRoundRobinThreeBackends(t *testing.T) {
	c := config()
	b := manual.NewBuilderWithScheme("lab-test-rr")
	var addresses []resolver.Address
	for i := 0; i < 3; i++ {
		s := backend(t, fmt.Sprintf("pod-%d", i))
		addresses = append(addresses, resolver.Address{Addr: s.GRPCAddress})
	}
	b.InitialState(resolver.State{Addresses: addresses})
	resolver.Register(b)
	c.HeadlessTarget = "lab-test-rr:///backends"
	r := runGRPC(context.Background(), c, true)
	if !r.Passed || r.TCPConnections != 3 {
		t.Fatalf("%+v", r)
	}
	for pod, n := range r.Pods {
		if n != 30 {
			t.Errorf("%s: got %d want 30", pod, n)
		}
	}
}
func TestSetupFailureDoesNotInventMeasurements(t *testing.T) {
	c := config()
	c.WarmupTimeout = 100 * time.Millisecond
	r := runGRPC(context.Background(), c, false)
	if r.Passed || r.Success != 0 || len(r.Pods) != 0 || len(r.ErrorSamples) == 0 {
		t.Fatalf("%+v", r)
	}
}
func TestCancelledContext(t *testing.T) {
	c := config()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	r := runGRPC(ctx, c, true)
	if r.Passed || r.Success != 0 {
		t.Fatalf("%+v", r)
	}
}
func TestRejectUnevenRoundRobin(t *testing.T) {
	r := Result{ID: "grpc-round-robin", Requests: 90, Success: 90, Pods: map[string]int{"a": 70, "b": 10, "c": 10}, TCPConnections: 3}
	r.evaluate(3)
	if r.Passed {
		t.Fatal("uneven RR passed")
	}
}
func TestRejectFailureDespiteAllBackends(t *testing.T) {
	r := Result{ID: "http-new", Requests: 90, Success: 89, Failures: 1, Pods: map[string]int{"a": 30, "b": 30, "c": 29}, MeasurementConnections: 90}
	r.evaluate(3)
	if r.Passed {
		t.Fatal("failed request hidden")
	}
}
func TestValidate(t *testing.T) {
	c := config()
	if e := c.Validate(); e != nil {
		t.Fatal(e)
	}
	c.Requests = 0
	if e := c.Validate(); e == nil {
		t.Fatal("zero requests accepted")
	}
	c = config()
	c.Replicas = 0
	if e := c.Validate(); e == nil {
		t.Fatal("zero replicas accepted")
	}
}
func TestPercentiles(t *testing.T) {
	v := []float64{1, 2, 3, 4, 5}
	if percentile(v, .5) != 3 || percentile(v, .95) != 5 || percentile(nil, .99) != 0 {
		t.Fatal("percentile implementation")
	}
}
func TestRejectEmptyBackendIdentity(t *testing.T) {
	if s, e := echo.Start("127.0.0.1:0", "127.0.0.1:0", ""); e == nil || s != nil {
		t.Fatal("empty identity accepted")
	}
}
