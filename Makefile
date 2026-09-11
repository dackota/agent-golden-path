# Agent Golden Path. Local platform on kind. Run `make help`.
CTX     := kind-agent-spike
CLUSTER := agent-spike
REPO    := https://github.com/dackota/agent-golden-path.git

.PHONY: help up cluster platform tools argocd status observe test evals evals-now down

help:          ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

up: cluster platform tools argocd  ## everything: cluster, platform, tools, Argo CD, demo agents

cluster:       ## create the kind cluster if missing
	kind get clusters | grep -q '^$(CLUSTER)$$' || kind create cluster --name $(CLUSTER) --wait 120s

platform:      ## install or upgrade every platform-owned piece
	kubectl --context $(CTX) apply -f platform/observability/otel-lgtm.yaml
	helm --kube-context $(CTX) upgrade -i otel-collector opentelemetry-collector --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
	  --version 0.173.0 -n platform-observability -f platform/observability/otel-collector-values.yaml
	kubectl --context $(CTX) create ns kagent --dry-run=client -o yaml | kubectl --context $(CTX) apply -f -
	helm --kube-context $(CTX) upgrade --install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds --version 0.10.0 -n kagent
	helm --kube-context $(CTX) upgrade --install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent --version 0.10.0 -n kagent \
	  -f platform/kagent/values.yaml --wait --timeout 6m
	kubectl --context $(CTX) apply -f platform/kagent/tool-catalog-sharing.yaml
	kubectl --context $(CTX) apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v1.0.1/sandbox-with-extensions.yaml
	kubectl --context $(CTX) apply -f platform/sandbox/platform-python-template.yaml
	kubectl --context $(CTX) apply -f platform/gateway/gateway.yaml
	kubectl --context $(CTX) -n platform-gateway rollout status deploy/litellm --timeout=300s
	kubectl --context $(CTX) apply --server-side --force-conflicts -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.0/standard-install.yaml
	helm --kube-context $(CTX) upgrade -i agentgateway-crds oci://cr.agentgateway.dev/charts/agentgateway-crds --create-namespace -n agentgateway-system --version v1.5.0
	helm --kube-context $(CTX) upgrade -i agentgateway oci://cr.agentgateway.dev/charts/agentgateway -n agentgateway-system --version v1.5.0 --wait
	kubectl --context $(CTX) apply -f platform/gateway/agentgateway-proxy.yaml
	kubectl --context $(CTX) apply -f platform/gateway/tools-gateway.yaml
	./platform/gateway/tools-jwks.sh $(CTX)
	kubectl --context $(CTX) apply -f platform/budgets/team-budgets.yaml
	kubectl --context $(CTX) apply -f platform/minter/minter.yaml
	kubectl --context $(CTX) apply -f platform/evals/cronjob.yaml

tools:         ## install the shared tool servers and their platform credentials
	kubectl --context $(CTX) apply -f platform/tools/orders.yaml
	kubectl --context $(CTX) apply -f platform/tools/github-prs.yaml
	./platform/tools/github-token.sh $(CTX)
	kubectl --context $(CTX) -n kagent rollout status deploy/github-prs --timeout=300s

argocd:        ## install Argo CD and the agents ApplicationSet
	kubectl --context $(CTX) create ns argocd --dry-run=client -o yaml | kubectl --context $(CTX) apply -f -
	kubectl --context $(CTX) apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/v3.2.0/manifests/install.yaml
	kubectl --context $(CTX) -n argocd rollout status deploy/argocd-server --timeout=300s
	kubectl --context $(CTX) apply -f platform/argocd/

status:        ## what Argo and the minter have done
	kubectl --context $(CTX) get applications -n argocd
	kubectl --context $(CTX) get agents,deploy,cronjob,sandboxclaims,httproute,secrets -n team-demo
	kubectl --context $(CTX) get agentgatewaypolicy -n platform-gateway

observe:       ## open Grafana for traces, metrics, and the prompt audit log
	@echo "Grafana: http://localhost:3000  (Explore -> Tempo for traces, Loki for the audit stream)"
	kubectl --context $(CTX) -n platform-observability port-forward svc/otel-lgtm 3000:3000

evals:         ## run every agent's evals on this laptop against Ollama, prompt mode, no cluster needed
	python3 evals/build.py build --mode prompt --out build/evals --ollama http://localhost:11434 --model gemma4:12b --grader gemma4:12b
	@for d in build/evals/*/; do echo "== $$d"; (cd $$d && OLLAMA_BASE_URL=http://localhost:11434 npx -y promptfoo@0.123.0 eval -c promptfooconfig.yaml -o results.json --no-cache --no-progress-bar >/dev/null; \
	  python3 ../../../evals/build.py report --agent agent.json --results results.json) || true; done

evals-now:     ## run the nightly evals job in the cluster right now and follow it
	kubectl --context $(CTX) -n platform-observability create job evals-manual-$$(date +%s) --from=cronjob/evals
	kubectl --context $(CTX) -n platform-observability wait --for=condition=ready pod -l job-name --timeout=120s >/dev/null 2>&1 || true
	kubectl --context $(CTX) -n platform-observability logs -f -l job-name --tail=-1

test:          ## unit tests, chart lint, render every agent, schema negative tests
	python3 -m unittest discover -s tests
	helm lint charts/agent -f deployments/agents/demo/vibe-app/values.yaml
	for v in deployments/agents/*/*/values.yaml; do helm template t charts/agent -f $$v >/dev/null && echo "render ok $$v"; done
	tests/schema-tests.sh

down:          ## delete the kind cluster
	kind delete cluster --name $(CLUSTER)
