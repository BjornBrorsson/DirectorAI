import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

interface Project {
  id: string;
  name: string;
  status: string;
}

function ProjectList() {
  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    fetch('/api/projects')
      .then((res) => res.json())
      .then(setProjects)
      .catch((err) => console.error(err));
  }, []);

  return (
    <div className="card">
      <h2>Projects</h2>
      {projects.length === 0 ? (
        <p>No projects yet. <Link to="/upload">Create one</Link>.</p>
      ) : (
        <ul>
          {projects.map((p) => (
            <li key={p.id}>
              {p.name} — <em>{p.status}</em> —
              <Link to={`/progress/${p.name}`}> run</Link>
              {' | '}
              <Link to={`/results/${p.name}`}> results</Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default ProjectList;
