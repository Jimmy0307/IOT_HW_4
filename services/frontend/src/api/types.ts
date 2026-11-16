// Response shapes mirrored from services/orchid/central_api/store.py and status.py.
// Every field coming from the API is untrusted: render as text only.

export type Role = "viewer" | "operator";

export interface LoginResponse {
  access_token: string;
  token_type: string;
  role: Role;
  username: string;
  expires_in: number;
}

export type SiteStatus = "ONLINE" | "DEGRADED" | "OFFLINE" | "UNKNOWN";
export type ActuatorDisplay = "ON" | "OFF" | "STALE" | "UNKNOWN";
export type GroupDisplay = "ON" | "OFF" | "STALE" | "MIXED";
export type ReadingLevel = "normal" | "warning" | "critical" | "stale" | "unknown";
export type DataQuality = "GOOD" | "SUSPECT" | "STALE" | "UNKNOWN";
export type GroupName = "fan_group" | "dehumidifier_group";
export type Mode = "AUTO" | "MANUAL_OVERRIDE" | "MAINTENANCE";

export interface ReadingView {
  value: number | null;
  unit?: string | null;
  device_ts?: string | null;
  gateway_ingest_ts?: string | null;
  quality?: string | null;
  validation_result?: string | null;
  fresh: boolean;
  display: "FRESH" | "STALE" | "UNKNOWN";
  level: ReadingLevel;
  age_s: number | null;
  low_threshold?: number;
  high_threshold?: number;
  last_result?: string | null;
}

export interface ActuatorView {
  state: string | null;
  display: ActuatorDisplay;
  fresh: boolean;
  observed_at: string | null;
  quality: string | null;
  commanded?: string | null;
  age_s: number | null;
  group: GroupName;
}

export interface GroupView {
  members: string[];
  display: GroupDisplay;
}

export interface SiteTargets {
  temperature: number;
  humidity: number;
  /** Serialized from Site.thresholds(): [high, low] (order normalised client-side). */
  temperature_band: number[];
  humidity_band: number[];
  interlock_temperature: number;
}

export interface SiteSummary {
  site_id: string;
  display_name: string;
  status: SiteStatus;
  mode: string | null;
  health: string | null;
  issues: string[];
  bridge: "UP" | "DOWN" | "UNKNOWN";
  temperature: ReadingView;
  humidity: ReadingView;
  actuators: Record<string, ActuatorView>;
  groups: Record<string, GroupView>;
  data_quality: DataQuality;
  last_updated: string | null;
  active_alerts: number;
  targets: SiteTargets;
}

export interface OverviewResponse {
  generated_at: string;
  sites: SiteSummary[];
}

export interface TrendPoint {
  t: string;
  v: number;
  q: string | null;
}

export interface CommandRow {
  command_id: string;
  group: string;
  requested: string | null;
  operator: string | null;
  reason: string | null;
  status: string | null;
  detail: string | null;
  members: Record<string, string>;
  source: string | null;
  mode: string | null;
  acked_at: string | null;
  created_ts: string | null;
}

export interface EventRow {
  id: number;
  ingest_ts: string;
  site: string;
  principal: string;
  kind: string;
  event_type: string;
  severity: string;
  event_id: string;
  claimed_ts: string | null;
  details: unknown;
}

export interface GatewayInfo {
  heartbeat?: Record<string, unknown> | null;
  heartbeat_age_s?: number | null;
  bridge_state?: string | null;
  bridge_ts?: string | null;
}

export interface SiteDetail extends SiteSummary {
  trend: { temperature: TrendPoint[]; humidity: TrendPoint[] };
  sensor_quality: { temperature: Record<string, number>; humidity: Record<string, number> };
  recent_commands: CommandRow[];
  recent_alerts: EventRow[];
  gateway: GatewayInfo;
}

export interface EventsResponse {
  events: EventRow[];
}

export interface SystemResponse {
  generated_at: string;
  broker: {
    status: "UP" | "STALE" | "UNKNOWN";
    clients_connected: number | null;
    msgs_received_1min: number | null;
    msgs_sent_1min: number | null;
    age_s: number | null;
  };
  historian: { connected: boolean; heartbeat_age_s: number | null };
  sites_total: number;
  sites_connected: number;
  site_status: Record<string, SiteStatus>;
  stale_sensors: number;
  security: {
    broker_auth_failures: number;
    broker_acl_denials_logged: number;
    login_failures: number;
    authz_denied: number;
    event_schema_rejected: number;
    untrusted_claims_dropped: number;
    topic_identity_mismatch: number;
    replay_detected: number;
    rate_limited_events: number;
    command_rejected: number;
    command_invalid: number;
  };
  commands: Record<string, number>;
  message_rate: {
    broker_received_1min: number | null;
    late_telemetry: number;
    gateway_telemetry: number;
  };
  audit: {
    integrity: string;
    rows: number;
    checked_at: string | null;
    head_prefix: string | number | null;
  };
  ingest: { queue_dropped_critical: number; queue_dropped_normal: number; errors: number };
  central_api?: { mqtt_connected: boolean };
}

export interface CommandAccepted {
  command_id: string;
  expires_at: string;
  status: "SENT";
}
