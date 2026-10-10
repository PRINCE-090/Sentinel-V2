import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { gsap } from "gsap";

type StrokeTextProps = {
  text: string;
  strokeColor?: string;
  fillColor?: string;
  strokeWidth?: number;
  drawDuration?: number;
  fillDelay?: number;
  stagger?: number;
  ease?: string;
  trigger?: "mount" | "hover" | "loop";
  fillMode?: "wipe" | "fade" | "none";
  fontSize?: number;
  fontWeight?: number;
  letterSpacing?: number;
  reverse?: boolean;
  className?: string;
};

export default function StrokeText({
  text,
  strokeColor = "#A78BFA",
  fillColor = "#F8FAFC",
  strokeWidth = 1.4,
  drawDuration = 1.6,
  fillDelay = 0.2,
  stagger = 0.05,
  ease = "power2.out",
  trigger = "mount",
  fillMode = "wipe",
  fontSize = 128,
  fontWeight = 800,
  letterSpacing = -4,
  reverse = false,
  className = "",
}: StrokeTextProps) {
  const rootRef = useRef<HTMLSpanElement>(null);
  const measureRef = useRef<SVGTextElement>(null);
  const wipeRef = useRef<SVGRectElement>(null);
  const [box, setBox] = useState<DOMRect | null>(null);
  const rawId = useId();
  const wipeId = `stroke-text-wipe-${rawId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const characters = useMemo(() => Array.from(text), [text]);
  const dash = Math.max(fontSize * 7, 200);

  useLayoutEffect(() => {
    let cancelled = false;
    const measure = () => {
      if (cancelled || !measureRef.current) return;
      try {
        const next = measureRef.current.getBBox();
        const pad = Math.max(strokeWidth, fontSize * 0.1);
        if (next.width) {
          setBox(new DOMRect(next.x - pad, next.y - pad, next.width + pad * 2, next.height + pad * 2));
        }
      } catch {
        // The SVG may be temporarily unavailable while the page is rendering.
      }
    };

    measure();
    document.fonts?.ready.then(measure).catch(() => {});
    return () => { cancelled = true; };
  }, [fontSize, fontWeight, letterSpacing, strokeWidth, characters]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root || !box) return;

    const strokes = gsap.utils.toArray<SVGElement>(root.querySelectorAll("[data-stroke-char]"));
    const fills = gsap.utils.toArray<SVGElement>(root.querySelectorAll("[data-fill-char]"));
    const wipe = wipeRef.current;
    const targets = [...strokes, ...fills, ...(wipe ? [wipe] : [])];
    const useWipe = fillMode === "wipe";
    const fillEnabled = fillMode !== "none";
    const staggerConfig = reverse ? { each: stagger, from: "end" as const } : stagger;

    const setStart = () => {
      gsap.set(strokes, { strokeDasharray: dash, strokeDashoffset: dash });
      gsap.set(fills, { opacity: useWipe ? 1 : 0 });
      if (wipe) gsap.set(wipe, { attr: { width: 0 } });
    };
    const setEnd = () => {
      gsap.set(strokes, { strokeDasharray: dash, strokeDashoffset: 0 });
      gsap.set(fills, { opacity: fillEnabled ? 1 : 0 });
      if (wipe) gsap.set(wipe, { attr: { width: fillEnabled ? box.width : 0 } });
    };

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setEnd();
      return () => gsap.killTweensOf(targets);
    }

    const buildTimeline = () => {
      setStart();
      const timeline = gsap.timeline({
        paused: true,
        repeat: trigger === "loop" ? -1 : 0,
        repeatDelay: trigger === "loop" ? 0.9 : 0,
      });
      timeline.to(strokes, { strokeDashoffset: 0, duration: drawDuration, ease, stagger: staggerConfig }, 0);
      if (useWipe && wipe) {
        timeline.to(wipe, { attr: { width: box.width }, duration: Math.max(0.4, drawDuration * 0.5), ease: "power2.inOut" }, drawDuration + fillDelay);
      } else if (fillEnabled) {
        timeline.to(fills, { opacity: 1, duration: Math.max(0.4, drawDuration * 0.5), ease: "power2.out", stagger: staggerConfig }, drawDuration + fillDelay);
      }
      return timeline;
    };

    let timeline = buildTimeline();
    if (trigger === "hover") {
      setEnd();
      const replay = () => { timeline.kill(); timeline = buildTimeline(); timeline.play(0); };
      root.addEventListener("pointerenter", replay);
      return () => { root.removeEventListener("pointerenter", replay); timeline.kill(); gsap.killTweensOf(targets); };
    }
    timeline.play(0);
    return () => { timeline.kill(); gsap.killTweensOf(targets); };
  }, [box, dash, drawDuration, ease, fillDelay, fillMode, reverse, stagger, trigger]);

  const textStyle = { fontSize: `${fontSize}px`, fontWeight, letterSpacing: `${letterSpacing}px` };
  const viewBox = box ? `${box.x} ${box.y} ${box.width} ${box.height}` : `0 ${-fontSize} 600 ${fontSize * 1.3}`;

  return (
    <span ref={rootRef} className={`stroke-text ${trigger === "hover" ? "stroke-text--hover" : ""} ${className}`.trim()} style={{ "--stroke-text-height": `${Math.round(fontSize * 1.3)}px` } as React.CSSProperties} role="img" aria-label={text}>
      <svg className="stroke-text__svg" viewBox={viewBox} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        {WipeClip(fillMode, box, wipeId, wipeRef)}
        <text ref={measureRef} className="stroke-text__stroke" x="0" y="0" fill="none" stroke={strokeColor} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" style={textStyle}>
          {characters.map((character, index) => <tspan data-stroke-char key={`stroke-${index}`}>{character}</tspan>)}
        </text>
        <text className="stroke-text__fill" x="0" y="0" fill={fillColor} style={textStyle} clipPath={fillMode === "wipe" && box ? `url(#${wipeId})` : undefined}>
          {characters.map((character, index) => <tspan data-fill-char key={`fill-${index}`}>{character}</tspan>)}
        </text>
      </svg>
    </span>
  );
}

function WipeClip(fillMode: StrokeTextProps["fillMode"], box: DOMRect | null, wipeId: string, wipeRef: React.RefObject<SVGRectElement | null>) {
  if (fillMode !== "wipe" || !box) return null;
  return <defs><clipPath id={wipeId} clipPathUnits="userSpaceOnUse"><rect ref={wipeRef} x={box.x} y={box.y} width="0" height={box.height} /></clipPath></defs>;
}
