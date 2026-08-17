import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

function Progress() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const [logs, setLogs] = useState<string[]>([]);
  const ws = useRef<WebSocket | null>(null);

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
      const msg = JSON.parse(event.data);
      setLogs((prev) => [...prev, JSON.stringify(msg)]);
      if (msg.status === 'done') {
        navigate(`/results/${name}`);
      }
      if (msg.status === 'error') {
        console.error('Pipeline error:', msg.error);
      }
    };
    socket.onopen = () => {
      socket.send(JSON.stringify({ action: 'run' }));
    };
    return () => socket.close();
  }, [name, navigate]);

  return (
    <div className="card">
      <h2>Pipeline: {name}</h2>
      <ul>
        {logs.map((l, i) => (
          <li key={i}><code>{l}</code></li>
        ))}
      </ul>
    </div>
  );
}

export default Progress;
