from __future__ import annotations
import math
from typing import List, Dict, Tuple

from sentinel.core import get_logger, CONFIG
from stage1_ingestion.schemas import EnrichedTransaction
from stage2_detection.schemas import DetectionResult
from .schemas import TypologyMatch

logger = get_logger(__name__)

FATF_TYPOLOGIES: List[Dict] = [
    {"code": "ML-TF-01", "name": "Structuring / Smurfing",
     "desc": "Breaking large sums into smaller deposits just below reporting thresholds to avoid detection. Multiple accounts or agents used."},
    {"code": "ML-TF-02", "name": "Layering via Shell Companies",
     "desc": "Funds moved through multiple shell or nominee companies across jurisdictions to obscure beneficial ownership."},
    {"code": "ML-TF-03", "name": "Trade-Based Money Laundering",
     "desc": "Over or under-invoicing of goods and services to transfer value across borders disguised as legitimate trade."},
    {"code": "ML-TF-04", "name": "Real Estate Laundering",
     "desc": "Purchasing real estate with illicit funds, often using cash or nominee buyers, then selling to generate clean proceeds."},
    {"code": "ML-TF-05", "name": "Mule Account Network",
     "desc": "Legitimate account holders recruited to receive and forward illicit funds, often unknowingly. High volume of small inbound transfers consolidated and forwarded."},
    {"code": "ML-TF-06", "name": "Crypto Asset Conversion",
     "desc": "Converting fiat currency to cryptocurrency to exploit pseudonymity, moving through mixing services or multiple wallets."},
    {"code": "ML-TF-07", "name": "Casino / Gambling Layering",
     "desc": "Using gambling establishments to convert illicit cash into chips, then cashing out as winnings."},
    {"code": "ML-TF-08", "name": "Round-Tripping",
     "desc": "Funds sent offshore and returned as foreign investment or loans to appear as legitimate capital."},
    {"code": "ML-TF-09", "name": "Hawala / Informal Value Transfer",
     "desc": "Using informal money brokers to transfer value across borders without physical movement of funds."},
    {"code": "ML-TF-10", "name": "Loan-Back Scheme",
     "desc": "Criminal places funds in offshore account, borrows against it domestically, repays loan with clean funds."},
    {"code": "ML-TF-11", "name": "Professional Money Laundering Network",
     "desc": "Lawyers, accountants, or company formation agents knowingly or unwittingly facilitating layering through professional services."},
    {"code": "ML-TF-12", "name": "Insurance Policy Manipulation",
     "desc": "Purchasing high-value insurance policies with illicit funds, then cancelling for refund as clean money."},
    {"code": "ML-TF-13", "name": "Cash Intensive Business Front",
     "desc": "Mixing illicit cash with revenues of a legitimate cash-intensive business such as restaurants or car washes."},
    {"code": "ML-TF-14", "name": "Prepaid Card Layering",
     "desc": "Loading illicit funds onto anonymous prepaid cards and spending or transferring across borders."},
    {"code": "ML-TF-15", "name": "Rapid Succession Transfers",
     "desc": "Moving funds through multiple accounts in rapid succession within hours to obscure origin."},
    {"code": "ML-TF-16", "name": "Account Takeover for Laundering",
     "desc": "Compromised legitimate accounts used as transit points for illicit funds, blending with normal activity."},
    {"code": "ML-TF-17", "name": "Fictitious Employee Payroll",
     "desc": "Creating ghost employees and routing illicit payments through payroll systems."},
    {"code": "ML-TF-18", "name": "Luxury Asset Acquisition",
     "desc": "Purchasing luxury goods, jewellery, art, or vehicles with illicit funds, then selling for seemingly clean proceeds."},
    {"code": "ML-TF-19", "name": "NPO / Charity Abuse",
     "desc": "Non-profit organisations used to move funds under the guise of charitable donations or humanitarian aid."},
    {"code": "ML-TF-20", "name": "Correspondent Banking Exploitation",
     "desc": "Using correspondent banking relationships to move funds through jurisdictions with weaker AML controls."},
    {"code": "ML-TF-21", "name": "Digital Payment Layering",
     "desc": "Multiple rapid transfers through digital payment platforms, wallets, and UPI to fragment and obscure fund flow."},
    {"code": "ML-TF-22", "name": "New Account Rapid Transacting",
     "desc": "Newly opened accounts immediately receiving and forwarding large sums before account history establishes normal baseline."},
    {"code": "ML-TF-23", "name": "Cross-Border Micro-Transfer Aggregation",
     "desc": "Many small cross-border transfers aggregated at destination to accumulate significant sums below individual thresholds."},
    {"code": "ML-TF-24", "name": "Placement via ATM Cash Deposits",
     "desc": "Illicit cash deposited across many ATMs in small amounts to avoid branch scrutiny."},
    {"code": "ML-TF-25", "name": "Loan Repayment with Illicit Funds",
     "desc": "Taking out legitimate loans and repaying them with illicit cash to create a paper trail of legitimate repayment income."},
    {"code": "ML-TF-26", "name": "Terrorist Financing via Small Donations",
     "desc": "Aggregating many small donations from individuals to fund terrorist operations, avoiding single large suspicious transactions."},
    {"code": "ML-TF-27", "name": "State Sponsor Sanctions Evasion",
     "desc": "Using front companies and intermediaries to conduct transactions on behalf of sanctioned states or entities."},
    {"code": "ML-TF-28", "name": "Proliferation Finance",
     "desc": "Raising and moving funds to acquire materials or technology for weapons of mass destruction programmes."},
    {"code": "ML-TF-29", "name": "Cyber-Enabled Fraud Proceeds",
     "desc": "Laundering proceeds from online fraud, BEC scams, or ransomware via rapid account hops and crypto conversion."},
    {"code": "ML-TF-30", "name": "Human Trafficking Proceeds",
     "desc": "Cash proceeds from human trafficking deposited through cash-intensive fronts or remittance services."},
    {"code": "ML-TF-31", "name": "Drug Proceeds Integration",
     "desc": "Retail drug sale proceeds broken into small deposits and integrated through multiple accounts."},
    {"code": "ML-TF-32", "name": "Ponzi / Investment Fraud Layering",
     "desc": "Funds raised from victims of investment fraud layered through multiple accounts to pay earlier investors."},
    {"code": "ML-TF-33", "name": "Identity Theft Account Exploitation",
     "desc": "Fraudulently opened accounts using stolen identities used as transit accounts for illicit fund movement."},
    {"code": "ML-TF-34", "name": "Tax Evasion Integration",
     "desc": "Undeclared income integrated through nominee accounts or foreign structures to avoid tax detection."},
    {"code": "ML-TF-35", "name": "Bribery and Corruption Proceeds",
     "desc": "Kickbacks and bribery payments routed through intermediaries, family members, or offshore accounts."},
    {"code": "ML-TF-36", "name": "Arms Trafficking Finance",
     "desc": "Payments for illegal weapons concealed as legitimate trade or routed through multiple jurisdictions."},
    {"code": "ML-TF-37", "name": "Environmental Crime Proceeds",
     "desc": "Proceeds from illegal mining, logging, or wildlife trafficking laundered through commodity trading fronts."},
    {"code": "ML-TF-38", "name": "Cybercrime Infrastructure Finance",
     "desc": "Purchasing bulletproof hosting, exploit kits, or botnet access using layered crypto or prepaid instruments."},
    {"code": "ML-TF-39", "name": "Romance Scam Fund Mule",
     "desc": "Victim of romance scam unknowingly acting as money mule, receiving and forwarding funds believing it is personal payment."},
    {"code": "ML-TF-40", "name": "Business Email Compromise Mule",
     "desc": "Corporate account redirected by BEC attack, funds forwarded through multiple mule accounts to obscure destination."},
    {"code": "ML-TF-41", "name": "Dormant Account Reactivation",
     "desc": "Long-dormant accounts suddenly reactivated with large transactions inconsistent with prior history."},
    {"code": "ML-TF-42", "name": "Over-the-Counter Crypto Purchase",
     "desc": "Large cash purchases of cryptocurrency from OTC brokers to avoid exchange KYC requirements."},
    {"code": "ML-TF-43", "name": "UPI Fraud Ring",
     "desc": "Coordinated network of UPI accounts rapidly exchanging small amounts to test accounts before larger fraud transfers."},
    {"code": "ML-TF-44", "name": "Merchant Collusion",
     "desc": "Merchants colluding with fraudsters to process fictitious transactions and split proceeds."},
    {"code": "ML-TF-45", "name": "SIM Swap Enabled Fraud",
     "desc": "SIM swap attack allows fraudster to intercept OTPs and drain accounts, proceeds rapidly moved through mule accounts."},
    {"code": "ML-TF-46", "name": "Investment Scam Proceeds",
     "desc": "Proceeds from fake investment platforms layered through multiple domestic accounts before conversion to crypto."},
    {"code": "ML-TF-47", "name": "Supply Chain Invoice Fraud",
     "desc": "Intercepting or manipulating supply chain invoices to redirect large corporate payments to fraudster-controlled accounts."},
    {"code": "ML-TF-48", "name": "Salary Account Mule",
     "desc": "Salary accounts of low-income workers used as transit points in exchange for small commissions."},
    {"code": "ML-TF-49", "name": "Cross-Platform Arbitrage Layering",
     "desc": "Exploiting different AML thresholds across payment platforms by splitting transactions across UPI, NEFT, RTGS, and cards."},
    {"code": "ML-TF-50", "name": "Fictitious Export Refund Fraud",
     "desc": "Claiming GST or export refunds on fictitious or inflated export invoices to extract government funds."},
]


class TypologyDNA:
    def __init__(self):
        self._embeddings = None
        self._model = None
        self._tfidf = None
        self._tfidf_matrix = None
        self._backend = None
        self._init_backend()

    def match(self, behavior_summary: str) -> List[TypologyMatch]:
        # Cap defensively here too — build_behavior_summary already caps at
        # 512 chars, but `match()` is a public entry point that can be
        # called with an arbitrary string directly, not only via that path.
        summary = behavior_summary[:512]
        if self._backend == "sentence_transformers":
            return self._match_sbert(summary)
        return self._match_tfidf(summary)

    def build_behavior_summary(self, tx: EnrichedTransaction, det: DetectionResult) -> str:
        f = tx.features
        r = tx.raw

        lines = [
            f"Account {r.sender_account} sent \u20b9{r.amount:,.0f} via {r.channel} "
            f"to {r.receiver_account} at {r.timestamp.strftime('%H:%M on %Y-%m-%d')}.",
        ]

        # --- Sender signals ---
        if f.velocity_24h > 5:
            lines.append(f"Sender made {int(f.velocity_24h)} transactions in the last 24 hours.")
        if abs(f.amount_deviation) > 2:
            lines.append(
                f"Amount is {abs(f.amount_deviation):.1f}\u00d7 standard deviations "
                f"from the sender's 30-day average."
            )
        if f.structuring_proximity > 0.7:
            lines.append("Transaction amount is suspiciously close to the reporting threshold.")
        if f.income_ratio > 0.5:
            lines.append(
                f"Transaction amount represents {f.income_ratio:.1%} of the sender's "
                f"declared monthly income."
            )
        if f.cross_border:
            lines.append("Transaction has cross-border characteristics.")
        if f.device_novelty:
            lines.append("Transaction originated from a new, unrecognised device.")

        # --- Receiver signals ---
        if f.receiver_inflow_spike_ratio > 2:
            lines.append(
                f"Receiver's inbound amount is {f.receiver_inflow_spike_ratio:.1f}\u00d7 its "
                f"historical average, indicating a sudden inflow spike."
            )
        # Guard against the "unknown age" default (0) being misread as a
        # genuinely new account — only flag when we have a real, non-zero
        # age reading below the threshold.
        if 0 < f.receiver_account_age_days < 30:
            lines.append(f"Receiver account is only {int(f.receiver_account_age_days)} days old.")
        if f.receiver_distinct_senders_24h >= 3:
            lines.append(
                f"Receiver has received funds from {int(f.receiver_distinct_senders_24h)} "
                f"distinct senders in the last 24 hours."
            )
        if f.fan_in_out_ratio > 2:
            lines.append(
                f"Receiver shows a fan-in/fan-out ratio of {f.fan_in_out_ratio:.1f}, "
                f"consistent with mule-style fund consolidation."
            )

        # --- Stage 2 detection signals ---
        if det.drift_stage in ("drifting", "critical"):
            lines.append(f"Account behavior is {det.drift_stage}: rising amounts and/or frequency detected.")
        if det.ring_flag:
            lines.append(
                f"Account is a node in a coordinated transaction ring "
                f"(centrality={det.ring_centrality:.2f})."
            )
        if det.fraud_score >= 70:
            lines.append(f"Composite fraud score is elevated at {det.fraud_score:.1f}/100.")

        summary = " ".join(lines)
        return summary[:512]

    def _init_backend(self):
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
            model = SentenceTransformer(CONFIG.typology_model)
            descs = [t["desc"] for t in FATF_TYPOLOGIES]
            self._embeddings = model.encode(descs, normalize_embeddings=True)
            self._model = model
            self._backend = "sentence_transformers"
            logger.info("TypologyDNA: SentenceTransformer backend ready (%d typologies)", len(FATF_TYPOLOGIES))
        except Exception as exc:
            logger.warning("SentenceTransformer unavailable (%s) — using TF-IDF fallback", exc)
            self._init_tfidf()

    def _init_tfidf(self):
        from sklearn.feature_extraction.text import TfidfVectorizer
        descs = [t["desc"] for t in FATF_TYPOLOGIES]
        self._tfidf = TfidfVectorizer(stop_words="english")
        self._tfidf_matrix = self._tfidf.fit_transform(descs)
        self._backend = "tfidf"

    def _match_sbert(self, summary: str) -> List[TypologyMatch]:
        import numpy as np
        q_emb = self._model.encode([summary], normalize_embeddings=True)
        sims = (self._embeddings @ q_emb.T).flatten()
        top_k = int(CONFIG.typology_top_k)
        idxs = sims.argsort()[-top_k:][::-1]
        return [
            TypologyMatch(
                typology_name=FATF_TYPOLOGIES[i]["name"],
                fatf_code=FATF_TYPOLOGIES[i]["code"],
                confidence=round(float(sims[i]), 4),
                description=FATF_TYPOLOGIES[i]["desc"],
            )
            for i in idxs
        ]

    def _match_tfidf(self, summary: str) -> List[TypologyMatch]:
        from sklearn.metrics.pairwise import cosine_similarity
        q_vec = self._tfidf.transform([summary])
        sims = cosine_similarity(q_vec, self._tfidf_matrix).flatten()
        top_k = int(CONFIG.typology_top_k)
        idxs = sims.argsort()[-top_k:][::-1]
        return [
            TypologyMatch(
                typology_name=FATF_TYPOLOGIES[i]["name"],
                fatf_code=FATF_TYPOLOGIES[i]["code"],
                confidence=round(float(sims[i]), 4),
                description=FATF_TYPOLOGIES[i]["desc"],
            )
            for i in idxs
        ]
