export interface ReasoningTrace {
  trace_id:         string;
  case_id:          string;
  agent_id:         string;
  agent_type:       string;
  workflow_node:    string;
  workflow_step:    number;
  confidence_score: number | null;
  input_context:    Record<string, unknown>;
  output_summary:   string | null;
  created_at:       string | null;
}

export interface AgentRun {
  run_id:            string;
  case_id:           string;
  agent_type:        string;
  status:            string;
  input_tokens:      number;
  output_tokens:     number;
  cache_read_tokens: number;
  latency_ms:        number | null;
  started_at:        string | null;
  completed_at:      string | null;
  output:            Record<string, unknown>;
}

export interface ConfidencePropagation {
  node:       string;
  label:      string;
  confidence: number | null;
  delta:      number | null;
}

export interface WorkflowTrace {
  case_id:                 string;
  total_traces:            number;
  agent_runs:              AgentRun[];
  reasoning_traces:        ReasoningTrace[];
  confidence_chain:        ConfidencePropagation[];
  total_input_tokens:      number;
  total_output_tokens:     number;
  escalation_recommended:  boolean | null;
  executive_summary:       string | null;
}
