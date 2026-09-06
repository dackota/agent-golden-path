{{/* Platform constants. Not knobs. */}}
{{- define "agent.gatewayUrl" -}}http://litellm.platform-gateway.svc.cluster.local:4000{{- end }}
{{- define "agent.toolsUrl" -}}http://agentgateway-proxy.agentgateway-system.svc.cluster.local/mcp/platform-tools{{- end }}
{{- define "agent.otelEndpoint" -}}http://otel-collector.platform-observability.svc.cluster.local:4318{{- end }}
{{- define "agent.credentialsSecret" -}}{{ .Values.name }}-platform-credentials{{- end }}
{{- define "agent.namespace" -}}team-{{ .Values.team }}{{- end }}

{{- define "agent.labels" -}}
app.kubernetes.io/name: {{ .Values.name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
goldenpath.dev/agent: "true"
goldenpath.dev/team: {{ .Values.team }}
goldenpath.dev/kind: {{ .Values.kind }}
{{- end }}

{{- define "agent.annotations" -}}
goldenpath.dev/owner: {{ .Values.owner | quote }}
goldenpath.dev/description: {{ .Values.description | quote }}
{{- end }}

{{/* Tool catalog: friendly name -> MCP server and the exact tool names it grants. Platform-maintained. */}}
{{- define "agent.catalog" -}}
{"k8s-readonly":    {"server": "kagent-tool-server", "tools": ["k8s_get_resources", "k8s_describe_resource", "k8s_get_pod_logs"]},
 "grafana":         {"server": "kagent-grafana-mcp", "tools": []},
 "web-fetch":       {"server": "kagent-tool-server", "tools": ["http_fetch"]},
 "orders-readonly": {"server": "orders-mcp", "tools": ["list_orders", "get_order"]},
 "orders-cancel":   {"server": "orders-mcp", "tools": ["cancel_order"]}}
{{- end }}

{{/* Flat, sorted, unique list of granted tool names as JSON. */}}
{{- define "agent.toolNames" -}}
{{- $cat := include "agent.catalog" . | fromJson -}}
{{- $names := list -}}
{{- range .Values.tools }}{{ if .name }}{{ range (index $cat .name).tools }}{{ $names = append $names . }}{{ end }}{{ end }}{{ end -}}
{{- $names | uniq | sortAlpha | toJson -}}
{{- end }}

{{- define "agent.resources" -}}
{{- $p := dict "small" (dict "cpu" "250m" "mem" "256Mi" "lcpu" "1" "lmem" "512Mi") "medium" (dict "cpu" "1" "mem" "1Gi" "lcpu" "2" "lmem" "2Gi") "large" (dict "cpu" "2" "mem" "4Gi" "lcpu" "4" "lmem" "8Gi") -}}
{{- $s := index $p .Values.size -}}
requests: {cpu: {{ $s.cpu | quote }}, memory: {{ $s.mem | quote }}}
limits: {cpu: {{ $s.lcpu | quote }}, memory: {{ $s.lmem | quote }}}
{{- end }}

{{- define "agent.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile: {type: RuntimeDefault}
{{- end }}

{{- define "agent.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: {drop: [ALL]}
{{- end }}

{{/* Env every container agent gets. Credentials come from the platform-minted Secret. */}}
{{- define "agent.env" -}}
- {name: PORT, value: {{ .Values.port | quote }}}
- {name: LLM_BASE_URL, value: {{ include "agent.gatewayUrl" . | quote }}}
- {name: LLM_MODEL, value: {{ .Values.model | quote }}}
- name: LLM_API_KEY
  valueFrom: {secretKeyRef: {name: {{ include "agent.credentialsSecret" . }}, key: LLM_API_KEY}}
{{- if .Values.tools }}
- {name: TOOLS_URL, value: {{ include "agent.toolsUrl" . | quote }}}
- name: TOOLS_TOKEN
  valueFrom: {secretKeyRef: {name: {{ include "agent.credentialsSecret" . }}, key: TOOLS_TOKEN}}
{{- end }}
- name: AGENT_SYSTEM_PROMPT
  valueFrom: {configMapKeyRef: {name: {{ .Values.name }}-prompt, key: system.txt}}
- {name: OTEL_SERVICE_NAME, value: {{ .Values.name | quote }}}
- {name: OTEL_EXPORTER_OTLP_ENDPOINT, value: {{ include "agent.otelEndpoint" . | quote }}}
- {name: OTEL_RESOURCE_ATTRIBUTES, value: "team={{ .Values.team }},platform.kind={{ .Values.kind }}"}
{{- range $k, $v := .Values.env }}
- {name: {{ $k }}, value: {{ $v | quote }}}
{{- end }}
{{- end }}
