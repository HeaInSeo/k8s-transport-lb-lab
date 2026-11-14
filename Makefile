.PHONY: help demo up smoke doctor clean test fmt ci install-kind
export GOCACHE := $(CURDIR)/.cache/go-build
help:
	@echo "make demo: cluster + four experiments + HTML/Markdown report"
	@echo "make doctor | up | smoke | clean | test | install-kind"
demo up smoke doctor clean:
	bash scripts/lab.sh $@
install-kind:
	bash scripts/install-kind.sh
fmt:
	gofmt -w cmd internal
test:
	go test -race -count=1 ./...
	python3 -m unittest discover -s tests -v
	bash -n scripts/lab.sh scripts/install-kind.sh
ci: test
	go vet ./...
	test -z "$$(gofmt -l cmd internal)"
