# Agent Golden Path. Local platform on kind. Run `make help`.
CTX     := kind-agent-spike
CLUSTER := agent-spike
REPO    := https://github.com/dackota/agent-golden-path.git

.PHONY: help up cluster platform argocd status test down

help:          ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

up: cluster platform argocd  ## everything: cluster, platform, Argo CD, demo agents

cluster:       ## create the kind cluster if missing
	kind get clusters | grep -q '^$(CLUSTER)$$' || kind create cluster --name $(CLUSTER) --wait 120s

platform:      ## install or upgrade every platform-owned piece
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

argocd:        ## install Argo CD and the agents ApplicationSet
	kubectl --context $(CTX) create ns argocd --dry-run=client -o yaml | kubectl --context $(CTX) apply -f -
	kubectl --context $(CTX) apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/v3.2.0/manifests/install.yaml
	kubectl --context $(CTX) -n argocd rollout status deploy/argocd-server --timeout=300s
	kubectl --context $(CTX) apply -f platform/argocd/

status:        ## what Argo and the minter have done
	kubectl --context $(CTX) get applications -n argocd
	kubectl --context $(CTX) get agents,deploy,cronjob,sandboxclaims,httproute,secrets -n team-demo
	kubectl --context $(CTX) get agentgatewaypolicy -n platform-gateway

test:          ## chart lint, render every agent, schema negative tests
	helm lint charts/agent -f deployments/agents/demo/vibe-app/values.yaml
	for v in deployments/agents/*/*/values.yaml; do helm template t charts/agent -f $$v >/dev/null && echo "render ok $$v"; done
	tests/schema-tests.sh

down:          ## delete the kind cluster
	kind delete cluster --name $(CLUSTER)
