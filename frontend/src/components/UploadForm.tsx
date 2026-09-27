import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

const ROLES = [
  { value: 'auto', label: 'Auto (let AI decide)' },
  { value: 'a_roll', label: 'A-roll (primary)' },
  { value: 'b_roll', label: 'B-roll (overlay)' },
  { value: 'voiceover', label: 'Voiceover / dialogue' },
  { value: 'music', label: 'Music' },
  { value: 'sfx', label: 'SFX' },
  { value: 'image', label: 'Image / still' },
  { value: 'script', label: 'Script / notes' },
];

// Review gates: pause the run after a pass so its artifact can be reviewed /
// edited before the next stage consumes it (v1 - see docs/wiki/web_frontend.md).
const GATE_MODES = [
  { value: 'auto', label: 'No pauses — run straight through' },
  { value: 'all', label: 'Review after every pass (1/2/3)' },
  { value: 'pass1', label: 'Review after Pass 1 (narrative)' },
  { value: 'pass2', label: 'Review after Pass 2 (visual log)' },
  { value: 'pass3', label: 'Review after Pass 3 (blueprint)' },
];

interface AssetRow {
  path: string;
  role: string;
}

interface BrowseEntry {
  name: string;
  path: string;
}

interface BrowseFile extends BrowseEntry {
  size: number;
}

interface BrowseResult {
  path: string;
  parent: string | null;
  dirs: BrowseEntry[];
  files: BrowseFile[];
}

function formatSize(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(0)} KB`;
  return `${bytes} B`;
}

// Server-side file picker: the API and this machine share a filesystem, so
// the modal browses real directories via /api/browse and returns absolute
// paths the ingest endpoint can copy from directly. (A browser-native
// <input type="file"> can't expose local paths.)
function FileBrowser({
  onSelect,
  onClose,
}: {
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [dir, setDir] = useState<string | null>(null);
  const [data, setData] = useState<BrowseResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const url = dir ? `/api/browse?path=${encodeURIComponent(dir)}` : '/api/browse';
    setError(null);
    fetch(url)
      .then(async (r) => {
        if (!r.ok) {
          const detail = await r.json().catch(() => null);
          throw new Error(detail?.detail ?? `Browse failed (${r.status})`);
        }
        return r.json() as Promise<BrowseResult>;
      })
      .then(setData)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [dir]);

  return (
    <div className="picker-overlay" onClick={onClose}>
      <div className="picker" onClick={(e) => e.stopPropagation()}>
        <div className="picker-header">
          <span className="picker-path">{data?.path ?? '…'}</span>
          <button type="button" className="picker-close" onClick={onClose}>
            ×
          </button>
        </div>
        {error && <p className="error">{error}</p>}
        <div className="picker-list">
          {data?.parent != null && (
            <button
              type="button"
              className="picker-item picker-up"
              onClick={() => setDir(data.parent)}
            >
              ..
            </button>
          )}
          {data?.dirs.map((d) => (
            <button
              type="button"
              key={d.path}
              className="picker-item picker-dir"
              onClick={() => setDir(d.path)}
            >
              {d.name}/
            </button>
          ))}
          {data?.files.map((f) => (
            <button
              type="button"
              key={f.path}
              className="picker-item picker-file"
              onClick={() => onSelect(f.path)}
            >
              <span className="picker-name">{f.name}</span>
              <span className="picker-size">{formatSize(f.size)}</span>
            </button>
          ))}
          {data && data.dirs.length === 0 && data.files.length === 0 && (
            <p className="picker-empty">No media files here.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function UploadForm() {
  const [name, setName] = useState('');
  const [rows, setRows] = useState<AssetRow[]>([{ path: '', role: 'auto' }]);
  const [browseFor, setBrowseFor] = useState<number | null>(null);
  const [direction, setDirection] = useState('');
  const [gateMode, setGateMode] = useState('auto');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  const updateRow = (i: number, patch: Partial<AssetRow>) => {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  };

  const removeRow = (i: number) => {
    setRows((prev) => prev.filter((_, idx) => idx !== i));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const createResp = await fetch(`/api/projects?name=${encodeURIComponent(name)}`, {
        method: 'POST',
      });
      if (!createResp.ok) {
        const detail = await createResp.json().catch(() => null);
        throw new Error(detail?.detail ?? `Could not create project (${createResp.status})`);
      }

      const assets = rows
        .filter((r) => r.path.trim())
        .map((r) => ({ path: r.path.trim(), role: r.role }));
      if (assets.length === 0) {
        throw new Error('Add at least one file.');
      }

      const ingestResp = await fetch(`/api/projects/${encodeURIComponent(name)}/ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          assets,
          direction: direction.trim() || null,
        }),
      });
      if (!ingestResp.ok) {
        const detail = await ingestResp.json().catch(() => null);
        throw new Error(detail?.detail ?? `Could not ingest files (${ingestResp.status})`);
      }

      navigate(
        `/progress/${name}${gateMode !== 'auto' ? `?gate=${gateMode}` : ''}`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="card">
      <h2>New Project</h2>
      <form onSubmit={handleSubmit}>
        <label>
          Project name
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>

        <fieldset>
          <legend>Files (paste a path or Browse)</legend>
          {rows.map((row, i) => (
            <div className="asset-row" key={i}>
              <input
                className="asset-path"
                value={row.path}
                onChange={(e) => updateRow(i, { path: e.target.value })}
                placeholder="C:/Media/clip.mp4"
              />
              <select
                className="asset-role"
                value={row.role}
                onChange={(e) => updateRow(i, { role: e.target.value })}
              >
                {ROLES.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="asset-browse"
                onClick={() => setBrowseFor(i)}
              >
                Browse…
              </button>
              <button
                type="button"
                className="asset-remove"
                onClick={() => removeRow(i)}
                disabled={rows.length === 1}
              >
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            className="asset-add"
            onClick={() => setRows((prev) => [...prev, { path: '', role: 'auto' }])}
          >
            + Add file
          </button>
        </fieldset>

        <label>
          Review gates
          <select value={gateMode} onChange={(e) => setGateMode(e.target.value)}>
            {GATE_MODES.map((g) => (
              <option key={g.value} value={g.value}>
                {g.label}
              </option>
            ))}
          </select>
        </label>

        <label>
          Direction for the AI editor (optional, plain text)
          <textarea
            value={direction}
            onChange={(e) => setDirection(e.target.value)}
            placeholder={'e.g. "I want fast and snappy editing" or "try a lo-fi backing track"'}
            rows={3}
          />
        </label>

        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Creating…' : 'Create Project'}
        </button>
      </form>

      {browseFor !== null && (
        <FileBrowser
          onSelect={(p) => {
            updateRow(browseFor, { path: p });
            setBrowseFor(null);
          }}
          onClose={() => setBrowseFor(null)}
        />
      )}
    </div>
  );
}

export default UploadForm;
