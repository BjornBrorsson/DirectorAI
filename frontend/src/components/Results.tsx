import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

interface Status {
  id: string;
  done: boolean;
  outputs: string[];
}

function Results() {
  const { name } = useParams<{ name: string }>();
  const [status, setStatus] = useState<Status | null>(null);

  useEffect(() => {
    if (!name) return;
    fetch(`/api/projects/${name}/status`)
      .then((res) => res.json())
      .then(setStatus)
      .catch((err) => console.error(err));
  }, [name]);

  if (!status) return <p>Loading…</p>;
  if (!status.done) return <p>Project has not finished yet.</p>;

  return (
    <div className="card">
      <h2>Results: {name}</h2>
      <ul>
        {status.outputs.map((file) => (
          <li key={file}>
            <a href={`/api/projects/${name}/download/${file}`} download>
              {file}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default Results;
