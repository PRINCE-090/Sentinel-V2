import { useState } from "react";
import capabilitiesImage from "../assets/capabilities.png";
type Capability = {
  image: string;
  label: string;
  alt: string;
};

export default function CapabilitiesExplorer({ items }: { items: Capability[] }) {
  const [active, setActive] = useState<number | null>(null);
  const selected = active === null ? null : items[active];

  return (
    <div className={`capabilities-explorer${selected ? " is-focused" : ""}`} onMouseLeave={() => setActive(null)}>
      <img className="capabilities-explorer__overview" src={capabilitiesImage} alt="Connected financial crime investigation flow" />
      <div className="capabilities-explorer__zones" aria-label="Explore Sentinel capabilities">
        {items.map((item, index) => (
          <button
            type="button"
            key={item.label}
            className={`capabilities-explorer__zone${active === index ? " is-active" : ""}`}
            onMouseEnter={() => setActive(index)}
            onFocus={() => setActive(index)}
            onBlur={() => setActive(null)}
            aria-label={`View ${item.label}`}
          >
          </button>
        ))}
      </div>
      <div className={`capabilities-explorer__focus${active !== null ? ` focus--${active}` : ""}`} aria-live="polite">
        {selected && <div className="capabilities-explorer__focus-content" key={selected.label}>
          <img src={selected.image} alt={selected.alt} />
          <div><p>CAPABILITY</p><strong>{selected.label}</strong></div>
        </div>}
      </div>
    </div>
  );
}
