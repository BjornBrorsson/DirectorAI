import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

interface PipeEvent {
  status: string;
  stage?: string;
  detail?: string;
  elapsed_s?: number;
  error?: string;
}

interface StageArtifact {
  artifact: string;
  data: unknown;
  listing?: string[];
}

const STAGE_LABELS: Record<string, string> = {
  ingest: 'Ingest',
  proxy: 'Proxies',
  interpret: 'Asset interpretation',
  pass1: 'Pass 1 — narrative',
  pass2: 'Pass 2 — visual',
  pass3: 'Pass 3 — director',
  audio: 'Audio cues',
  conform: 'FCPXML conform',
};

const GATE_LABELS: Record<string, string> = {
  pass1: 'Pass 1 — narrative nodes',
  pass2: 'Pass 2 — visual events',
  pass3: 'Pass 3 — blueprint',
};

function formatElapsed(s: number | undefined): string {
  if (s === undefined) return '';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

function describe(e: PipeEvent): string {
  if (e.status === 'progress') {
    const label = STAGE_LABELS[e.stage ?? ''] ?? e.stage ?? '';
    return `[${formatElapsed(e.elapsed_s)}] ${label}${e.detail ? ` — ${e.detail}` : ''}`;
  }
  if (e.status === 'awaiting_gate') {
    return `PAUSED at review gate: ${GATE_LABELS[e.stage ?? ''] ?? e.stage}`;
  }
  if (e.status === 'error') return `ERROR: ${e.error ?? 'unknown'}`;
  if (e.status === 'done') return 'Done — FCPXML written';
  return e.status;
}

/**
 * v1 review-gate panel: shows the stage artifact as editable JSON (plus the
 * human-readable cut listing for pass3) and resumes the run with or without
 * edits. Edits replace the whole checkpoint file server-side.
 */
function GatePanel({ name, stage }: { name: string; stage: string }) {
  const [artifact, setArtifact] = useState<StageArtifact | null>(null);
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setError(null);
    fetch(`/api/projects/${encodeURIComponent(name)}/stage/${stage}`)
      .then(async (r) => {
        if (!r.ok) {
          const detail = await r.json().catch(() => null);
          throw new Error(detail?.detail ?? `Could not load artifact (${r.status})`);
        }
        return r.json() as Promise<StageArtifact>;
      })
      .then((a) => {
        setArtifact(a);
        setText(JSON.stringify(a.data, null, 2));
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [name, stage]);

  const resume = async (withEdits: boolean) => {
    setError(null);
    let edits: unknown = undefined;
    if (withEdits) {
      try {
        edits = JSON.parse(text);
      } catch (err) {
        setError(`Invalid JSON: ${err instanceof Error ? err.message : err}`);
        return;
      }
    }
    setBusy(true);
    try {
      const r = await fetch(
        `/api/projects/${encodeURIComponent(name)}/stage/${stage}/resume`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(edits === undefined ? {} : { edits }),
        },
      );
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        throw new Error(detail?.detail ?? `Resume failed (${r.status})`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gate-panel">
      <p className="gate-banner">
        ⏸ Paused for review: <b>{GATE_LABELS[stage] ?? stage}</b>
        {artifact ? ` (${artifact.artifact})` : ''}
      </p>
      {artifact?.listing && artifact.listing.length > 0 && (
        <pre className="gate-listing">{artifact.listing.join('\n')}</pre>
      )}
      {artifact && (
        <textarea
          className="gate-editor"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={16}
          spellCheck={false}
        />
      )}
      {error && <p className="error">{error}</p>}
      <div className="gate-actions">
        <button type="button" onClick={() => resume(false)} disabled={busy}>
          Approve &amp; continue
        </button>
        <button type="button" onClick={() => resume(true)} disabled={busy}>
          Apply edits &amp; continue
        </button>
      </div>
    </div>
  );
}

function Progress() {
  const { name } = useParams<{ name: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [events, setEvents] = useState<PipeEvent[]>([]);
  const [ticker, setTicker] = useState(0);
  const ws = useRef<WebSocket | null>(null);

  // Tick once a second so the "running for Xm" label moves during long
  // model calls that emit no events of their own.
  useEffect(() => {
    const id = window.setInterval(() => setTicker((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    if (!name) return;
    // Build the socket URL relative to the current page so it works both
    // through the Vite dev proxy (npm run dev) and when served directly by
    // the FastAPI app in production - a hardcoded 127.0.0.1:8000 only works
    // for the latter.
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${proto}//${window.location.host}/ws/${name}`);
    ws.current = socket;
    socket.onmessage = (event) => {
      const msg = JSON.parse(event.data) as PipeEvent;
      setEvents((prev) => [...prev, msg]);
      if (msg.status === 'done') {
        navigate(`/results/${name}`);
      }
      if (msg.status === 'error') {
        console.error('Pipeline error:', msg.error);
      }
    };
    socket.onopen = () => {
      // ?gate=<mode> comes from the UploadForm review-gate selector; the
      // server only applies it when this socket launches a fresh run.
      const gate = searchParams.get('gate');
      socket.send(
        JSON.stringify({ action: 'run', ...(gate ? { gate_mode: gate } : {}) }),
      );
    };
    return () => socket.close();
  }, [name, navigate, searchParams]);

  const latest = [...events].reverse().find((e) => e.status === 'progress');
  const terminal = [...events]
    .reverse()
    .find((e) => e.status === 'done' || e.status === 'error');
  const running = !terminal;
  // While the run is paused at a review gate no further events are emitted -
  // the last event IS the awaiting_gate. The resume endpoint releases the
  // run, whose first follow-up event then hides the panel.
  const last = events.length ? events[events.length - 1] : null;
  const showGate = running && last?.status === 'awaiting_gate';

  return (
    <div className="card">
      <h2>Pipeline: {name}</h2>
      {running ? (
        <p className="progress-now">
          {latest ? (
            <>
              <b>{STAGE_LABELS[latest.stage ?? ''] ?? latest.stage}</b>
              {latest.detail ? ` — ${latest.detail}` : ''}
              <span className="progress-elapsed">
                {' '}({formatElapsed(latest.elapsed_s)})
              </span>
            </>
          ) : (
            'Starting…'
          )}
          <span className="progress-tick" aria-hidden>
            {' '}{ticker % 2 === 0 ? '●' : '○'}
          </span>
        </p>
      ) : (
        <p className={terminal?.status === 'error' ? 'error' : 'progress-now'}>
          {describe(terminal!)}
        </p>
      )}
      {showGate && name && last?.stage && (
        <GatePanel name={name} stage={last.stage} />
      )}
      <ul className="progress-log">
        {events.map((e, i) => (
          <li key={i} className={e.status === 'error' ? 'error' : ''}>
            <code>{describe(e)}</code>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default Progress;
