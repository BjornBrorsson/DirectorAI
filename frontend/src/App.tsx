import { Link, Route, Routes } from 'react-router-dom';
import ProjectList from './components/ProjectList';
import UploadForm from './components/UploadForm';
import Progress from './components/Progress';
import Results from './components/Results';
import Settings from './components/Settings';
import './App.css';

function App() {
  return (
    <div className="app">
      <header>
        <h1>DirectorCut-AI</h1>
        <nav>
          <Link to="/">Projects</Link>
          <Link to="/upload">New Project</Link>
          <Link to="/settings">Settings</Link>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<ProjectList />} />
          <Route path="/upload" element={<UploadForm />} />
          <Route path="/progress/:name" element={<Progress />} />
          <Route path="/results/:name" element={<Results />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
