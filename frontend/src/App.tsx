import { useCallback, useEffect, useState } from "react";
import { api, getKey, setKey, Evidence, Investigation, InvestigationRow, Metrics, Scenario } from "./api";

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-slate-900 p-4">
      <div className="text-xs uppercase text-slate-400">{label}</div>
      <div className="text-2xl font-semibold">{value}</div>
    </div>
  );
}

export default function App() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenarioId, setScenarioId] = useState("");
  const [title, setTitle] = useState("Checkout API latency increased after today's deployment");
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [rows, setRows] = useState<InvestigationRow[]>([]);
  const [selected, setSelected] = useState<Investigation | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [key, setKeyState] = useState(getKey());

  const refresh = useCallback(async () => {
    const [m, r] = await Promise.all([api.metrics(), api.investigations()]);
    setMetrics(m);
    setRows(r);
  }, []);

  const open = useCallback(async (id: number) => {
    const [inv, ev] = await Promise.all([api.investigation(id), api.evidence(id)]);
    setSelected(inv);
    setEvidence(ev);
  }, []);

  const guard = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try { await fn(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }, []);

  useEffect(() => {
    guard(async () => {
      const s = await api.scenarios();
      setScenarios(s);
      setScenarioId(s[0]?.id ?? "");
      await refresh();
    });
  }, [guard, refresh]);

  const run = () => guard(async () => {
    const { id } = await api.investigate(title, scenarioId);
    await Promise.all([refresh(), open(id)]);
  });

  const decide = (approved: boolean) => selected && guard(async () => {
    await api.approve(selected.id, approved);
    await Promise.all([refresh(), open(selected.id)]);
  });

  const r = selected?.report;
  return (
    <main className="mx-auto max-w-6xl space-y-6 p-6">
      <header>
        <h1 className="text-2xl font-bold">TracePilot AI</h1>
        <p className="text-sm text-slate-400">Incident investigation and root-cause analysis. Read-only: humans approve every remediation.</p>
        <input type="password" placeholder="API key (if auth is enabled)" className="mt-2 w-72 rounded bg-slate-800 p-2 text-sm"
          value={key} onChange={(e) => { setKeyState(e.target.value); setKey(e.target.value); }} onBlur={() => guard(refresh)} />
      </header>

      {error && <div className="rounded bg-red-900/60 p-3 text-sm">{error}</div>}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Investigations" value={metrics?.investigations ?? "-"} />
        <Stat label="Avg duration (s)" value={metrics ? metrics.avg_duration_s.toFixed(3) : "-"} />
        <Stat label="Awaiting approval" value={metrics?.awaiting_approval ?? "-"} />
        <Stat label="Agent failures" value={metrics?.agent_failures ?? "-"} />
        <Stat label="Human escalations" value={metrics?.human_escalations ?? "-"} />
      </section>

      <section className="space-y-3 rounded-lg bg-slate-900 p-4">
        <h2 className="font-semibold">New investigation</h2>
        <input className="w-full rounded bg-slate-800 p-2" value={title} onChange={(e) => setTitle(e.target.value)} />
        <div className="flex gap-3">
          <select className="flex-1 rounded bg-slate-800 p-2" value={scenarioId} onChange={(e) => setScenarioId(e.target.value)}>
            {scenarios.map((s) => <option key={s.id} value={s.id}>{s.id} - {s.service}</option>)}
          </select>
          <button className="rounded bg-indigo-600 px-4 py-2 disabled:opacity-50" disabled={busy || !scenarioId || !title} onClick={run}>
            {busy ? "Working..." : "Investigate"}
          </button>
        </div>
      </section>

      <div className="grid gap-6 md:grid-cols-3">
        <section className="rounded-lg bg-slate-900 p-4">
          <h2 className="mb-2 font-semibold">Investigations</h2>
          <ul className="space-y-1 text-sm">
            {rows.length === 0 && <li className="text-slate-500">None yet</li>}
            {rows.map((x) => (
              <li key={x.id}>
                <button className="w-full rounded p-2 text-left hover:bg-slate-800" onClick={() => guard(() => open(x.id))}>
                  #{x.id} {x.top_cause ?? "no hypothesis"}
                  <span className="block text-xs text-slate-400">{x.status} / {x.approval}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section className="space-y-4 rounded-lg bg-slate-900 p-4 md:col-span-2">
          {!r ? <p className="text-slate-500">Select or start an investigation.</p> : (
            <>
              <div>
                <h2 className="font-semibold">Report #{selected.id}</h2>
                <p className="text-sm">{r.summary}</p>
                <p className="text-xs text-slate-400">Service: {r.service ?? "unknown"} / onset: t+{r.onset ?? "?"}min / tools: {r.tools_used.join(", ")}</p>
                {r.llm?.used && <p className="text-xs text-slate-400">Claude {r.llm.model}: {r.llm.input_tokens}+{r.llm.output_tokens} tokens, ${r.llm.cost_usd}</p>}
              </div>
              <div className="space-y-2">
                {r.hypotheses.map((h, i) => (
                  <div key={h.cause} className="rounded bg-slate-800 p-3">
                    <div className="flex justify-between">
                      <span>{i + 1}. {h.title}</span>
                      <span>{Math.round(h.confidence * 100)}%</span>
                    </div>
                    <div className="h-1 rounded bg-slate-700"><div className="h-1 rounded bg-indigo-500" style={{ width: `${h.confidence * 100}%` }} /></div>
                    {h.explanation && <div className="mt-1 text-sm">{h.explanation}</div>}
                    <div className="mt-1 text-xs text-slate-400">Evidence: {h.evidence_ids.join(", ")}</div>
                  </div>
                ))}
              </div>
              <div className="rounded border border-amber-600/50 p-3 text-sm">
                <div className="font-medium">Proposed remediation (not executed)</div>
                <div>{r.remediation.action}</div>
                <div className="text-xs text-slate-400">Next: {r.next_steps.join(" ")}</div>
                <div className="mt-2 flex items-center gap-2">
                  <button className="rounded bg-green-700 px-3 py-1 disabled:opacity-50" disabled={busy} onClick={() => decide(true)}>Approve</button>
                  <button className="rounded bg-red-700 px-3 py-1 disabled:opacity-50" disabled={busy} onClick={() => decide(false)}>Reject</button>
                  <span className="text-xs text-slate-400">Decision: {selected.approval}</span>
                </div>
              </div>
              {r.failures.length > 0 && <div className="text-sm text-red-300">Failures: {r.failures.join("; ")}</div>}
              <div>
                <h3 className="mb-1 text-sm font-semibold">Evidence</h3>
                <ul className="space-y-1 text-xs">
                  {evidence.map((e) => (
                    <li key={e.id}><span className="font-mono text-indigo-300">{e.id}</span> [{e.tool}] {e.summary}</li>
                  ))}
                </ul>
              </div>
            </>
          )}
        </section>
      </div>
    </main>
  );
}
