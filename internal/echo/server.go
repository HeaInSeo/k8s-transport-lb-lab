// Package echo hosts a tiny HTTP/1.1 and unary gRPC test backend.
package echo

import (
	"context"
	"encoding/json"
	"errors"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"
	"net"
	"net/http"
	"sync"
	"time"
)

type Reply struct {
	Pod string `json:"pod"`
}
type Server struct {
	HTTPAddress, GRPCAddress string
	http                     *http.Server
	grpc                     *grpc.Server
	once                     sync.Once
	Errors                   chan error
}

// Start uses the standard Health/Check RPC, with backend identity in metadata.
func Start(httpAddress, grpcAddress, pod string) (*Server, error) {
	if pod == "" {
		return nil, errors.New("pod identity must not be empty")
	}
	hl, err := net.Listen("tcp", httpAddress)
	if err != nil {
		return nil, err
	}
	gl, err := net.Listen("tcp", grpcAddress)
	if err != nil {
		_ = hl.Close()
		return nil, err
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /echo", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(Reply{Pod: pod})
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) })
	hs := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 90 * time.Second}
	gs := grpc.NewServer(grpc.UnaryInterceptor(func(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		if err := grpc.SetHeader(ctx, metadata.Pairs("x-lab-pod", pod)); err != nil {
			return nil, err
		}
		return handler(ctx, req)
	}))
	h := health.NewServer()
	h.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)
	healthpb.RegisterHealthServer(gs, h)
	s := &Server{HTTPAddress: hl.Addr().String(), GRPCAddress: gl.Addr().String(), http: hs, grpc: gs, Errors: make(chan error, 2)}
	go func() {
		if e := hs.Serve(hl); e != nil && !errors.Is(e, http.ErrServerClosed) {
			s.Errors <- e
		}
	}()
	go func() {
		if e := gs.Serve(gl); e != nil && !errors.Is(e, grpc.ErrServerStopped) {
			s.Errors <- e
		}
	}()
	return s, nil
}

// Shutdown has a bounded drain, including when a client holds a connection open.
func (s *Server) Shutdown(ctx context.Context) {
	s.once.Do(func() {
		done := make(chan struct{})
		go func() { s.grpc.GracefulStop(); close(done) }()
		_ = s.http.Shutdown(ctx)
		select {
		case <-done:
		case <-ctx.Done():
			s.grpc.Stop()
			<-done
		}
		_ = s.http.Close()
	})
}
