export interface Scenario { id: string; service: string; alert: string }
export interface Hypothesis { cause: string; title: string; score: number; confidence: number; evidence_ids: string[]; explanation: string }
export interface Report {
  service: string | null; onset: number | null; summary: string; hypotheses: Hypothesis[];
  next_steps: string[]; remediation: { action: string; requires_approval: boolean; status: string };
  tools_used: string[]; failures: string[];
  llm: { used: boolean; model?: string; input_tokens?: number; output_tokens?: number; cost_usd?: number };
}
export interface Investigation { id: number; incident_id: number; status: string; approval: string; report: Report }
export interface InvestigationRow { id: number; incident_id: number; status: string; approval: string; top_cause: string | null }
export interface Evidence { id: string; tool: string; summary: string; supports: string[]; weight: number }
export interface Metrics {
  investigations: number; avg_duration_s: number; agent_failures: number;
  human_escalations: number; awaiting_approval: number;
}

export const getKey = () => localStorage.getItem("tracepilot_api_key") ?? "";
export const setKey = (k: string) => localStorage.setItem("tracepilot_api_key", k);

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, { headers: { "content-type": "application/json", "x-api-key": getKey() }, ...init });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  scenarios: () => req<Scenario[]>("/scenarios"),
  metrics: () => req<Metrics>("/metrics"),
  investigations: () => req<InvestigationRow[]>("/investigations"),
  investigation: (id: number) => req<Investigation>(`/investigations/${id}`),
  evidence: (id: number) => req<Evidence[]>(`/investigations/${id}/evidence`),
  investigate: async (title: string, scenario_id: string) => {
    const inc = await req<{ id: number }>("/incidents", post({ title, scenario_id }));
    return req<{ id: number }>("/investigations", post({ incident_id: inc.id }));
  },
  approve: (id: number, approved: boolean) =>
    req<{ approval: string }>(`/investigations/${id}/approve`, post({ approved })),
};
