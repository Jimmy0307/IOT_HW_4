import type { ActuatorView } from "../api/types";
import { actuatorLabel, actuatorLevel } from "../lib/status";
import { Badge } from "./Badge";

/** "fan01" -> "Fan01", "dehumidifier02" -> "Dehumidifier02". */
export function deviceLabel(id: string): string {
  return id.length > 0 ? id.charAt(0).toUpperCase() + id.slice(1) : id;
}

interface ActuatorChipProps {
  id: string;
  view: ActuatorView | undefined;
  showAge?: boolean;
}

export function ActuatorChip({ id, view, showAge = false }: ActuatorChipProps) {
  const display = view?.display ?? "UNKNOWN";
  const level = actuatorLevel(display, view?.quality);
  const label = actuatorLabel(display, view?.state);
  const testId = /^[a-z0-9]{1,32}$/.test(id) ? `actuator-${id}` : undefined;
  return (
    <div className="actuator">
      <span className="actuator-name">{deviceLabel(id)}</span>
      <Badge
        level={level}
        testId={testId}
        variant={display === "ON" ? "solid" : "soft"}
        title={view?.observed_at ? `observed ${view.observed_at}` : "no observation"}
      >
        {label}
      </Badge>
      {showAge && view?.quality === "BAD" ? <span className="actuator-note">observed != commanded</span> : null}
      {showAge && view?.age_s !== null && view?.age_s !== undefined ? (
        <span className="actuator-note">{`${Math.round(view.age_s)} s`}</span>
      ) : null}
    </div>
  );
}
