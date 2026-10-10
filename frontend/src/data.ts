import type {
  ActionVerdict,
  ConfidenceBand,
  FullDossier,
  EvaluationResponse,
  TransactionRequest,
} from "./types";

export const EMPTY_TRANSACTION_REQUEST: TransactionRequest = {
  transaction_id: "",
  sender_account: "",
  receiver_account: "",
  amount: 0,
  timestamp: "",
  channel: "",
  device_fingerprint_hash: "",
  ip_geolocation: "",
  merchant_category_code: "",
};
export const MOCK_SCENARIO_A: {
  request: TransactionRequest;
  response: EvaluationResponse;
  dossier: FullDossier;
} = {
  request: {
    transaction_id: "TX_MULE_RING_991",
    sender_account: "ACC_MULE_SENDER_01",
    receiver_account: "ACC_MULE_RECV_02",
    amount: 850000,
    timestamp: new Date().toISOString(),
    channel: "SWIFT",
    device_fingerprint_hash: "dev_hash_unrecognized_99x",
    ip_geolocation: "KY",
    merchant_category_code: "7995",
  },
  response: {
    transaction_id: "TX_MULE_RING_991",
    action: "BLOCK" as ActionVerdict,
    fraud_score: 94.2,
    primary_reason: "mule_ring_detected",
    requires_manual_review: true,
    sar_narrative:
      "SUSPICIOUS ACTIVITY REPORT (SAR)\n\nExecutive Summary: Transaction TX_MULE_RING_991 has been flagged for immediate BLOCK action due to direct association with an active coordinated transaction mule ring.\n\nKey Risk Drivers:\n• Graph Analysis identified account ACC_MULE_SENDER_01 as a primary node in a cyclic fund routing ring.\n• Extreme inflow spike ratio (18.4x above historical average).\n• Cross-border routing from high-risk jurisdiction (KY) combined with gambling MCC 7995.\n\nRecommendation: Freeze accounts and report to national FIU.",
  },
  dossier: {
    detection_result: {
      lightgbm_prob: 0.91,
      anomaly_score: 0.88,
      drift_stage: "critical",
      confidence_band: "high" as ConfidenceBand,
    },
    graph_summary: {
      mule_ring_flag: true,
      centrality_score: 0.89,
      cycle_detected: true,
      cycle_nodes: [
        "ACC_MULE_SENDER_01",
        "ACC_MULE_RECV_02",
        "ACC_TRANSIT_03",
        "ACC_MULE_SENDER_01",
      ],
      neighbor_count: 14,
    },
    top_reason_codes: [
      {
        feature_name: "mule_ring_flag",
        label: "Mule Ring Graph Association",
        value: 1,
        impact: "positive",
        attribution_score: 0.38,
        human_description: "Account is a node in a cyclic transaction ring.",
      },
      {
        feature_name: "receiver_inflow_spike_ratio",
        label: "Receiver Inflow Spike Ratio",
        value: 18.4,
        impact: "positive",
        attribution_score: 0.26,
        human_description:
          "Receiver inbound volume is 18.4x higher than normal baseline.",
      },
      {
        feature_name: "receiver_account_age_days",
        label: "Receiver Account Age",
        value: 1200,
        impact: "negative",
        attribution_score: -0.08,
        human_description:
          "Receiver account age (1200 days) slightly decreases risk.",
      },
    ],
    typology_matches: [
      {
        typology_name: "Mule Account Network",
        fatf_code: "ML-TF-05",
        confidence: 0.92,
        description:
          "Coordinated accounts receiving and forwarding illicit funds rapidly.",
      },
      {
        typology_name: "Rapid Succession Transfers",
        fatf_code: "ML-TF-15",
        confidence: 0.84,
        description:
          "Moving funds through multiple accounts in rapid succession.",
      },
    ],
    proceeds_trail: [
      {
        hop_number: 1,
        from_account: "ACC_MULE_SENDER_01",
        to_account: "ACC_MULE_RECV_02",
        amount: 850000,
        timestamp_delta_hours: 0,
      },
      {
        hop_number: 2,
        from_account: "ACC_MULE_RECV_02",
        to_account: "ACC_TRANSIT_03",
        amount: 845000,
        timestamp_delta_hours: 0.2,
      },
      {
        hop_number: 3,
        from_account: "ACC_TRANSIT_03",
        to_account: "ACC_MULE_SENDER_01",
        amount: 840000,
        timestamp_delta_hours: 0.5,
      },
    ],
  },
};

export const SCENARIO_PRESETS: Array<{ label: string; request: TransactionRequest }> = [
  {
    label: "Mule Ring",
    request: {
      transaction_id: "DEMO_TX_003_BLOCK_HOLD",
      timestamp: "2026-08-07T14:45:00Z",
      sender_account: "ACC_1029",
      receiver_account: "ACC_9999",
      amount: 750000,
      channel: "SWIFT",
      device_fingerprint_hash: "dev_unrecognized_9999",
      ip_geolocation: "KY",
      merchant_category_code: "7995",
    },
  },
  {
    label: "Suspicious",
    request: {
      transaction_id: "DEMO_TX_002_MONITOR",
      timestamp: "2026-08-07T14:42:00Z",
      sender_account: "ACC_1029",
      receiver_account: "ACC_1001",
      amount: 180000,
      channel: "SWIFT",
      device_fingerprint_hash: "baa6b779cdf457ff8eaf",
      ip_geolocation: "US",
      merchant_category_code: "5944",
    },
  },
  {
    label: "Clean Transfer",
    request: {
      transaction_id: "DEMO_TX_001_CLEAN",
      timestamp: "2026-08-07T14:40:00Z",
      sender_account: "ACC_1029",
      receiver_account: "ACC_1001",
      amount: 2500,
      channel: "UPI",
      device_fingerprint_hash: "baa6b779cdf457ff8eaf",
      ip_geolocation: "IN",
      merchant_category_code: "5411",
    },
  },
];
