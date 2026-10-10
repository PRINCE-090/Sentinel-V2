import decisiveImage from "./assets/decisive.png";
import connectedImage from "./assets/connected.png";
import defensibleImage from "./assets/defensible.png";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, MessageCircle, Radar } from "lucide-react";
import { EMPTY_TRANSACTION_REQUEST, MOCK_SCENARIO_A } from "./data";
import { downloadCaseExport, evaluateTransaction, SentinelApiError } from "./api";
import StrokeText from "./components/StrokeText";
import CapabilitiesExplorer from "./components/CapabilitiesExplorer";
import Dither from "./components/Dither.jsx";
import AssessmentTransition from "./components/AssessmentTransition";
import ChatbotFeature, { ChatbotLoading } from "./components/ChatbotFeature";
import FeatureGate from "./components/FeatureGate";
import CaseReviewQueue from "./components/CaseReviewQueue";
import {
  AnalyticsBreakdown,
  InputSimulator,
  MagicCard,
  NetworkGraph,
  ProceedsTrail,
  SarDossierView,
  ShapChart,
  Topbar,
  TransactionMarquee,
} from "./components/SentinelModules";
import type {
  EvaluationResponse,
  FullDossier,
  TransactionRequest,
} from "./types";

const ROUTE_BACKGROUND_OFFSET: Record<string, number> = {
  "/": 0,
  "/assess": -12,
  "/results": -24,
  "/network": -36,
  "/case-file": -48,
  "/chatbot": -60,
  "/cases": -72,
};

// Add each optional product feature here. A false flag means the feature is
// neither rendered nor interactive anywhere in the application.
const FEATURE_FLAGS = {
  chatbot: true,
  reviewerOversight: true,
  reviewQueue: true,
  caseExport: true,
} as const;

export default function App() {
  const [path, setPath] = useState(() => window.location.pathname);
  const [isFloatingNav, setIsFloatingNav] = useState(() => window.scrollY > 32);
  const floatingNavRef = useRef(isFloatingNav);
  const ditherScrollRef = useRef<HTMLDivElement>(null);
  const [request, setRequest] = useState<TransactionRequest>(
    EMPTY_TRANSACTION_REQUEST,
  );
  const [response, setResponse] = useState<EvaluationResponse>(
    MOCK_SCENARIO_A.response,
  );
  const [dossier, setDossier] = useState<FullDossier>(MOCK_SCENARIO_A.dossier);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [isAssessEntering, setIsAssessEntering] = useState(false);
  const [isChatbotLoading, setIsChatbotLoading] = useState(
    () => window.location.pathname === "/chatbot",
  );
  const [notice, setNotice] = useState(
    "Enter transaction details or select a scenario preset.",
  );
  const backgroundOffset = ROUTE_BACKGROUND_OFFSET[path] ?? 0;
  const evaluate = async () => {
    setIsEvaluating(true);
    setNotice("Running risk controls and graph analysis…");
    const liveRequest = { ...request, timestamp: new Date().toISOString() };
    setRequest(liveRequest);
    try {
      const liveResponse = await evaluateTransaction(liveRequest);
      setResponse(liveResponse);
      setDossier(MOCK_SCENARIO_A.dossier);
      setNotice(
        `Evaluation complete: ${liveResponse.action} recommendation is ready.`,
      );
      navigate("/results");
    } catch (error) {
      setNotice(
        error instanceof SentinelApiError
          ? error.message
          : "The assessment could not be completed. Please try again.",
      );
    } finally {
      setIsEvaluating(false);
    }
  };
  useEffect(() => {
    const syncPath = () => {
      const nextPath = window.location.pathname;
      if (nextPath === "/chatbot") setIsChatbotLoading(true);
      setPath(nextPath);
    };
    window.addEventListener("popstate", syncPath);
    return () => window.removeEventListener("popstate", syncPath);
  }, []);
  useEffect(() => {
    if (path !== "/chatbot") return;
    setIsChatbotLoading(true);
    const timer = window.setTimeout(() => setIsChatbotLoading(false), 1400);
    return () => window.clearTimeout(timer);
  }, [path]);
  useEffect(() => {
    let frame = 0;
    const updateDitherPosition = () => {
      frame = 0;
      const offset = -Math.min(window.scrollY * 0.08, 420);
      ditherScrollRef.current?.style.setProperty("--dither-scroll-y", `${offset}px`);
    };
    const onScroll = () => {
      if (!frame) frame = window.requestAnimationFrame(updateDitherPosition);
    };
    updateDitherPosition();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);
  useEffect(() => {
    const updateNav = () => {
      const scrollY = window.scrollY;
      const nextFloating = floatingNavRef.current ? scrollY > 20 : scrollY > 96;
      if (nextFloating === floatingNavRef.current) return;
      floatingNavRef.current = nextFloating;
      setIsFloatingNav(nextFloating);
    };
    window.addEventListener("scroll", updateNav, { passive: true });
    return () => window.removeEventListener("scroll", updateNav);
  }, []);
  const navigate = (to: string) => {
    if (to === "/chatbot") setIsChatbotLoading(true);
    window.history.pushState({}, "", to);
    setPath(to);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const enterAssess = (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (isAssessEntering) return;
    setIsAssessEntering(true);
    window.setTimeout(() => navigate("/assess"), 220);
    window.setTimeout(() => setIsAssessEntering(false), 1020);
  };
  const PageHeading = ({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) => (
    <div className="route-heading">
      <p className="kicker">{eyebrow}</p>
      <h1>{title}</h1>
      <p className="lede">{description}</p>
    </div>
  );
  let page: React.ReactNode;
  if (path === "/assess") {
    page = <>
      <PageHeading eyebrow="TRANSACTION INTAKE" title="Assess a transaction" description="Provide the core transaction signals. Sentinel will consolidate behavioral, network, and policy controls into one decision." />
      <div className="single-panel"><InputSimulator request={request} setRequest={setRequest} onEvaluate={evaluate} isEvaluating={isEvaluating} /></div>
    </>;
  } else if (path === "/results") {
    page = <>
      <PageHeading eyebrow="ASSESSMENT OUTPUT" title="Decision results" description="A concise risk recommendation with the evidence used to support it." />
      <div className="grid results-grid"><MagicCard response={response} dossier={dossier} /><AnalyticsBreakdown dossier={dossier} /><ShapChart dossier={dossier} /></div>
    </>;
  } else if (path === "/network") {
    page = <>
      <PageHeading eyebrow="NETWORK INTELLIGENCE" title="Follow the money" description="Inspect connected accounts and the proceeds trail behind the current transaction." />
      <div className="grid flow-grid"><NetworkGraph /><ProceedsTrail dossier={dossier} /></div>
    </>;
  } else if (path === "/case-file") {
    page = <>
      <PageHeading eyebrow="COMPLIANCE WORKSPACE" title="Case dossier" description="Review the matched typologies and prepare the narrative for analyst action." />
      <SarDossierView response={response} dossier={dossier} onExport={(format) => void downloadCaseExport(response.transaction_id, format)} />
    </>;
  } else if (path === "/cases") {
    page = <FeatureGate enabled={FEATURE_FLAGS.reviewQueue}><CaseReviewQueue /></FeatureGate>;
  } else if (path === "/chatbot" && FEATURE_FLAGS.chatbot) {
    page = <FeatureGate enabled={FEATURE_FLAGS.chatbot}>
      {isChatbotLoading ? <ChatbotLoading /> : <ChatbotFeature />}
    </FeatureGate>;
  } else {
    page = <section className="landing">
      <div className="landing-hero">
        <div className="hero-copy">
          <p className="kicker">AI-ASSISTED FINANCIAL CRIME MONITORING</p>
          <h1>
            Know the risk.<br />
            <StrokeText text="Move with confidence." strokeColor="#B8AA91" fillColor="#B8AA91" strokeWidth={1.15} drawDuration={1.25} fillDelay={0.1} stagger={0.03} fontSize={86} fontWeight={400} letterSpacing={-1} className="hero-stroke-text hero-accent" />
          </h1>
          <p>Sentinel brings transaction data, network intelligence, and explainable decisioning into one focused investigation flow.</p>
          <div className="hero-actions">
            <a className="hero-cta" href="/assess" onClick={enterAssess}>Start with Sentinel <ArrowRight size={17} /></a>
            <FeatureGate enabled={FEATURE_FLAGS.chatbot}>
              <a className="hero-cta hero-chatbot-trigger" href="/chatbot">Chat with Sentinel <MessageCircle size={17} /></a>
            </FeatureGate>
          </div>
        </div>
      </div>
      <section className="landing-capabilities" aria-labelledby="capabilities-heading">
        <div className="capabilities-intro">
          <p className="kicker">ONE FOCUSED INVESTIGATION FLOW</p>
          <h2 id="capabilities-heading">Built to make every signal actionable.</h2>
          <p>Sentinel keeps the important evidence in view from the first transaction signal to the final analyst-ready case.</p>
        </div>
        <div className="capabilities-gallery">
          <CapabilitiesExplorer items={[
            { image: decisiveImage, label: "Decisive controls", alt: "Financial risk controls visualization" },
            { image: connectedImage, label: "Connected evidence", alt: "Connected financial evidence visualization" },
            { image: defensibleImage, label: "Defensible cases", alt: "Defensible investigation case visualization" },
          ]} />
        </div>
      </section>
      <div className="landing-next">
        <div><Radar size={18} /><span>READY TO INVESTIGATE</span></div>
        <h2>Start with us.</h2>
        <p>Enter transaction details, run the assessment, then follow the evidence through every stage of the case.</p>
        <a href="/assess">Begin an assessment <ArrowRight size={17} /></a>
      </div>
    </section>;
  }
  return (
    <main onClick={(event) => {
      const link = (event.target as Element).closest("a[href^='/']") as HTMLAnchorElement | null;
      if (!link || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey) return;
      event.preventDefault();
      navigate(link.pathname);
    }}>
      <div className="app-dither" style={{ transform: `translateX(${backgroundOffset}vw)` }} aria-hidden="true">
        <div ref={ditherScrollRef} className="app-dither__scroll">
          <Dither waveColor={[0.26, 0.24, 0.2]} disableAnimation={false} enableMouseInteraction={false} mouseRadius={0.3} colorNum={4} pixelSize={2.5} waveAmplitude={0.3} waveFrequency={3} waveSpeed={0.12} />
        </div>
      </div>
      <AssessmentTransition active={isEvaluating} />
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <Topbar currentPath={path} isFloating={isFloatingNav} />
      {path !== "/" && <TransactionMarquee />}
      <div id="main-content" className={`shell ${path === "/assess" && isAssessEntering ? "route-slide-in" : ""}`} tabIndex={-1}>
        {page}
        <div className="sr-status" role="status" aria-live="polite">
          {notice}
        </div>
      </div>
    </main>
  );
}
