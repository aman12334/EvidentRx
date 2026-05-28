{{/*
EvidentRx Helm template helpers
*/}}

{{/*
Expand the name of the chart.
*/}}
{{- define "evidentrx.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "evidentrx.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label.
*/}}
{{- define "evidentrx.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources.
*/}}
{{- define "evidentrx.labels" -}}
helm.sh/chart: {{ include "evidentrx.chart" . }}
app.kubernetes.io/name: {{ include "evidentrx.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: evidentrx-platform
{{- end }}

{{/*
Selector labels for a given component.
*/}}
{{- define "evidentrx.selectorLabels" -}}
app.kubernetes.io/name: {{ include "evidentrx.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Image reference with optional registry prefix.
*/}}
{{- define "evidentrx.image" -}}
{{- $registry := .Values.global.image.registry -}}
{{- $repo := .repo -}}
{{- $tag := .tag | default .Values.global.image.tag -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end }}
