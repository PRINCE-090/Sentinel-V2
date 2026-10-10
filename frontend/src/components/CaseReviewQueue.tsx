import { useEffect, useState } from "react";
import { Download, Save } from "lucide-react";
import { downloadCaseExport, getCases, SentinelApiError, updateCaseReview } from "../api";
import type { CaseDetail, InvestigationPriority, ReviewStatus } from "../types";

const statuses: ReviewStatus[] = ["Needs Review", "Suspected", "Verified"];
const priorities: InvestigationPriority[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];

export default function CaseReviewQueue() {
  const [filters, setFilters] = useState({ review_status: "", action: "", priority: "", risk: "" });
  const [cases, setCases] = useState<CaseDetail[]>([]);
  const [selected, setSelected] = useState<CaseDetail | null>(null);
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<ReviewStatus>("Needs Review");
  const [notice, setNotice] = useState("Loading review queue…");
  const [isSaving, setIsSaving] = useState(false);

  const load = async () => {
    try {
      const riskRange = filters.risk === "High" ? { min_fraud_score: 75 } : filters.risk === "Medium" ? { min_fraud_score: 40, max_fraud_score: 74.99 } : filters.risk === "Low" ? { max_fraud_score: 39.99 } : {};
      const result = await getCases({ review_status: filters.review_status, action: filters.action, priority: filters.priority, ...riskRange, limit: 50 });
      setCases(result.items);
      setNotice(`${result.total} cases in the current queue.`);
      if (selected) setSelected(result.items.find((item) => item.transaction_id === selected.transaction_id) ?? null);
    } catch (error) { setNotice(error instanceof SentinelApiError ? error.message : "Could not load the review queue."); }
  };
  useEffect(() => { void load(); }, [filters.review_status, filters.action, filters.priority, filters.risk]);
  const selectCase = (item: CaseDetail) => { setSelected(item); setStatus(item.review_status); setNote(item.investigator_note ?? ""); };
  const save = async () => {
    if (!selected) return;
    setIsSaving(true);
    try {
      const updated = await updateCaseReview(selected.transaction_id, { review_status: status, investigator_note: note });
      setSelected(updated); setCases((items) => items.map((item) => item.transaction_id === updated.transaction_id ? updated : item)); setNotice("Reviewer update saved.");
    } catch (error) { setNotice(error instanceof SentinelApiError ? error.message : "Could not save this review."); } finally { setIsSaving(false); }
  };
  return <section className="review-queue" aria-labelledby="review-queue-title">
    <div className="queue-toolbar"><div><p className="kicker">REVIEWER OVERSIGHT</p><h1 id="review-queue-title">Case review queue</h1></div><p>{notice}</p></div>
    <div className="queue-filters">
      <select value={filters.risk} onChange={(event) => setFilters({ ...filters, risk: event.target.value })}><option value="">All risk bands</option><option>High</option><option>Medium</option><option>Low</option></select>
      <select value={filters.review_status} onChange={(event) => setFilters({ ...filters, review_status: event.target.value })}><option value="">All statuses</option>{statuses.map((item) => <option key={item}>{item}</option>)}</select>
      <select value={filters.action} onChange={(event) => setFilters({ ...filters, action: event.target.value })}><option value="">All decisions</option>{["BLOCK", "HOLD", "MONITOR", "RELEASE"].map((item) => <option key={item}>{item}</option>)}</select>
      <select value={filters.priority} onChange={(event) => setFilters({ ...filters, priority: event.target.value })}><option value="">All priorities</option>{priorities.map((item) => <option key={item}>{item}</option>)}</select>
      <button onClick={() => setFilters({ review_status: "", action: "", priority: "", risk: "" })}>Reset filters</button>
    </div>
    <div className="queue-layout"><div className="queue-list">{cases.map((item) => <button className={`case-row ${selected?.transaction_id === item.transaction_id ? "active" : ""}`} key={item.transaction_id} onClick={() => selectCase(item)}><span><b>{item.transaction_id}</b><small>{item.action} · {item.investigation_priority}</small></span><span className={`case-status ${item.review_status.toLowerCase().replaceAll(" ", "-")}`}>{item.review_status}</span><strong>{item.fraud_score.toFixed(1)}</strong></button>)}</div>
      {selected && <aside className="case-review-panel"><div className="case-review-heading"><div><span className={`case-status ${selected.review_status.toLowerCase().replaceAll(" ", "-")}`}>{selected.review_status}</span><h2>{selected.transaction_id}</h2></div><b>{selected.fraud_score.toFixed(1)} / 100</b></div><p className="panel-description">{selected.primary_reason}</p><label>REVIEW STATUS<select value={status} onChange={(event) => setStatus(event.target.value as ReviewStatus)}>{statuses.map((item) => <option key={item}>{item}</option>)}</select></label><label>REVIEWER NOTES<textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="Document the review decision…" /></label><div className="case-review-actions"><button onClick={() => void save()} disabled={isSaving}><Save size={15} />{isSaving ? "SAVING…" : "SAVE REVIEW"}</button><button onClick={() => void downloadCaseExport(selected.transaction_id, "pdf")}><Download size={15} />PDF</button><button onClick={() => void downloadCaseExport(selected.transaction_id, "csv")}><Download size={15} />CSV</button></div></aside>}</div>
  </section>;
}
