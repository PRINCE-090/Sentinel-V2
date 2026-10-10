import { motion } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Check,
  Copy,
  Download,
  Network,
  Radar,
  ShieldAlert,
  Zap,
  ChevronRight,
} from "lucide-react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { CSSProperties, Dispatch, SetStateAction } from "react";
import type {
  EvaluationResponse,
  FullDossier,
  TransactionRequest,
} from "../types";
import { SCENARIO_PRESETS } from "../data";
// @ts-expect-error GlassSurface is intentionally kept as the supplied JSX component.
import GlassSurface from "./GlassSurface.jsx";
const live =
  "TX_88192  •  ₹4,50,000  •  SWIFT  •  BLOCK     TX_88193  •  ₹12,000  •  UPI  •  RELEASE     TX_88194  •  ₹9,900  •  IMPS  •  HOLD";
const warnings =
  "ML-TF-01  Structuring     ML-TF-05  Mule Account Network     ML-TF-15  Rapid Succession     ML-TF-22  New Account Transacting";
export function TransactionMarquee() {
  return (
    <>
      <div className="ticker">
        <span>
          {live} &nbsp;&nbsp;&nbsp; {live}
        </span>
      </div>
      <div className="ticker amber">
        <span>
          {warnings} &nbsp;&nbsp;&nbsp; {warnings}
        </span>
      </div>
    </>
  );
}
export function Topbar({
  currentPath,
  isFloating,
}: {
  currentPath: string;
  isFloating: boolean;
}) {
  const links = [
    ["/", "Home"],
    ["/assess", "Assess"],
    ["/results", "Results"],
    ["/network", "Network"],
    ["/case-file", "Case file"],
    ["/cases", "Review queue"],
  ];
  const content = (
    <header className={isFloating ? "is-floating" : undefined}>
      <div className="brand">
        <div>
          <b>SENTINEL</b>
          <small>AI-ASSISTED FINANCIAL CRIME MONITORING</small>
        </div>
      </div>
      <nav aria-label="Primary navigation">
        {links.map(([href, label]) => (
          <a className={currentPath === href ? "active" : undefined} href={href} key={href}>
            {label}
          </a>
        ))}
      </nav>
      <div className="header-right">
        {currentPath !== "/assess" && (
          <a className="nav-cta" href="/assess">New assessment <ArrowRight size={14} /></a>
        )}
      </div>
    </header>
  );

  return (
    <GlassSurface
      width="var(--topbar-width)"
      height="var(--topbar-height)"
      borderRadius={isFloating ? 999 : 0}
      backgroundOpacity={0.08}
      saturation={1.25}
      displace={0.04}
      brightness={42}
      opacity={0.88}
      distortionScale={-55}
      greenOffset={3}
      blueOffset={6}
      className={`topbar-glass${isFloating ? " is-floating" : ""}`}
    >
      {content}
    </GlassSurface>
  );
}
const fields: (keyof TransactionRequest)[] = [
  "transaction_id",
  "sender_account",
  "receiver_account",
  "amount",
  "channel",
  "device_fingerprint_hash",
  "ip_geolocation",
  "merchant_category_code",
];
const placeholders: Partial<Record<keyof TransactionRequest, string>> = {
  transaction_id: "Enter transaction ID",
  sender_account: "Enter sender's account",
  receiver_account: "Enter receiver's account",
  amount: "Enter transfer amount",
  device_fingerprint_hash: "Enter device fingerprint",
  ip_geolocation: "Enter country code (e.g. IN)",
  merchant_category_code: "Enter merchant category code",
};
export function InputSimulator({
  request,
  setRequest,
  onEvaluate,
  isEvaluating,
}: {
  request: TransactionRequest;
  setRequest: Dispatch<SetStateAction<TransactionRequest>>;
  onEvaluate: () => void;
  isEvaluating: boolean;
}) {
  const set = (key: keyof TransactionRequest, value: string) =>
    setRequest((x) => ({
      ...x,
      [key]: key === "amount" ? (value === "" ? 0 : Number(value)) : value,
    }));
  return (
    <section className="panel simulator">
      <div className="section-title">
        <Zap size={16} /> TRANSACTION SIMULATOR
      </div>
      <p className="panel-description">
        Enter a transaction to receive a consolidated risk recommendation.
      </p>
      <div className="form-grid">
        {fields.map((key) => {
          const id = `field-${key}`;
          return (
            <label key={key} htmlFor={id}>
              {key.replaceAll("_", " ")}
              {key === "channel" ? (
                <select
                  id={id}
                  value={request[key] as string}
                  onChange={(e) => set(key, e.target.value)}
                >
                  <option value="" disabled>Select payment channel</option>
                  {["SWIFT", "UPI", "NEFT", "RTGS", "CARD", "CASH"].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              ) : (
                <input
                  id={id}
                  value={key === "amount" && !request.amount ? "" : request[key] ?? ""}
                  type={key === "amount" ? "number" : "text"}
                  placeholder={placeholders[key]}
                  onChange={(e) => set(key, e.target.value)}
                />
              )}
            </label>
          );
        })}
      </div>
      <div className="preset-row">
        <span>SCENARIO PRESETS</span>
        {SCENARIO_PRESETS.map((preset) => (
          <button key={preset.label} onClick={() => setRequest({ ...preset.request, timestamp: new Date().toISOString() })}>
            {preset.label}
          </button>
        ))}
      </div>
      <motion.button
        whileHover={{ y: -1 }}
        whileTap={{ scale: 0.98 }}
        className="evaluate"
        onClick={onEvaluate}
        disabled={isEvaluating}
      >
        <Zap size={16} />
        {isEvaluating ? "EVALUATING…" : "RUN RISK ASSESSMENT"}
        <ChevronRight size={16} />
      </motion.button>
    </section>
  );
}
const colors = {
  BLOCK: "red",
  HOLD: "yellow",
  MONITOR: "blue",
  RELEASE: "green",
};
export function MagicCard({
  response,
  dossier,
}: {
  response: EvaluationResponse;
  dossier: FullDossier;
}) {
  const verdict = colors[response.action];
  return (
    <section className="panel magic">
      <div className="section-title decision-title">
        <ShieldAlert size={16} /> DECISION BRIEF
      </div>
      <div className="verdict-top">
        <div>
          <span className="eyebrow">RECOMMENDED ACTION</span>
          <div className={"verdict " + verdict}>{response.action}</div>
        </div>
        <div
          className={
            "manual " + (response.requires_manual_review ? "" : "clear")
          }
        >
          <AlertTriangle size={18} />
          <small>MANUAL REVIEW</small>
          <b>{response.requires_manual_review ? "REQUIRED" : "CLEAR"}</b>
        </div>
      </div>
      <div className="score-row">
        <div
          className={"score-ring " + verdict}
          style={{ "--score": `${response.fraud_score}%` } as CSSProperties}
        >
          <b>{response.fraud_score}</b>
          <span>RISK SCORE / 100</span>
        </div>
        <div className="reason">
          <span>PRIMARY SIGNAL</span>
          <b>{response.primary_reason.replaceAll("_", " ")}</b>
          <p>
            Confidence{" "}
            <strong>
              {dossier.detection_result.confidence_band.toUpperCase()}
            </strong>
          </p>
          <p>
            Model drift <strong>{dossier.detection_result.drift_stage}</strong>
          </p>
        </div>
      </div>
      <div className="decision-note">
        <Check size={15} /> Graph correlation confirms a coordinated pattern.
      </div>
    </section>
  );
}
export function AnalyticsBreakdown({ dossier }: { dossier: FullDossier }) {
  const data = [
    [
      "LIGHTGBM SUPERVISED PROBABILITY",
      dossier.detection_result.lightgbm_prob,
      "cyan",
    ],
    ["ISOLATION FOREST ANOMALY", dossier.detection_result.anomaly_score, "red"],
    ["RULE-BASED SIGNAL FLOOR", 0.72, "yellow"],
    [
      "GRAPH CENTRALITY / PAGERANK",
      dossier.graph_summary.centrality_score,
      "purple",
    ],
  ];
  return (
    <section className="panel analytics">
      <div className="section-title">
        <Radar size={16} /> DUAL-MODEL BEHAVIORAL ANALYTICS{" "}
      </div>
      {data.map(([name, value, color]) => (
        <div className="metric" key={String(name)}>
          <div>
            <span>{name}</span>
            <b>{(Number(value) * 100).toFixed(0)}%</b>
          </div>
          <i>
            <em
              className={String(color)}
              style={{ width: `${Number(value) * 100}%` }}
            />
          </i>
        </div>
      ))}
      <div className="strain">
        <div>
          <small>SENDER INCOME STRAIN</small>
          <b className="red-text">
            9.4× <sup>CRITICAL</sup>
          </b>
        </div>
        <div>
          <small>RECEIVER FAN-IN / OUT</small>
          <b>
            14 : 1 <sup>HIGH</sup>
          </b>
        </div>
      </div>
    </section>
  );
}
export function ShapChart({ dossier }: { dossier: FullDossier }) {
  return (
    <section className="panel shap">
      <div className="section-title">
        <Activity size={16} /> EXPLAINABLE AI / SHAP
      </div>
      {dossier.top_reason_codes.map((x) => (
        <div className="shap-row" key={x.feature_name}>
          <div>
            <span>{x.label}</span>
            <small>{x.human_description}</small>
          </div>
          <div
            className={
              x.impact === "positive" ? "shap-bar up" : "shap-bar down"
            }
            style={{ width: `${Math.abs(x.attribution_score) * 210}px` }}
          >
            {x.attribution_score > 0 ? "+" : ""}
            {x.attribution_score.toFixed(2)}
          </div>
        </div>
      ))}
    </section>
  );
}
function AccountNode({ data }: { data: NodeProps["data"] }) {
  const danger = (data as { danger?: boolean }).danger;
  return (
    <div className={"account-node " + (danger ? "danger" : "")}>
      <Handle type="target" position={Position.Left} />
      <Network size={14} />
      <b>{String((data as { label: string }).label).replace("ACC_", "")}</b>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { account: AccountNode };
export function NetworkGraph() {
  const nodes = [
    {
      id: "a",
      type: "account",
      position: { x: 0, y: 90 },
      data: { label: "ACC_MULE_SENDER_01", danger: true },
    },
    {
      id: "b",
      type: "account",
      position: { x: 250, y: 0 },
      data: { label: "ACC_MULE_RECV_02", danger: true },
    },
    {
      id: "c",
      type: "account",
      position: { x: 250, y: 185 },
      data: { label: "ACC_TRANSIT_03", danger: true },
    },
  ];
  const edges = [
    {
      id: "ab",
      source: "a",
      target: "b",
      animated: true,
      style: { stroke: "#ff3b30" },
    },
    {
      id: "bc",
      source: "b",
      target: "c",
      animated: true,
      style: { stroke: "#ff3b30" },
    },
    {
      id: "ca",
      source: "c",
      target: "a",
      animated: true,
      style: { stroke: "#ff3b30" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#ff3b30" },
    },
  ];
  return (
    <section id="network" className="panel graph">
      <div className="section-title">
        <Network size={16} /> FLOW INTELLIGENCE{" "}
        <span className="red-text">CYCLE DETECTED</span>
      </div>
      <p className="panel-description">
        Three accounts form a high-velocity closed loop.
      </p>
      <div className="flow">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
        >
          <Background color="#233047" gap={18} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </section>
  );
}
export function ProceedsTrail({ dossier }: { dossier: FullDossier }) {
  return (
    <section className="panel trail">
      <div className="section-title">
        <ArrowRight size={16} /> MULTI-HOP PROCEEDS TRAIL
      </div>
      <table>
        <thead>
          <tr>
            <th>HOP</th>
            <th>FROM → TO</th>
            <th>AMOUNT</th>
            <th>DELTA</th>
          </tr>
        </thead>
        <tbody>
          {dossier.proceeds_trail.map((x) => (
            <tr key={x.hop_number}>
              <td>0{x.hop_number}</td>
              <td>
                {x.from_account.replace("ACC_", "")} <ArrowRight size={12} />{" "}
                {x.to_account.replace("ACC_", "")}
              </td>
              <td>₹{x.amount.toLocaleString("en-IN")}</td>
              <td>{x.timestamp_delta_hours}h</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
export function SarDossierView({
  response,
  dossier,
  onExport,
}: {
  response: EvaluationResponse;
  dossier: FullDossier;
  onExport?: (format: "pdf" | "csv") => void;
}) {
  const copy = () =>
    navigator.clipboard?.writeText(response.sar_narrative || "");
  return (
    <section id="case-file" className="panel dossier">
      <div className="section-title">
        <ShieldAlert size={16} /> COMPLIANCE CASE DOSSIER <span>CASE OPEN</span>
      </div>
      <p className="panel-description">
        Evidence and narrative are ready for analyst review.
      </p>
      <div className="typologies">
        {dossier.typology_matches.map((x) => (
          <div key={x.fatf_code}>
            <b>{x.fatf_code}</b>
            <span>{x.typology_name}</span>
            <strong>{Math.round(x.confidence * 100)}%</strong>
          </div>
        ))}
      </div>
      <div className="sar">
        <span>SAR NARRATIVE PREVIEW</span>
        <p>{response.sar_narrative}</p>
      </div>
      <div className="dossier-actions">
        <button onClick={copy}>
          <Copy size={15} /> COPY SAR
        </button>
        <button onClick={() => onExport?.("pdf")} disabled={!onExport}>
          <Download size={15} /> EXPORT SAR (PDF)
        </button>
      </div>
    </section>
  );
}
