import { useState, useRef, useEffect, useCallback } from 'react';
import Chessground from 'react-chessground';
import 'react-chessground/dist/styles/chessground.css';
import { Chess, KING, type Square, type PieceSymbol } from "chess.js";
import { Swiper, SwiperSlide } from 'swiper/react';
import { Navigation } from 'swiper/modules';
// @ts-ignore
import 'swiper/css';
// @ts-ignore
import 'swiper/css/navigation';
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

interface CommentGroup {
  move: string;
  whiteComment?: string;
  blackComment?: string;
  iconMoveSan?: string;
  iconMoveColor?: 'w' | 'b';
  blackIconMoveSan?: string;
  blackIconMoveColor?: 'b';
}

const getTrueAttackerSquare = (
  chess: Chess,
  kingSq: Square,
  checkingColor: 'w' | 'b',
  movedPieceSq: Square
): Square | null => {
  return movedPieceSq; // Simplified for now
};

const ArenaPage = () => {
  const [chess] = useState(() => new Chess());
  const chessRef = useRef(chess);

  const [currentFen, setCurrentFen] = useState(chessRef.current.fen());
  const [lastMoveHighlight, setLastMoveHighlight] = useState<[Square, Square] | undefined>(undefined);
  const [drawableShapes, setDrawableShapes] = useState<Array<{ orig: Square; dest: Square; brush: string }>>([]);
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
  const [isStreaming, setIsStreaming] = useState(false);
  const [isCommentaryMinimized, setIsCommentaryMinimized] = useState(false);
  const [boardSize, setBoardSize] = useState(() => (typeof window !== 'undefined' && window.innerWidth < 768 ? 320 : 560));
  const [history, setHistory] = useState<string[]>([]);
  const [analysisComments, setAnalysisComments] = useState<CommentGroup[]>([]);
  const movesContainerRef = useRef<HTMLDivElement>(null);
  const swiperRef = useRef<any>(null);

  // API base URL
  const GAME_ARENA_API = 'http://localhost:8000';

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
        setLoadingEndpoints(false);
      })
      .catch(error => {
        console.error('Failed to fetch endpoints:', error);
        setLoadingEndpoints(false);
      });
  }, [GAME_ARENA_API]);

  const startGame = useCallback(() => {
    // Reset game state
    chessRef.current.reset();
    setCurrentFen(chessRef.current.fen());
    setMoves([]);
    setHistory([]);
    setAnalysisComments([]);
    setCurrentThinking(null);
    setGameStatus('playing');
    setIsStreaming(true);
    setLastMoveHighlight(undefined);
    setDrawableShapes([]);

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
        const historyVerbose = chessRef.current.history({ verbose: true });
        const lastMove = historyVerbose[historyVerbose.length - 1];
        if (lastMove) {
          setLastMoveHighlight([lastMove.from, lastMove.to]);

          // Update drawable shapes for check arrows
          const newShapes: Array<{ orig: Square; dest: Square; brush: string }> = [];
          newShapes.push({ orig: lastMove.from, dest: lastMove.to, brush: 'blue' });
          if (chessRef.current.inCheck()) {
            const kingSq = chessRef.current.findPiece({ type: KING, color: chessRef.current.turn() })?.[0];
            if (kingSq) {
              const checkingColor = lastMove.color;
              const movedPieceToSq = lastMove.to;
              const attackerSq = getTrueAttackerSquare(chessRef.current, kingSq, checkingColor, movedPieceToSq);
              if (attackerSq) {
                newShapes.push({ orig: attackerSq, dest: kingSq, brush: 'red' });
              }
            }
          }
          setDrawableShapes(newShapes);
        }

        // Store move
        setMoves(prev => [...prev, moveEvent]);
        setHistory(prev => [...prev, ...chessRef.current.history({verbose: false}).slice(prev.length)]);

        // Update analysis comments
        const plyOfMove = chessRef.current.history().length;
        const groupIndex = Math.floor((plyOfMove - 1) / 2);
        const moveNumber = Math.floor((plyOfMove - 1) / 2) + 1;

        setAnalysisComments(prevComments => {
          const newComments = [...prevComments];
          const existingGroup = newComments[groupIndex];
          let updatedGroup: CommentGroup;

          if (moveEvent.player === 'white') {
            const whiteMoveDisplay = `${moveNumber}. ${moveEvent.move_san || ""}`;
            updatedGroup = {
              ...(existingGroup || {}),
              move: whiteMoveDisplay,
              whiteComment: moveEvent.thoughts,
              blackComment: existingGroup ? existingGroup.blackComment : undefined,
              iconMoveSan: moveEvent.move_san,
              iconMoveColor: 'w'
            };
            if (!existingGroup) updatedGroup.blackComment = undefined;
          } else {
            const blackMoveDisplay = `${moveEvent.move_san || ""}`;
            if (existingGroup) {
              const whitePart = existingGroup.move.split(' ... ')[0];
              updatedGroup = {
                ...existingGroup,
                move: `${whitePart} ... ${blackMoveDisplay}`,
                blackComment: moveEvent.thoughts,
                blackIconMoveSan: moveEvent.move_san,
                blackIconMoveColor: 'b'
              };
            } else {
              updatedGroup = {
                move: `${moveNumber}... ${blackMoveDisplay}`,
                whiteComment: undefined,
                blackComment: moveEvent.thoughts,
                blackIconMoveSan: moveEvent.move_san,
                blackIconMoveColor: 'b'
              };
            }
          }
          newComments[groupIndex] = updatedGroup;
          return newComments;
        });

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
    setMoves([]);
    setHistory([]);
    setAnalysisComments([]);
  };

  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  useEffect(() => {
    if (movesContainerRef.current) {
      movesContainerRef.current.scrollTop = movesContainerRef.current.scrollHeight;
    }
  }, [history]);

  useEffect(() => {
    if (swiperRef.current && analysisComments.length > 0) {
      swiperRef.current.swiper?.slideTo(analysisComments.length - 1, 300);
    }
  }, [analysisComments]);

  useEffect(() => {
    const resizeBoard = () => {
      const isMobile = window.innerWidth < 768;
      const newBoardSize = isMobile ? Math.min(window.innerWidth - 40, 320) : 560;
      setBoardSize(newBoardSize);
    };
    resizeBoard();
    window.addEventListener("resize", resizeBoard);
    return () => window.removeEventListener("resize", resizeBoard);
  }, []);

  const renderPieceIcon = (moveSan: string, isWhitePlayerMove: boolean) => {
    if (!moveSan) return null;
    let pieceType: PieceSymbol = 'p';
    if (moveSan.startsWith('K')) pieceType = 'k';
    else if (moveSan.startsWith('Q')) pieceType = 'q';
    else if (moveSan.startsWith('R')) pieceType = 'r';
    else if (moveSan.startsWith('B')) pieceType = 'b';
    else if (moveSan.startsWith('N')) pieceType = 'n';
    else if (moveSan.startsWith('O-O')) pieceType = 'k';
    const pieceColor = isWhitePlayerMove ? 'w' : 'b';
    return <img src={`${BASE_URL}img/chesspieces/wikipedia/${pieceColor}${pieceType.toUpperCase()}.png`} alt={`${pieceColor}${pieceType}`} className="w-4 h-4 inline mr-1" />;
  };

  return (
    <>
      {/* Configuration Modal */}
      {gameStatus === 'config' && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-slate-700/90 text-white rounded-xl shadow-2xl p-6 sm:p-8 max-w-4xl w-full border border-slate-600">
            <h2 className="text-2xl sm:text-3xl font-bold mb-6 text-center">Configure LLM vs LLM Arena</h2>

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
                    <label className="block text-sm mb-1">Endpoint</label>
                    {loadingEndpoints ? (
                      <div className="text-sm text-slate-400">Loading endpoints...</div>
                    ) : (
                      <select
                        value={whitePlayer.model_name}
                        onChange={(e) => setWhitePlayer({ ...whitePlayer, model_name: e.target.value })}
                        className="w-full bg-slate-700 border border-slate-500 rounded px-3 py-2"
                      >
                        {availableEndpoints.map(endpoint => (
                          <option key={endpoint.name} value={endpoint.name}>
                            {endpoint.name}
                            {endpoint.provider && ` (${endpoint.provider})`}
                          </option>
                        ))}
                      </select>
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
                    <label className="block text-sm mb-1">Endpoint</label>
                    {loadingEndpoints ? (
                      <div className="text-sm text-slate-400">Loading endpoints...</div>
                    ) : (
                      <select
                        value={blackPlayer.model_name}
                        onChange={(e) => setBlackPlayer({ ...blackPlayer, model_name: e.target.value })}
                        className="w-full bg-slate-700 border border-slate-500 rounded px-3 py-2"
                      >
                        {availableEndpoints.map(endpoint => (
                          <option key={endpoint.name} value={endpoint.name}>
                            {endpoint.name}
                            {endpoint.provider && ` (${endpoint.provider})`}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                </div>
              </div>
            </div>

            <button
              onClick={startGame}
              disabled={loadingEndpoints || !whitePlayer.model_name || !blackPlayer.model_name}
              className="w-full mt-6 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-600 disabled:cursor-not-allowed text-white font-bold py-3 px-6 rounded-lg transition-colors"
            >
              Start Arena Match
            </button>
          </div>
        </div>
      )}

      {/* Game Complete Modal */}
      {gameStatus === 'complete' && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-slate-700/90 text-white rounded-xl shadow-2xl p-4 sm:p-6 max-w-2xl w-full text-center border border-slate-600">
            <h2 className="text-2xl sm:text-3xl font-bold mb-3">Game Complete!</h2>
            <Chessground
              fen={currentFen}
              viewOnly={true}
              lastMove={lastMoveHighlight}
              style={{ margin: '0 auto 16px auto', border: '2px solid #4A5568', borderRadius: '4px' }}
              drawable={{ enabled: true, autoShapes: drawableShapes }}
            />
            <p className="text-lg sm:text-xl mb-6">{gameResult}</p>
            <div className="flex gap-4 justify-center">
              <button onClick={newGame} className="px-5 py-2.5 sm:px-6 sm:py-3 bg-blue-600 text-white rounded-lg cursor-pointer hover:bg-blue-700 transition-colors text-base sm:text-lg font-semibold shadow-md hover:shadow-lg">New Game</button>
            </div>
          </div>
        </div>
      )}

      {/* Main Game View */}
      {gameStatus === 'playing' && (
        <div className="min-h-screen p-2 sm:p-3 bg-slate-800 text-slate-100 bg-[url('/img/chess-pieces.jpg')] bg-cover bg-center bg-blend-multiply selection:bg-emerald-500 selection:text-white">
          <div className="w-full max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-[1fr_auto_1fr] xl:grid-cols-[2fr_3fr_2fr] gap-3 sm:gap-4 items-start">
            {/* Left Panel - Game Moves */}
            <div className="order-2 lg:order-1 lg:col-span-1 space-y-3 sm:space-y-4 bg-slate-700/70 backdrop-blur-sm p-3 sm:p-4 rounded-lg shadow-xl border border-slate-600 max-h-[calc(100vh-100px)] overflow-y-auto">
              <div className="p-2 bg-slate-600/50 rounded-lg">
                <h2 className="text-lg sm:text-xl text-center font-semibold text-slate-100 mb-2">Game Moves</h2>
                <div ref={movesContainerRef} className="moves-table max-h-96 overflow-y-auto bg-slate-800/50 p-2 rounded scrollbar-thin scrollbar-thumb-slate-500 scrollbar-track-slate-700">
                  {history.length === 0 && <p className="text-center text-slate-400 italic">No moves yet.</p>}
                  {Array.from({ length: Math.ceil(history.length / 2) }).map((_, i) => {
                    const whiteMove = history[i * 2];
                    const blackMove = history[i * 2 + 1];
                    return (
                      <div key={i} className="flex items-center gap-2 py-1 text-sm border-b border-slate-700 last:border-b-0">
                        <div className="text-slate-400 font-medium w-6 text-right">{i + 1}.</div>
                        <div className="flex-1 p-1 bg-slate-200/10 rounded min-w-[60px] text-center">
                          {whiteMove && <span className="text-slate-50">{renderPieceIcon(whiteMove, true)}{whiteMove}</span>}
                        </div>
                        <div className="flex-1 p-1 bg-slate-900/20 rounded min-w-[60px] text-center">
                          {blackMove && <span className="text-slate-50">{renderPieceIcon(blackMove, false)}{blackMove}</span>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* Center - Board */}
            <div className="order-1 lg:order-2 lg:col-span-1 flex justify-center items-start relative">
              <div style={{ width: `${boardSize}px`, height: `${boardSize}px` }} className="shadow-2xl rounded-md overflow-hidden border-4 border-slate-600">
                <Chessground
                  width={boardSize}
                  height={boardSize}
                  fen={currentFen}
                  viewOnly={true}
                  lastMove={lastMoveHighlight}
                  drawable={{
                    enabled: true,
                    autoShapes: drawableShapes,
                  }}
                />
              </div>
            </div>

            {/* Right Panel - Player Info */}
            <div className="order-3 lg:order-3 lg:col-span-1 space-y-3 sm:space-y-4 bg-slate-700/70 backdrop-blur-sm p-3 sm:p-4 rounded-lg shadow-xl border border-slate-600 max-h-[calc(100vh-100px)] overflow-y-auto">
              <div className="space-y-2 p-2 bg-slate-600/50 rounded-lg">
                <div className="flex items-center justify-between gap-2 p-1 rounded bg-slate-900/30">
                  <div className="flex flex-col gap-1">
                    <div className="flex items-center gap-1">
                      <img src={`${BASE_URL}img/chesspieces/wikipedia/bK.png`} alt="Black King" className="size-7 sm:size-8 object-contain p-0.5 bg-black/30 rounded-full"/>
                      <span className="font-semibold text-sm sm:text-base">{blackPlayer.provider.charAt(0).toUpperCase() + blackPlayer.provider.slice(1)} (Black)</span>
                    </div>
                    <div className="text-xs text-slate-300 ml-8">{blackPlayer.model_name}</div>
                  </div>
                  <div className="bg-black text-white rounded-lg w-10 h-10 flex items-center justify-center text-sm">
                    <span className="font-mono">0</span>
                  </div>
                </div>
                <div className="flex items-center justify-between gap-2 p-1 rounded bg-slate-200/20 mt-2">
                  <div className="flex flex-col gap-1">
                    <div className="flex items-center gap-1">
                      <img src={`${BASE_URL}img/chesspieces/wikipedia/wK.png`} alt="White King" className="size-7 sm:size-8 object-contain p-0.5 bg-white/30 rounded-full"/>
                      <span className="font-semibold text-sm sm:text-base">{whitePlayer.provider.charAt(0).toUpperCase() + whitePlayer.provider.slice(1)} (White)</span>
                    </div>
                    <div className="text-xs text-slate-300 ml-8">{whitePlayer.model_name}</div>
                  </div>
                  <div className="bg-white text-black rounded-lg w-10 h-10 flex items-center justify-center text-sm">
                    <span className="font-mono">0</span>
                  </div>
                </div>
              </div>

              <div className="flex flex-col items-center space-y-2 p-3 bg-slate-600/50 rounded-lg">
                <div className="text-lg sm:text-xl font-bold text-orange-400">
                  {currentThinking || "Arena Match"}
                </div>
                <img src={`${BASE_URL}img/chesspieces/wikipedia/${chessRef.current.turn() === "w" ? "wK" : "bK"}.png`} alt="Active King" className="size-16 sm:size-20 object-contain my-1" />
                {isStreaming && (
                  <div className="flex items-center gap-2 text-blue-400">
                    <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-400"></div>
                    <span className="text-sm">Game in progress...</span>
                  </div>
                )}
              </div>

              <div className="flex flex-col space-y-2 sm:space-y-3 p-2 bg-slate-600/50 rounded-lg">
                <button onClick={newGame} className="bg-blue-600 text-base sm:text-lg cursor-pointer hover:bg-blue-700 text-white py-2.5 sm:py-3 px-4 rounded-lg shadow-md hover:shadow-lg transition-all font-medium">New Game</button>
              </div>
            </div>
          </div>

          {/* Move Commentary */}
          {analysisComments.length > 0 && (
            <div className="order-4 lg:order-4 lg:col-span-5 p-4 bg-slate-700/80 backdrop-blur-sm rounded-lg shadow-xl mt-4 w-full md:w-3/4 mx-auto border border-slate-600">
              <h2 className="text-xl font-semibold mb-3 text-center text-slate-100 flex items-center justify-center">
                Move Commentary
                <button onClick={() => setIsCommentaryMinimized(!isCommentaryMinimized)} className="ml-3 p-1 rounded-full hover:bg-slate-600 transition-colors" aria-label={isCommentaryMinimized ? "Expand commentary" : "Minimize commentary"}>
                  {isCommentaryMinimized ? <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-chevron-down size-5"><path d="m6 9 6 6 6-6"/></svg> : <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-chevron-up size-5"><path d="m18 15-6-6-6 6"/></svg>}
                </button>
              </h2>
              <Swiper ref={swiperRef} modules={[Navigation]} spaceBetween={30} slidesPerView={1} navigation autoHeight={true} style={{ "--swiper-navigation-color": "#E2E8F0", "--swiper-pagination-color": "#E2E8F0" } as React.CSSProperties} className="analysis-carousel bg-slate-800/50 rounded">
                {!isCommentaryMinimized && analysisComments.map((commentGroup, i) => (
                  <SwiperSlide key={i} className="p-4">
                    <div className="bg-slate-700/50 p-4 rounded-lg">
                      <h3 className="text-lg font-semibold mb-3 text-center">
                        {commentGroup.iconMoveSan && renderPieceIcon(commentGroup.iconMoveSan, true)}
                        {commentGroup.blackIconMoveSan && renderPieceIcon(commentGroup.blackIconMoveSan, false)}
                        {commentGroup.move}
                      </h3>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {commentGroup.whiteComment && (
                          <div className="bg-slate-800/50 p-3 rounded">
                            <div className="flex items-center gap-2 mb-2">
                              <img src={`${BASE_URL}img/chesspieces/wikipedia/wK.png`} alt="White" className="w-5 h-5" />
                              <span className="font-semibold">White's Comment:</span>
                            </div>
                            <p className="text-sm text-slate-200 whitespace-pre-wrap">{commentGroup.whiteComment}</p>
                          </div>
                        )}
                        {commentGroup.blackComment && (
                          <div className="bg-slate-800/50 p-3 rounded">
                            <div className="flex items-center gap-2 mb-2">
                              <img src={`${BASE_URL}img/chesspieces/wikipedia/bK.png`} alt="Black" className="w-5 h-5" />
                              <span className="font-semibold">Black's Comment:</span>
                            </div>
                            <p className="text-sm text-slate-200 whitespace-pre-wrap">{commentGroup.blackComment}</p>
                          </div>
                        )}
                      </div>
                    </div>
                  </SwiperSlide>
                ))}
              </Swiper>
              {isCommentaryMinimized && <p className="text-center text-slate-400 italic py-4">Commentary minimized.</p>}
            </div>
          )}
        </div>
      )}
    </>
  );
};

export default ArenaPage;
