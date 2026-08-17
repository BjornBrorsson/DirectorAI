import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

function UploadForm() {
  const [name, setName] = useState('');
  const [video, setVideo] = useState('');
  const [audio, setAudio] = useState('');
  const [script, setScript] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

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

      const ingestResp = await fetch(`/api/projects/${encodeURIComponent(name)}/ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_path: video,
          audio_path: audio || null,
          script_path: script || null,
        }),
      });
      if (!ingestResp.ok) {
        const detail = await ingestResp.json().catch(() => null);
        throw new Error(detail?.detail ?? `Could not ingest files (${ingestResp.status})`);
      }

      navigate(`/progress/${name}`);
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
        <label>
          Video path (absolute, on this machine)
          <input value={video} onChange={(e) => setVideo(e.target.value)} placeholder="C:/Media/gameplay.mp4" required />
        </label>
        <label>
          Voiceover / audio path (optional)
          <input value={audio} onChange={(e) => setAudio(e.target.value)} placeholder="C:/Media/voice.mp3" />
        </label>
        <label>
          Script path (optional, .txt)
          <input value={script} onChange={(e) => setScript(e.target.value)} placeholder="C:/Media/script.txt" />
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Creating…' : 'Create Project'}
        </button>
      </form>
    </div>
  );
}

export default UploadForm;
