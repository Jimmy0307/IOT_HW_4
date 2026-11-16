import { useState } from "react";
import { ApiError, api } from "../api/client";
import type { CommandAccepted, CommandRow, GroupName, Mode } from "../api/types";
import { commandStatusLevel } from "../lib/status";
import { validateReason, validateTtl } from "../lib/validation";
import { Badge } from "./Badge";

const DEFAULT_OVERRIDE_TTL = "300";

interface Sent {
  label: string;
  accepted: CommandAccepted;
}

interface ControlPanelProps {
  siteId: string;
  currentMode: string | null;
  recentCommands: CommandRow[] | undefined;
  onSent: (commandId: string) => void;
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return "Not permitted: operator role required.";
    if (err.status === 422) return "Rejected by server validation.";
    return `Command failed (${err.status || "network"}): ${err.message}`;
  }
  return "Command failed: unexpected error.";
}

export function ControlPanel({ siteId, currentMode, recentCommands, onSent }: ControlPanelProps) {
  const [reason, setReason] = useState("");
  const [ttl, setTtl] = useState(DEFAULT_OVERRIDE_TTL);
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);
  const [sent, setSent] = useState<Sent | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reasonCheck = validateReason(reason);
  const ttlCheck = validateTtl(ttl);
  // Buttons stay clickable so an invalid reason produces a visible error instead of a dead button.
  const disabled = busy;

  const ack = sent ? recentCommands?.find((c) => c.command_id === sent.accepted.command_id) : undefined;
  const ackStatus = ack?.status ?? (sent ? "SENT" : null);

  const submit = async (label: string, call: () => Promise<CommandAccepted>) => {
    setTouched(true);
    if (!reasonCheck.ok) {
      setError(`Not sent: ${reasonCheck.error ?? "invalid reason"}`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const accepted = await call();
      setSent({ label, accepted });
      onSent(accepted.command_id);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  const group = (g: GroupName, state: "ON" | "OFF", label: string) => () =>
    void submit(label, () => api.sendGroupCommand(siteId, g, state, reason));

  const mode = (m: Mode, label: string) => () => {
    if (m === "MANUAL_OVERRIDE" && !ttlCheck.ok) {
      setError(ttlCheck.error);
      return;
    }
    void submit(label, () => api.setMode(siteId, m, reason, m === "MANUAL_OVERRIDE" ? ttlCheck.value ?? undefined : undefined));
  };

  return (
    <section className="card control-panel" data-testid="control-panel" aria-labelledby="control-title">
      <header className="section-head">
        <h2 id="control-title">Operator control</h2>
        <span className="muted">{`Current mode: ${currentMode ?? "UNKNOWN"}`}</span>
      </header>

      <label className="field">
        <span className="field-label">Reason (required)</span>
        <input
          data-testid="cmd-reason"
          type="text"
          value={reason}
          maxLength={64}
          autoComplete="off"
          placeholder="e.g. lab test: humidity high"
          aria-invalid={touched && !reasonCheck.ok}
          aria-describedby="reason-help"
          onChange={(e) => {
            setReason(e.target.value);
            setTouched(true);
          }}
        />
        <span id="reason-help" className={touched && !reasonCheck.ok ? "field-error" : "field-help"}>
          {touched && reasonCheck.error ? reasonCheck.error : "1-64 chars: letters, digits, space, _ . : -"}
        </span>
      </label>

      <div className="control-grid">
        <div className="control-group">
          <h3>Fan group</h3>
          <div className="btn-row">
            <button type="button" className="btn btn-on" data-testid="cmd-fan-on" disabled={disabled} onClick={group("fan_group", "ON", "Fan group ON")}>
              Fans ON
            </button>
            <button type="button" className="btn btn-off" data-testid="cmd-fan-off" disabled={disabled} onClick={group("fan_group", "OFF", "Fan group OFF")}>
              Fans OFF
            </button>
          </div>
        </div>
        <div className="control-group">
          <h3>Dehumidifier group</h3>
          <div className="btn-row">
            <button type="button" className="btn btn-on" data-testid="cmd-dehum-on" disabled={disabled} onClick={group("dehumidifier_group", "ON", "Dehumidifier group ON")}>
              Dehumidifiers ON
            </button>
            <button type="button" className="btn btn-off" data-testid="cmd-dehum-off" disabled={disabled} onClick={group("dehumidifier_group", "OFF", "Dehumidifier group OFF")}>
              Dehumidifiers OFF
            </button>
          </div>
        </div>
        <div className="control-group control-group-wide">
          <h3>Mode</h3>
          <div className="btn-row">
            <button type="button" className="btn" data-testid="mode-auto" disabled={disabled} onClick={mode("AUTO", "Mode AUTO")}>
              AUTO
            </button>
            <button type="button" className="btn btn-warn" data-testid="mode-override" disabled={disabled} onClick={mode("MANUAL_OVERRIDE", "Mode MANUAL_OVERRIDE")}>
              MANUAL OVERRIDE
            </button>
            <label className="ttl-field">
              <span>TTL (s)</span>
              <input
                data-testid="mode-ttl"
                type="text"
                inputMode="numeric"
                value={ttl}
                maxLength={6}
                aria-invalid={!ttlCheck.ok}
                onChange={(e) => setTtl(e.target.value)}
              />
            </label>
            <button type="button" className="btn btn-warn" data-testid="mode-maintenance" disabled={disabled} onClick={mode("MAINTENANCE", "Mode MAINTENANCE")}>
              MAINTENANCE
            </button>
          </div>
          <p className="field-help">
            A group command in AUTO switches the site to MANUAL_OVERRIDE; it is REJECTED in MAINTENANCE, during
            FAILSAFE, or (fans OFF) while the temperature interlock is active.
          </p>
        </div>
      </div>

      <div className="control-result" aria-live="polite">
        {busy ? <span className="muted">Sending...</span> : null}
        {error ? (
          <div className="banner banner-critical" data-testid="cmd-error" data-level="critical">
            <strong>ERROR</strong>
            <span>{error}</span>
          </div>
        ) : null}
        {sent && !error ? (
          <div className="sent" data-testid="cmd-result">
            <span>{`${sent.label} accepted. Command ID `}</span>
            <code data-testid="cmd-id">{sent.accepted.command_id}</code>
            <span>{" - ack: "}</span>
            <Badge level={commandStatusLevel(ackStatus)} testId="cmd-ack-status">
              {ackStatus ?? "SENT"}
            </Badge>
            <span className="muted">{" (see Recent commands below)"}</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}
