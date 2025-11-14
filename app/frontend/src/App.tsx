import { useState, useCallback } from 'react';
import './App.css'
import ChessPage from './ChessPage'
import PGNViewerPage from './PGNViewerPage'
import ArenaPage from './ArenaPage'

function App() {
  const [currentPage, setCurrentPage] = useState<'play' | 'pgnViewer' | 'arena'>('play');
  const [pgnToView, setPgnToView] = useState<string>('');

  const handleViewPgn = useCallback((pgn: string) => {
    setPgnToView(pgn);
    setCurrentPage('pgnViewer');
  }, []);

  const navButtonBaseStyle = "px-4 py-2 text-sm font-medium transition-colors duration-150 rounded-lg focus:outline-none";
  const activeNavButtonStyle = "bg-blue-600 text-white";
  const inactiveNavButtonStyle = "bg-slate-700 text-slate-200 hover:bg-slate-600";

  return (
    <>
      <nav className="bg-slate-800 p-2 shadow-md flex justify-center items-center space-x-3 sticky top-0 z-[1000]">
        <button
          onClick={() => setCurrentPage('play')}
          disabled={currentPage === 'play'}
          className={`${navButtonBaseStyle} ${currentPage === 'play' ? activeNavButtonStyle : inactiveNavButtonStyle}`}
        >
          Play vs Computer
        </button>
        <button
          onClick={() => setCurrentPage('arena')}
          disabled={currentPage === 'arena'}
          className={`${navButtonBaseStyle} ${currentPage === 'arena' ? activeNavButtonStyle : inactiveNavButtonStyle}`}
        >
          LLM vs LLM Arena
        </button>
        <button
          onClick={() => setCurrentPage('pgnViewer')}
          disabled={currentPage === 'pgnViewer'}
          className={`${navButtonBaseStyle} ${currentPage === 'pgnViewer' ? activeNavButtonStyle : inactiveNavButtonStyle}`}
        >
          PGN Viewer
        </button>
      </nav>

      <div className="pt-1">
        {currentPage === 'play' && <ChessPage onViewPgn={handleViewPgn} />}
        {currentPage === 'arena' && <ArenaPage />}
        {currentPage === 'pgnViewer' && <PGNViewerPage pgnString={pgnToView} />}
      </div>
    </>
  )
}

export default App;
