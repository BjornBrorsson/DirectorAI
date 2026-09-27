import { useEffect, useState } from 'react';

interface Config {
  gemini_model: string;
  gemini_backend: string;
  elevenlabs_configured: boolean;
  log_level: string;
  env: string;
}

function Settings() {
  const [config, setConfig] = useState<Config | null>(null);

  useEffect(() => {
    fetch('/api/settings')
      .then((res) => res.json())
      .then(setConfig)
      .catch((err) => console.error(err));
  }, []);

  if (!config) return <p>Loading…</p>;

  return (
    <div className="card">
      <h2>Settings</h2>
      <p><strong>Gemini backend:</strong> {config.gemini_backend}</p>
      <p><strong>Model:</strong> {config.gemini_model}</p>
      <p>
        <strong>ElevenLabs:</strong>{' '}
        {config.elevenlabs_configured ? 'configured' : 'not configured (SFX/music cues will be stubbed)'}
      </p>
      <p><strong>Log level:</strong> {config.log_level}</p>
      <p><strong>Environment:</strong> {config.env}</p>
      <p className="note">
        Configuration is managed server-side via <code>.env</code>
        (<code>GEMINI_BACKEND</code>, <code>ELEVENLABS_API_KEY</code>, …).
      </p>
    </div>
  );
}

export default Settings;
