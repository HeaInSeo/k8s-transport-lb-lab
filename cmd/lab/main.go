package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"github.com/HeaInSeo/k8s-transport-lb-lab/internal/echo"
	"github.com/HeaInSeo/k8s-transport-lb-lab/internal/probe"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run() error {
	if len(os.Args) < 2 {
		return fmt.Errorf("usage: lab server|probe")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	switch os.Args[1] {
	case "server":
		fs := flag.NewFlagSet("server", flag.ContinueOnError)
		hp := fs.String("http", ":8080", "HTTP listen address")
		gp := fs.String("grpc", ":50051", "gRPC listen address")
		pod := fs.String("pod", os.Getenv("HOSTNAME"), "backend identity")
		if err := fs.Parse(os.Args[2:]); err != nil {
			return err
		}
		s, err := echo.Start(*hp, *gp, *pod)
		if err != nil {
			return err
		}
		fmt.Fprintf(os.Stderr, "backend=%s http=%s grpc=%s (test-only plaintext)\n", *pod, s.HTTPAddress, s.GRPCAddress)
		var serveErr error
		select {
		case <-ctx.Done():
		case serveErr = <-s.Errors:
		}
		drain, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		s.Shutdown(drain)
		return serveErr
	case "probe":
		fs := flag.NewFlagSet("probe", flag.ContinueOnError)
		c := probe.Config{}
		fs.StringVar(&c.HTTPURL, "http", "http://echo.lb-lab.svc.cluster.local:8080/echo", "ClusterIP HTTP URL")
		fs.StringVar(&c.GRPCTarget, "grpc", "dns:///echo.lb-lab.svc.cluster.local:50051", "ClusterIP gRPC target")
		fs.StringVar(&c.HeadlessTarget, "headless", "dns:///echo-headless.lb-lab.svc.cluster.local:50051", "Headless gRPC target")
		fs.IntVar(&c.Requests, "requests", 600, "measured requests per scenario")
		fs.IntVar(&c.Replicas, "replicas", 3, "expected backend count")
		fs.DurationVar(&c.RequestTimeout, "timeout", 2*time.Second, "per-request deadline")
		fs.DurationVar(&c.WarmupTimeout, "warmup-timeout", 30*time.Second, "per-scenario preparation deadline")
		if err := fs.Parse(os.Args[2:]); err != nil {
			return err
		}
		report, err := probe.Run(ctx, c)
		if err != nil {
			return err
		}
		if err := json.NewEncoder(os.Stdout).Encode(report); err != nil {
			return err
		}
		if !report.Passed {
			return fmt.Errorf("scenario checks failed; inspect report")
		}
		return nil
	default:
		return fmt.Errorf("unknown subcommand: %s", os.Args[1])
	}
}
