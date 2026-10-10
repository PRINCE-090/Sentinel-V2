import { useEffect, useState } from "react";
import { ScanLine } from "lucide-react";

const stages = [
  "Verifying transaction",
  "Mapping account links",
  "Preparing decision summary",
];

export default function AssessmentTransition({ active }: { active: boolean }) {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    if (!active) { setStage(0); return; }
    const first = window.setTimeout(() => setStage(1), 1600);
    const second = window.setTimeout(() => setStage(2), 3200);
    return () => { window.clearTimeout(first); window.clearTimeout(second); };
  }, [active]);
  if (!active) return null;
  return <div className="assessment-transition" role="status" aria-live="assertive" aria-label="Running risk assessment">
    <div className="assessment-transition__panel">
      <div className="assessment-transition__icon"><ScanLine size={25} /></div>
      <p className="kicker">SENTINEL ANALYSIS ENGINE</p>
      <h2 key={stages[stage]} className="assessment-transition__stage">{stages[stage]}</h2>
      <div className="assessment-transition__steps" aria-hidden="true">
        {stages.map((item, index) => <div key={item} className={index <= stage ? "is-active" : ""}><i /> <span>{index < stage ? `Verified — ${item}` : index === stage ? item : "Queued"}</span></div>)}
      </div>
    </div>
  </div>;
}
