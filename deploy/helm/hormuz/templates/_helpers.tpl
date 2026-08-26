{{- define "hormuz.name" -}}
hormuz
{{- end -}}

{{- define "hormuz.fullname" -}}
{{- printf "%s-hormuz" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "hormuz.selectorLabels" -}}
app.kubernetes.io/name: {{ include "hormuz.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: gateway
{{- end -}}

{{- define "hormuz.labels" -}}
{{ include "hormuz.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "hormuz.image" -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- end -}}

{{- define "hormuz.validate" -}}
{{- if ne .Values.contract.schema "hormuz.kubernetes-profile.v1" -}}
{{- fail "contract.schema must be hormuz.kubernetes-profile.v1" -}}
{{- end -}}
{{- if ne .Values.contract.platform "linux/amd64" -}}
{{- fail "the v1 chart supports only linux/amd64" -}}
{{- end -}}
{{- if ne .Values.image.digest "sha256:8ac24f5c7afb8ce09ec133616de06702f568a2e70594d8034146a131d86e5b67" -}}
{{- fail "this chart version requires the exact signed Hormuz image digest" -}}
{{- end -}}
{{- if lt (int .Values.replicaCount) 2 -}}
{{- fail "the multi-replica reference requires at least two replicas" -}}
{{- end -}}
{{- if ge (int .Values.podDisruptionBudget.minAvailable) (int .Values.replicaCount) -}}
{{- fail "podDisruptionBudget.minAvailable must be lower than replicaCount" -}}
{{- end -}}
{{- end -}}
