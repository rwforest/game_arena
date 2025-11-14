import { useState, useRef, useEffect, useCallback } from 'react';
import Chessground from 'react-chessground';
import 'react-chessground/dist/styles/chessground.css';
import { Chess, type Square } from "chess.js";
import './App.css';

const BASE_URL = import.meta.env.BASE_URL || '/';

interface MoveEvent {
  type: 'move';
  move_number: number;
  player: 'white' | 'black';
  move_san: string;
  fen: string;
  thoughts: string;
  reasoning_tokens: number | null;
  is_terminal: boolean;
}

// interface ThinkingEvent {
//   type: 'thinking';
//   player: 'white' | 'black';
//   move_number: number;
// }

interface GameCompleteEvent {
  type: 'game_complete';
  game_id: string;
  result: string;
  pgn: string;
  total_moves: number;
}

interface PlayerConfig {
  provider: 'databricks' | 'openai' | 'gemini' | 'anthropic' | 'together' | 'xai';
  model_name: string;
  temperature?: number;
  max_tokens?: number;
}

interface EndpointInfo {
  name: string;
  provider?: string;
  model?: string;
  base_url?: string;
}

const ArenaPage = () => {
  const [chess] = useState(() => new Chess());
  const chessRef = useRef(chess);

  const [currentFen, setCurrentFen] = useState(chessRef.current.fen());
  const [lastMoveHighlight, setLastMoveHighlight] = useState<[Square, Square] | undefined>(undefined);
  const [gameStatus, setGameStatus] = useState<'config' | 'playing' | 'complete'>('config');
  const [gameResult, setGameResult] = useState('');

  // Available endpoints
  const [availableEndpoints, setAvailableEndpoints] = useState<EndpointInfo[]>([]);
  const [loadingEndpoints, setLoadingEndpoints] = useState(true);

  // Player configurations
  const [whitePlayer, setWhitePlayer] = useState<PlayerConfig>({
    provider: 'databricks',
    model_name: '',
    temperature: 0.2,
    max_tokens: 1024,
  });
  const [blackPlayer, setBlackPlayer] = useState<PlayerConfig>({
    provider: 'databricks',
    model_name: '',
    temperature: 0.2,
    max_tokens: 1024,
  });
  const [numMoves, setNumMoves] = useState(20);
  const [logThoughts, setLogThoughts] = useState(true);

  // Game state
  const [moves, setMoves] = useState<MoveEvent[]>([]);
  const [currentThinking, setCurrentThinking] = useState<string | null>(null);
  const [whiteThoughts, setWhiteThoughts] = useState<string>('');
  const [blackThoughts, setBlackThoughts] = useState<string>('');
  const [isStreaming, setIsStreaming] = useState(false);

  // API base URL - update this to your Game Arena backend URL
  const GAME_ARENA_API = 'http://localhost:8000'; // Change to your actual backend URL

  const eventSourceRef = useRef<EventSource | null>(null);

  // Fetch available endpoints on mount
  useEffect(() => {
    fetch(`${GAME_ARENA_API}/routes`)
      .then(res => res.json())
      .then(data => {
        const endpoints = data.routes || [];
        setAvailableEndpoints(endpoints);

        // Set first endpoint as default for both players
        if (endpoints.length > 0) {
          setWhitePlayer(prev => ({ ...prev, model_name: endpoints[0].name }));
          setBlackPlayer(prev => ({ ...prev, model_name: endpoints[0].name }));
        }
      })
      .catch(err => {
        console.error('Failed to fetch endpoints:', err);
      })
      .finally(() => {
        setLoadingEndpoints(false);
      });
  }, [GAME_ARENA_API]);

  const startGame = useCallback(() => {
    // Reset game state
    chessRef.current.reset();
    setCurrentFen(chessRef.current.fen());
    setMoves([]);
    setWhiteThoughts('');
    setBlackThoughts('');
    setCurrentThinking(null);
    setGameStatus('playing');
    setIsStreaming(true);
    setLastMoveHighlight(undefined);

    // Prepare request payload
    const payload = {
      num_moves: numMoves,
      player_white: whitePlayer,
      player_black: blackPlayer,
      log_thoughts: logThoughts,
    };

    // Create EventSource for SSE streaming
    const url = `${GAME_ARENA_API}/game/chess/start_streaming`;

    // Use fetch with streaming
    fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    })
      .then(response => {
        if (!response.body) {
          throw new Error('No response body');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        const processStream = () => {
          reader.read().then(({ done, value }) => {
            if (done) {
              setIsStreaming(false);
              return;
            }

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n\n');
            buffer = lines.pop() || '';

            lines.forEach(line => {
              if (line.startsWith('data: ')) {
                const jsonStr = line.slice(6);
                try {
                  const event = JSON.parse(jsonStr);
                  handleStreamEvent(event);
                } catch (e) {
                  console.error('Failed to parse SSE event:', e);
                }
              }
            });

            processStream();
          }).catch(err => {
            console.error('Stream reading error:', err);
            setIsStreaming(false);
          });
        };

        processStream();
      })
      .catch(error => {
        console.error('Failed to start game:', error);
        setGameStatus('config');
        setIsStreaming(false);
        alert('Failed to start game. Make sure the Game Arena backend is running.');
      });
  }, [whitePlayer, blackPlayer, numMoves, logThoughts, GAME_ARENA_API]);

  const handleStreamEvent = useCallback((event: any) => {
    switch (event.type) {
      case 'game_started':
        console.log('Game started:', event.game_id);
        break;

      case 'thinking':
        setCurrentThinking(`${event.player} is thinking... (Move ${event.move_number})`);
        break;

      case 'move':
        const moveEvent = event as MoveEvent;
        console.log('Move received:', moveEvent);

        // Update chess instance with new FEN
        chessRef.current.load(moveEvent.fen);
        setCurrentFen(moveEvent.fen);

        // Parse move to get from/to squares for highlighting
        const history = chessRef.current.history({ verbose: true });
        const lastMove = history[history.length - 1];
        if (lastMove) {
          setLastMoveHighlight([lastMove.from, lastMove.to]);
        }

        // Store move and thoughts
        setMoves(prev => [...prev, moveEvent]);

        if (moveEvent.player === 'white') {
          setWhiteThoughts(moveEvent.thoughts);
        } else {
          setBlackThoughts(moveEvent.thoughts);
        }

        setCurrentThinking(null);
        break;

      case 'game_complete':
        const completeEvent = event as GameCompleteEvent;
        console.log('Game complete:', completeEvent);
        setGameResult(completeEvent.result);
        setGameStatus('complete');
        setIsStreaming(false);
        setCurrentThinking(null);
        break;

      case 'error':
        console.error('Game error:', event.message);
        setCurrentThinking(null);
        setIsStreaming(false);
        break;
    }
  }, []);

  const newGame = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    setGameStatus('config');
    setGameResult('');
    chessRef.current.reset();
    setCurrentFen(chessRef.current.fen());
  };

  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  return (
    <div className="min-h-screen p-2 sm:p-3 bg-slate-800 text-slate-100 bg-[url('/img/chess-pieces.jpg')] bg-cover bg-center bg-blend-multiply">
      <div className="w-full max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold text-center mb-6 text-white">LLM vs LLM Arena</h1>

        {gameStatus === 'config' && (
          <div className="bg-slate-700/90 backdrop-blur-sm p-6 rounded-lg shadow-xl border border-slate-600 max-w-4xl mx-auto">
            <h2 className="text-2xl font-semibold mb-4">Configure Game</h2>

            <div className="mb-6">
              <label className="block text-sm font-medium mb-2">Number of Moves</label>
              <input
                type="number"
                value={numMoves}
                onChange={(e) => setNumMoves(parseInt(e.target.value))}
                min="1"
                max="100"
                className="w-full bg-slate-600 border border-slate-500 rounded px-3 py-2"
              />
            </div>

            <div className="mb-6">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={logThoughts}
                  onChange={(e) => setLogThoughts(e.target.checked)}
                  className="w-4 h-4"
                />
                <span>Log full chain-of-thought (may include extended reasoning)</span>
              </label>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* White Player Config */}
              <div className="bg-slate-600/50 p-4 rounded-lg">
                <h3 className="text-xl font-semibold mb-3">White Player</h3>
                <div className="space-y-3">
                  <div>
                    <label className="block text-sm mb-1">Provider</label>
                    <select
                      value={whitePlayer.provider}
                      onChange={(e) => setWhitePlayer({ ...whitePlayer, provider: e.target.value as any })}
                      className="w-full bg-slate-700 border border-slate-500 rounded px-3 py-2"
                    >
                      <option value="databricks">Databricks</option>
                      <option value="openai">OpenAI</option>
                      <option value="gemini">Gemini</option>
                      <option value="anthropic">Anthropic</option>
                      <option value="together">Together</option>
                      <option value="xai">xAI</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm mb-1">
                      Model / Endpoint
                      {loadingEndpoints && <span className="text-xs ml-2">(loading...)</span>}
                    </label>
                    {whitePlayer.provider === 'databricks' && availableEndpoints.length > 0 ? (
                      <select
                        value={whitePlayer.model_name}
                        onChange={(e) => setWhitePlayer({ ...whitePlayer, model_name: e.target.value })}
                        className="w-full bg-slate-700 border border-slate-500 rounded px-3 py-2"
                      >
                        {availableEndpoints.map(endpoint => (
                          <option key={endpoint.name} value={endpoint.name}>
                            {endpoint.name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        value={whitePlayer.model_name}
                        onChange={(e) => setWhitePlayer({ ...whitePlayer, model_name: e.target.value })}
                        placeholder="e.g., gpt-4o, claude-3-5-sonnet"
                        className="w-full bg-slate-700 border border-slate-500 rounded px-3 py-2"
                      />
                    )}
                  </div>
                </div>
              </div>

              {/* Black Player Config */}
              <div className="bg-slate-600/50 p-4 rounded-lg">
                <h3 className="text-xl font-semibold mb-3">Black Player</h3>
                <div className="space-y-3">
                  <div>
                    <label className="block text-sm mb-1">Provider</label>
                    <select
                      value={blackPlayer.provider}
                      onChange={(e) => setBlackPlayer({ ...blackPlayer, provider: e.target.value as any })}
                      className="w-full bg-slate-700 border border-slate-500 rounded px-3 py-2"
                    >
                      <option value="databricks">Databricks</option>
                      <option value="openai">OpenAI</option>
                      <option value="gemini">Gemini</option>
                      <option value="anthropic">Anthropic</option>
                      <option value="together">Together</option>
                      <option value="xai">xAI</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm mb-1">
                      Model / Endpoint
                      {loadingEndpoints && <span className="text-xs ml-2">(loading...)</span>}
                    </label>
                    {blackPlayer.provider === 'databricks' && availableEndpoints.length > 0 ? (
                      <select
                        value={blackPlayer.model_name}
                        onChange={(e) => setBlackPlayer({ ...blackPlayer, model_name: e.target.value })}
                        className="w-full bg-slate-700 border border-slate-500 rounded px-3 py-2"
                      >
                        {availableEndpoints.map(endpoint => (
                          <option key={endpoint.name} value={endpoint.name}>
                            {endpoint.name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        value={blackPlayer.model_name}
                        onChange={(e) => setBlackPlayer({ ...blackPlayer, model_name: e.target.value })}
                        placeholder="e.g., gpt-4o, claude-3-5-sonnet"
                        className="w-full bg-slate-700 border border-slate-500 rounded px-3 py-2"
                      />
                    )}
                  </div>
                </div>
              </div>
            </div>

            <button
              onClick={startGame}
              className="w-full mt-6 bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-6 rounded-lg transition-colors"
            >
              Start Arena Match
            </button>
          </div>
        )}

        {(gameStatus === 'playing' || gameStatus === 'complete') && (
          <div className="grid grid-cols-1 lg:grid-cols-[2fr_3fr_2fr] gap-4">
            {/* Left Panel - White Thoughts */}
            <div className="bg-slate-700/70 backdrop-blur-sm p-4 rounded-lg shadow-xl border border-slate-600 max-h-[600px] overflow-y-auto">
              <h2 className="text-xl font-semibold mb-3 flex items-center gap-2">
                <img src={`${BASE_URL}img/chesspieces/wikipedia/wK.png`} alt="White" className="w-8 h-8" />
                White's Thoughts
              </h2>
              <div className="bg-slate-800/50 p-3 rounded text-sm whitespace-pre-wrap">
                {whiteThoughts || 'Waiting for first move...'}
              </div>
            </div>

            {/* Center - Board */}
            <div className="flex flex-col items-center gap-4">
              <div className="w-full max-w-[560px]">
                <Chessground
                  fen={currentFen}
                  viewOnly={true}
                  lastMove={lastMoveHighlight}
                  style={{ margin: '0 auto', border: '4px solid #4A5568', borderRadius: '8px' }}
                />
              </div>

              {currentThinking && (
                <div className="bg-yellow-600/20 border border-yellow-600 text-yellow-100 px-4 py-2 rounded-lg">
                  {currentThinking}
                </div>
              )}

              {isStreaming && (
                <div className="flex items-center gap-2 text-blue-400">
                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-400"></div>
                  <span>Game in progress...</span>
                </div>
              )}

              {gameStatus === 'complete' && (
                <div className="bg-green-600/20 border border-green-600 text-green-100 px-6 py-3 rounded-lg text-center">
                  <div className="text-lg font-semibold">Game Complete!</div>
                  <div className="text-sm mt-1">{gameResult}</div>
                  <button
                    onClick={newGame}
                    className="mt-3 bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 px-4 rounded transition-colors"
                  >
                    New Game
                  </button>
                </div>
              )}
            </div>

            {/* Right Panel - Black Thoughts */}
            <div className="bg-slate-700/70 backdrop-blur-sm p-4 rounded-lg shadow-xl border border-slate-600 max-h-[600px] overflow-y-auto">
              <h2 className="text-xl font-semibold mb-3 flex items-center gap-2">
                <img src={`${BASE_URL}img/chesspieces/wikipedia/bK.png`} alt="Black" className="w-8 h-8" />
                Black's Thoughts
              </h2>
              <div className="bg-slate-800/50 p-3 rounded text-sm whitespace-pre-wrap">
                {blackThoughts || 'Waiting for first move...'}
              </div>
            </div>
          </div>
        )}

        {/* Move History */}
        {moves.length > 0 && (
          <div className="mt-6 bg-slate-700/70 backdrop-blur-sm p-4 rounded-lg shadow-xl border border-slate-600">
            <h2 className="text-xl font-semibold mb-3">Move History</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 text-sm">
              {moves.map((move, idx) => (
                <div key={idx} className="bg-slate-800/50 p-2 rounded">
                  <span className="font-semibold">
                    {move.move_number}. {move.player === 'white' ? '' : '...'}{move.move_san}
                  </span>
                  {move.reasoning_tokens && (
                    <span className="ml-2 text-xs text-slate-400">
                      ({move.reasoning_tokens} reasoning tokens)
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ArenaPage;
