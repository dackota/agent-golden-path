{{- define "agent.labels" -}}
app.kubernetes.io/name: {{ .Values.name }}
app.kubernetes.io/managed-by: agent-app-chart
platform.suresteel.com/team: {{ .Values.team }}
platform.suresteel.com/kind: container
{{- end }}
