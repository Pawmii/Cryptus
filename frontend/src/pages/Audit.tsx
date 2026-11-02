import { Fragment, useState } from "react";
import { Alert, Button, ErrorBlock, LoadingBlock, PageHead, Panel } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, shortHash } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { Paged } from "../lib/types";

interface Entry { id: number; at: string; actor_type: string; actor: string | null; action: string; target: string | null;
  details: Record<string, unknown>; ip: string | null; hash: string }

export function Audit() {
  const [prefix, setPrefix] = useState("");
  const [offset, setOffset] = useState(0);
  const log = useApi<Paged<Entry>>(`/api/admin/audit?limit=100&offset=${offset}${prefix ? `&action=${encodeURIComponent(prefix)}` : ""}`);
  const [verify, setVerify] = useState<{ ok: boolean; broken_at: number | null } | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  if (!log.data) return log.error ? <ErrorBlock error={log.error} /> : <LoadingBlock />;
  return (
    <>
      <PageHead title="Audit log" actions={<Button size="sm" icon="shield" onClick={async () => setVerify(await api.get("/api/admin/audit/verify"))}>Verify chain</Button>}>
        Append-only and hash-chained: each entry commits to the previous one, so any edit or deletion is detectable.
      </PageHead>
      {verify && (verify.ok ? <Alert tone="success" title="Chain intact">Every entry links correctly to its predecessor.</Alert>
        : <Alert tone="danger" title="Chain broken">Entry #{verify.broken_at} does not match its recorded hash. Investigate database access immediately.</Alert>)}
      <div className="filters">
        {["", "auth.", "invoice.", "sweep.", "treasury.", "network.", "keychain.", "wallet.", "api_key.", "webhook.", "user.", "settings."].map((p) => (
          <button key={p} className="btn btn--sm btn--ghost" aria-pressed={prefix === p}
            style={prefix === p ? { color: "var(--fg)", boxShadow: "inset 0 -1px 0 var(--accent)" } : undefined}
            onClick={() => { setPrefix(p); setOffset(0); }}>{p ? p.replace(".", "") : "all"}</button>
        ))}
      </div>
      <Panel flush>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>#</th><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Hash</th></tr></thead>
          <tbody>{log.data.items.map((e) => (
            <Fragment key={e.id}>
              <tr data-href onClick={() => setOpen(open === e.id ? null : e.id)}>
                <td className="num xs muted">{e.id}</td>
                <td className="xs nowrap">{dateTime(e.at)}</td>
                <td className="small"><div><span>{e.actor ?? e.actor_type}</span><span className="xs faint">{e.actor_type}{e.ip ? ` · ${e.ip}` : ""}</span></div></td>
                <td className="mono xs">{e.action}</td>
                <td className="mono xs muted">{shortHash(e.target, 8, 4)}</td>
                <td className="mono xs faint">{e.hash.slice(0, 10)}</td>
              </tr>
              {open === e.id && <tr><td colSpan={6}><pre className="code">{JSON.stringify(e.details, null, 2)}</pre></td></tr>}
            </Fragment>))}</tbody></table></div>
        <div className="panel__foot" style={{ justifyContent: "space-between" }}>
          <span className="xs muted num">{log.data.total} entries</span>
          <div className="row">
            <Button size="sm" variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Newer</Button>
            <Button size="sm" variant="ghost" disabled={offset + 100 >= log.data.total} onClick={() => setOffset(offset + 100)}>Older</Button>
          </div>
        </div>
      </Panel>
    </>
  );
}
