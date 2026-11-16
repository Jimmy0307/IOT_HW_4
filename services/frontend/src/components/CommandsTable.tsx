import type { CommandRow } from "../api/types";
import { formatDateTime, humanize, truncate } from "../lib/format";
import { commandStatusLevel } from "../lib/status";
import { Badge } from "./Badge";

function membersText(members: Record<string, string> | null | undefined): string {
  if (!members || typeof members !== "object") return "--";
  const parts = Object.entries(members).map(([k, v]) => `${k}: ${String(v)}`);
  return parts.length > 0 ? parts.join(", ") : "--";
}

interface CommandsTableProps {
  commands: CommandRow[] | undefined;
  highlightId?: string | null;
}

export function CommandsTable({ commands, highlightId }: CommandsTableProps) {
  const rows = commands ?? [];
  return (
    <div className="table-wrap">
      <table className="table" data-testid="commands-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Group</th>
            <th>Requested</th>
            <th>Status</th>
            <th>Operator</th>
            <th>Reason</th>
            <th>Members / detail</th>
            <th>Command ID</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td className="empty" colSpan={8}>
                No commands recorded for this site.
              </td>
            </tr>
          ) : (
            rows.map((c) => {
              const status = c.status ?? "UNKNOWN";
              const id = String(c.command_id);
              return (
                <tr
                  key={id}
                  data-testid="command-row"
                  data-command-id={id}
                  className={highlightId && highlightId === id ? "row-highlight" : undefined}
                >
                  <td className="nowrap">{formatDateTime(c.acked_at || c.created_ts)}</td>
                  <td>{humanize(c.group)}</td>
                  <td className="mono">{c.requested ?? "--"}</td>
                  <td>
                    <Badge level={commandStatusLevel(status)} testId="command-status">
                      {status}
                    </Badge>
                  </td>
                  <td>{c.operator || "--"}</td>
                  <td>{c.reason ? truncate(String(c.reason), 64) : "--"}</td>
                  <td className="details">
                    {membersText(c.members)}
                    {c.detail ? <span className="sub">{truncate(String(c.detail), 120)}</span> : null}
                  </td>
                  <td className="mono" title={id}>
                    {id.slice(0, 8)}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
