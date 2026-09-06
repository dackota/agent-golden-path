# PROTOTYPE. Self-service agent golden path spike. Local kind cluster only.
CTX      := kind-agent-spike
CLUSTER  := agent-spike
AGENT    ?= intake/examples/faq-helper.yaml
NAME     := $(shell python3 -c 'import yaml,sys; print(yaml.safe_load(open("$(AGENT)"))["name"])')
KIND_    := $(shell python3 -c 'import yaml,sys; print(yaml.safe_load(open("$(AGENT)"))["kind"])')
NS       := team-$(shell python3 -c 'import yaml,sys; print(yaml.safe_load(open("$(AGENT)"))["team"])')

.PHONY: up platform deploy status down render mint

up:            ## create kind cluster + install platform pieces (kagent, agent-sandbox, gateway)
	kind get clusters | grep -q '^$(CLUSTER)$$' || kind create cluster --name $(CLUSTER) --wait 120s
	$(MAKE) platform

platform:      ## install/upgrade platform-owned pieces
	kubectl --context $(CTX) create ns kagent --dry-run=client -o yaml | kubectl --context $(CTX) apply -f -
	helm --kube-context $(CTX) upgrade --install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds --version 0.10.0 -n kagent
	helm --kube-context $(CTX) upgrade --install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent --version 0.10.0 -n kagent \
	  --set providers.default=ollama --set providers.ollama.model=gemma4:12b \
	  --set providers.ollama.config.host=host.docker.internal:11434 --wait --timeout 6m
	kubectl --context $(CTX) apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v1.0.1/sandbox-with-extensions.yaml
	kubectl --context $(CTX) apply -f platform/gateway/gateway.yaml
	kubectl --context $(CTX) -n platform-gateway rollout status deploy/litellm --timeout=300s
	kubectl --context $(CTX) apply --server-side --force-conflicts -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.0/standard-install.yaml
	helm --kube-context $(CTX) upgrade -i agentgateway-crds oci://cr.agentgateway.dev/charts/agentgateway-crds --create-namespace -n agentgateway-system --version v1.5.0
	helm --kube-context $(CTX) upgrade -i agentgateway oci://cr.agentgateway.dev/charts/agentgateway -n agentgateway-system --version v1.5.0 --wait
	kubectl --context $(CTX) apply -f platform/gateway/agentgateway-proxy.yaml
	kubectl --context $(CTX) apply -f platform/gateway/tools-gateway.yaml
	./platform/gateway/tools-jwks.sh $(CTX)
	kubectl --context $(CTX) apply -f platform/kagent/tool-catalog-sharing.yaml
	kubectl --context $(CTX) apply -f platform/sandbox/platform-python-template.yaml
	kubectl --context $(CTX) label ns team-demo platform.suresteel.com/agent-namespace=true --overwrite

render:        ## AGENT=<agent.yaml>  validate + render manifests to out/<name>/
	python3 intake/render.py $(AGENT) out/$(NAME)

mint: render   ## mint a budgeted gateway key for the agent (platform step)
	./platform/gateway/mint-key.sh $(CTX) out/$(NAME)

deploy: mint   ## AGENT=<agent.yaml>  the whole self-service path
ifeq ($(KIND_),prompt)
	kubectl --context $(CTX) apply -f out/$(NAME)/kagent.yaml
	kubectl --context $(CTX) label ns $(NS) platform.suresteel.com/agent-namespace=true --overwrite
	kubectl --context $(CTX) wait agent/$(NAME) -n $(NS) --for=condition=Ready --timeout=180s
else ifeq ($(KIND_),container)
	@if [ -f out/$(NAME)/tools-policy.yaml ]; then \
	  ./platform/gateway/mint-tool-token.sh $(CTX) out/$(NAME) && \
	  kubectl --context $(CTX) apply -f out/$(NAME)/tools-policy.yaml; fi
	helm --kube-context $(CTX) upgrade --install $(NAME) charts/agent-app -n $(NS) --create-namespace -f out/$(NAME)/helm-values.yaml --wait
	kubectl --context $(CTX) -n $(NS) rollout restart deploy/$(NAME) && kubectl --context $(CTX) -n $(NS) rollout status deploy/$(NAME) --timeout=120s
else
	kubectl --context $(CTX) apply -f out/$(NAME)/sandbox.yaml
	kubectl --context $(CTX) wait sandboxclaim/$(NAME) -n $(NS) --for=condition=Ready --timeout=120s
endif

status:        ## what is running for team-demo
	kubectl --context $(CTX) get agents,modelconfigs,sandboxclaims,sandboxes,deploy,networkpolicy,secrets -n team-demo

down:          ## delete the kind cluster
	kind delete cluster --name $(CLUSTER)
