import type { EventRow } from "../api/types";
import { formatDateTime, formatDetails, humanize } from "../lib/format";
import { severityLevel } from "../lib/status";
import { Badge } from "./Badge";

interface EventRowViewProps {
  event: EventRow;
  showSite?: boolean;
  showPrincipal?: boolean;
}

/**
 * One event row. Every API-supplied string (event_type, principal, details...)
 * is rendered as a React text node - never as markup.
 */
export function EventRowView({ event, showSite = true, showPrincipal = true }: EventRowViewProps) {
  const details = formatDetails(event.details);
  return (
    <tr data-testid="event-row">
      <td className="nowrap" title={String(event.ingest_ts)}>
        {formatDateTime(event.ingest_ts)}
      </td>
      {showSite ? <td className="nowrap">{String(event.site)}</td> : null}
      {showPrincipal ? <td className="mono">{String(event.principal)}</td> : null}
      <td>
        <span className="event-type">{humanize(String(event.event_type))}</span>
        <span className="event-kind">{String(event.kind)}</span>
      </td>
      <td>
        <Badge level={severityLevel(event.severity, event.event_type)}>{String(event.severity)}</Badge>
      </td>
      <td className="details" data-testid="event-details">
        {details}
      </td>
    </tr>
  );
}

interface EventsTableProps {
  events: EventRow[] | undefined;
  testId: string;
  showSite?: boolean;
  showPrincipal?: boolean;
  emptyText?: string;
}

export function EventsTable({ events, testId, showSite = true, showPrincipal = true, emptyText }: EventsTableProps) {
  const rows = events ?? [];
  return (
    <div className="table-wrap">
      <table className="table" data-testid={testId}>
        <thead>
          <tr>
            <th>Time</th>
            {showSite ? <th>Site</th> : null}
            {showPrincipal ? <th>Principal</th> : null}
            <th>Type</th>
            <th>Severity</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td className="empty" colSpan={4 + (showSite ? 1 : 0) + (showPrincipal ? 1 : 0)}>
                {emptyText ?? "No events."}
              </td>
            </tr>
          ) : (
            rows.map((e) => (
              <EventRowView key={`${e.id}-${e.event_id}`} event={e} showSite={showSite} showPrincipal={showPrincipal} />
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
