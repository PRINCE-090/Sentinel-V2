import { useCallback, useEffect, useRef, useState } from "react";
import { gsap } from "gsap";

type GalleryItem = { image: string; label: string; alt: string; focusPosition?: string };
type Props = {
  items: GalleryItem[];
  defaultIndex?: number;
  expandRatio?: number;
  trigger?: "hover" | "click";
  accentColor?: string;
  overlayColor?: string;
  textColor?: string;
  grayscale?: boolean;
  showLabels?: boolean;
  duration?: number;
  ease?: string;
  parallax?: number;
  tilt?: number;
  stagger?: number;
  height?: number;
  gap?: number;
  radius?: number;
  preserveContinuity?: boolean;
};

export default function AccordionGallery({ items, defaultIndex = 1, expandRatio = 0.52, trigger = "hover", accentColor = "#B8AA91", overlayColor = "#050505", textColor = "#F3F7FB", grayscale = true, showLabels = true, duration = 0.6, ease = "power3.out", parallax = 0.5, tilt = 8, stagger = 0.06, height = 460, gap = 10, radius = 16, preserveContinuity = false }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const mediaRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const barRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const textRefs = useRef<(HTMLSpanElement | null)[]>([]);
  const timelineRef = useRef<gsap.core.Timeline | null>(null);
  const firstRun = useRef(true);
  const mediaSize = useRef(320);
  const [active, setActive] = useState(Math.min(Math.max(defaultIndex, 0), items.length - 1));
  const reducedMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const applyLayout = useCallback((animate: boolean) => {
    const panels = panelRefs.current;
    if (!panels.length) return;
    const ratio = Math.min(Math.max(expandRatio, 0.2), 0.9);
    const grow = items.length > 1 ? (ratio * (items.length - 1)) / (1 - ratio) : 1;
    timelineRef.current?.kill();
    const timeline = gsap.timeline();
    panels.forEach((panel, index) => {
      if (!panel) return;
      const selected = index === active;
      const media = mediaRefs.current[index];
      const bar = barRefs.current[index];
      const label = textRefs.current[index];
      const animationDuration = animate && !reducedMotion ? duration : 0;
      timeline.to(panel, { flexGrow: preserveContinuity ? 1 : selected ? grow : 1, rotateY: preserveContinuity ? 0 : selected ? 0 : index < active ? tilt : -tilt, duration: animationDuration, ease }, 0);
      if (media) {
        const shift = preserveContinuity ? 0 : Math.max(-1.5, Math.min(1.5, active - index)) * parallax * mediaSize.current * 0.06;
        timeline.to(media, { xPercent: -50, yPercent: -50, x: selected ? 0 : shift, "--ag-gray": grayscale ? (selected ? 0 : 1) : 0, "--ag-dim": selected ? 0 : 0.35, duration: animationDuration, ease }, 0);
      }
      if (showLabels && bar && label) timeline.to([bar, label], { opacity: selected ? 1 : 0, x: selected ? 0 : -14, duration: selected ? animationDuration : animationDuration * 0.6, ease, stagger: reducedMotion ? 0 : stagger }, 0);
    });
    timelineRef.current = timeline;
  }, [active, duration, ease, expandRatio, grayscale, items.length, parallax, preserveContinuity, reducedMotion, showLabels, stagger, tilt]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const measure = () => {
      const usable = Math.max(root.getBoundingClientRect().width - gap * (items.length - 1), 120);
      mediaSize.current = Math.max(140, usable * Math.min(Math.max(expandRatio, 0.2), 0.9) * 1.22);
      root.style.setProperty("--ag-media-size", `${mediaSize.current}px`);
      applyLayout(!firstRun.current);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    return () => observer.disconnect();
  }, [applyLayout, expandRatio, gap, items.length]);

  useEffect(() => { applyLayout(!firstRun.current); firstRun.current = false; }, [applyLayout]);
  useEffect(() => () => { timelineRef.current?.kill(); }, []);

  return <div ref={rootRef} className={`accordion-gallery${preserveContinuity ? " accordion-gallery--continuous" : ""}`} style={{ "--ag-accent": accentColor, "--ag-overlay": overlayColor, "--ag-text": textColor, "--ag-gap": `${gap}px`, "--ag-radius": `${radius}px`, height: `${height}px` } as React.CSSProperties} role="list" aria-label="Sentinel capabilities">
    {items.map((item, index) => <button type="button" key={item.label} ref={(element) => { panelRefs.current[index] = element; }} className={`ag-panel${active === index ? " ag-panel--active" : ""}`} onMouseEnter={() => trigger === "hover" && setActive(index)} onClick={() => setActive(index)} onFocus={() => setActive(index)} onKeyDown={(event) => { if (event.key === "ArrowRight") setActive((index + 1) % items.length); if (event.key === "ArrowLeft") setActive((index - 1 + items.length) % items.length); }} role="listitem" aria-current={active === index}>
      <span className="ag-panel__frame"><span className="ag-panel__media" ref={(element) => { mediaRefs.current[index] = element; }}><img src={item.image} alt={item.alt} draggable="false" style={{ objectPosition: item.focusPosition ?? "50% 50%" }} /></span><span className="ag-panel__overlay" /></span>
      {showLabels && <span className="ag-panel__label"><span className="ag-panel__bar" ref={(element) => { barRefs.current[index] = element; }} /><span className="ag-panel__text" ref={(element) => { textRefs.current[index] = element; }}>{item.label}</span></span>}
    </button>)}
  </div>;
}
