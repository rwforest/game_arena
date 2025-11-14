import { useEffect, useRef, useState, useCallback } from "react";
import { Chess, Move, KING, type PieceSymbol } from "chess.js";
// import "jquery/dist/jquery.js"; // Ensure jQuery is available if chessboard.js requires it globally
import "@chrisoakman/chessboardjs/dist/chessboard-1.0.0.min.css";
import "@chrisoakman/chessboardjs/dist/chessboard-1.0.0.js";
import "./App.css"; // Assuming this contains necessary custom styles
// import { isPromotionMove, showPromotionDialog } from "./promotionUtils"; // Not typically needed for PGN viewer
import EvaluationBar from './eb'; 
// import { useLocation } from 'react-router-dom'; // No longer needed for initial PGN

interface PGNViewerPageProps {
  pgnString: string;
}

// Type for the ChessboardArrow global object
type ChessboardArrowType = {
  new (boardEl: HTMLDivElement | null): ChessboardArrowType; // Constructor signature
  drawArrow: (
    from: string,
    to: string,
    options?: { clear?: boolean; fillColor?: string }
  ) => void;
  clear: () => void;
};

// Declare Chessboard and ChessboardArrow as globals to satisfy TypeScript
declare const Chessboard: any; 
declare const ChessboardArrow: ChessboardArrowType | undefined;

interface PgnMoveWithComments extends Move {
  comments?: string[];
  // Add other properties from the verbose move object if they are needed elsewhere
  // isCapture: boolean;
  // isPromotion: boolean;
  // isEnPassant: boolean;
  // isKingsideCastle: boolean;
  // isQueensideCastle: boolean;
}

type Theme = {
  white: string;
  black: string;
  displayColor: string;
};

type ThemeName = "default" | "blue" | "green" | "gray" | "orange";

const themes: Record<ThemeName, Theme> = {
  default: { white: "#f0d9b5", black: "#b58863", displayColor: "linear-gradient(135deg, #f0d9b5 50%, #b58863 50%)" },
  blue: { white: "#dee3e6", black: "#8ca2ad", displayColor: "linear-gradient(135deg, #dee3e6 50%, #8ca2ad 50%)" },
  green: { white: "#eeeed2", black: "#769656", displayColor: "linear-gradient(135deg, #eeeed2 50%, #769656 50%)" },
  gray: { white: "#a7a7a7", black: "#888888", displayColor: "linear-gradient(135deg, #a7a7a7 50%, #888888 50%)" },
  orange: { white: "#F8F4E8", black: "#FE5000", displayColor: "linear-gradient(135deg, #F8F4E8 50%, #FE5000 50%)" },
};

const PGNViewerPage = ({ pgnString: initialPgnString }: PGNViewerPageProps) => {
  // const location = useLocation(); // Removed
  // const passedPgnString = location.state && typeof location.state === 'object' && 'pgnString' in location.state ? (location.state as { pgnString: string }).pgnString : ""; // Removed

  const boardRef = useRef<HTMLDivElement>(null);
  const boardArrowRef = useRef<ChessboardArrowType | null>(null);
  const boardInstance = useRef<any>(null); 

  const [chess] = useState(() => new Chess()); // Main chess instance for PGN logic
  const chessRef = useRef(chess); // Ref to the chess instance

  const [pgnString, setPgnString] = useState<string>(initialPgnString || "");
  const [currentMoveIndex, setCurrentMoveIndex] = useState<number>(-1);
  const [history, setHistory] = useState<string[]>([]); // SAN moves
  const [verboseHistory, setVerboseHistory] = useState<PgnMoveWithComments[]>([]); // Verbose move objects
  const [evaluationScore, setEvaluationScore] = useState(0);
  const [isEvaluating, setIsEvaluating] = useState<boolean>(false);

  const [boardSize, setBoardSize] = useState(() => (typeof window !== 'undefined' && window.innerWidth < 768 ? 320 : 560));
  const [currentTheme, setCurrentTheme] = useState<ThemeName>(() => (typeof localStorage === 'undefined' ? 'default' : (localStorage.getItem("chessTheme") as ThemeName || "default")));
  const currentThemeRef = useRef(currentTheme);

  const initialPgnFen = useRef<string | null>(null);

  // Ref for the PGN moves container for auto-scrolling
  const pgnMovesContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => { currentThemeRef.current = currentTheme; }, [currentTheme]);
  useEffect(() => { chessRef.current = chess; }, [chess]); // Keep chessRef synced if chess instance itself changes (though it's const here)

  const applyThemeCallback = useCallback((themeName: ThemeName) => {
    const squares = boardRef.current?.querySelectorAll(".square-55d63"); if (!squares) return;
    const theme = themes[themeName];
    squares.forEach(squareElement => { const square = squareElement as HTMLElement; square.style.backgroundColor = ""; if (themeName !== "default") { if (square.classList.contains("white-1e1d7")) square.style.backgroundColor = theme.white; else if (square.classList.contains("black-3c85d")) square.style.backgroundColor = theme.black; } });
  }, []); 

  const fetchEvaluation = useCallback(async (fen: string): Promise<number | null> => {
    if (isEvaluating) return null;
    setIsEvaluating(true);
    try {
      const res = await fetch(`https://chess-api.com/v1`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fen }) });
      if (!res.ok) { setIsEvaluating(false); return null; }
      const data = await res.json();
      let evaluation = 0;
      if (data.centipawns !== undefined && data.centipawns !== null) { evaluation = parseInt(data.centipawns, 10); if (isNaN(evaluation)) evaluation = (data.eval !== undefined && data.eval !== null && !isNaN(parseFloat(data.eval))) ? Math.round(parseFloat(data.eval) * 100) : 0; } 
      else if (data.eval !== undefined && data.eval !== null) { evaluation = Math.round(parseFloat(data.eval) * 100); if (isNaN(evaluation)) evaluation = 0; }
      if (isNaN(evaluation)) evaluation = 0;
      setEvaluationScore(evaluation);
      setIsEvaluating(false);
      return evaluation;
    } catch (error) { setIsEvaluating(false); return null; }
  }, [isEvaluating]);

  const navigateToMove = useCallback(async (index: number) => {
    let fenToEvaluate: string | null = null;
    let targetIndex = index;

    if (!history.length && !initialPgnFen.current) { // Nothing loaded
        return;
    }
    
    if (!history.length && initialPgnFen.current) { // PGN loaded but no moves (e.g., just a FEN setup)
        targetIndex = -1; // Stay at initial position
        chessRef.current = new Chess(initialPgnFen.current);
        if(boardInstance.current) boardInstance.current.position(initialPgnFen.current, false);
        if (boardArrowRef.current) boardArrowRef.current.clear();
        setCurrentMoveIndex(-1);
        fenToEvaluate = initialPgnFen.current;
    } else if (history.length > 0) { // PGN has moves
        if (index < -1) targetIndex = -1;
        if (index >= history.length) targetIndex = history.length - 1;

        const targetChess = new Chess(initialPgnFen.current || undefined);
        for (let i = 0; i <= targetIndex; i++) {
            if (history[i]) targetChess.move(history[i]); // Apply moves up to targetIndex
        }
        
        chessRef.current = targetChess;
        if(boardInstance.current) boardInstance.current.position(targetChess.fen(), false);
        setCurrentMoveIndex(targetIndex);
        fenToEvaluate = targetChess.fen();

        if (boardArrowRef.current) {
            boardArrowRef.current.clear();
            if (targetIndex >= 0 && verboseHistory[targetIndex]) {
                const move = verboseHistory[targetIndex];
                boardArrowRef.current.drawArrow(move.from, move.to);
                if (targetChess.inCheck()) {
                    const kingSq = targetChess.findPiece({ type: KING, color: targetChess.turn() })?.[0];
                    if (kingSq) boardArrowRef.current.drawArrow(move.to, kingSq, { clear: false, fillColor: "rgba(240, 0, 0, .6)"});
                }
            }
        }
    } else { // Should be covered by first condition, but as a fallback
         if (initialPgnFen.current) {
            chessRef.current = new Chess(initialPgnFen.current);
            if(boardInstance.current) boardInstance.current.position(initialPgnFen.current, false);
            setCurrentMoveIndex(-1);
            fenToEvaluate = initialPgnFen.current;
        } else {
            return;
        }
    }
    
    if (fenToEvaluate) {
      await fetchEvaluation(fenToEvaluate);
    }

    // Auto-scroll logic
    setTimeout(() => {
        if (pgnMovesContainerRef.current) {
            if (targetIndex === -1) {
                pgnMovesContainerRef.current.scrollTop = 0;
            } else {
                // Find the specific move element (either white or black's part of the row)
                const activeMoveElement = pgnMovesContainerRef.current.querySelector(`[data-move-idx="${targetIndex}"]`);
                if (activeMoveElement) {
                    activeMoveElement.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            }
        }
    }, 0); // setTimeout to allow DOM updates
  }, [history, verboseHistory, initialPgnFen, fetchEvaluation]);

  const loadPgn = useCallback(() => {
    try {
      const tempChess = new Chess();
      // Load PGN with comments, chess.js will store them in `move.comment` if they are standard PGN comments
      // However, our custom format {Speaker: Text} needs manual parsing after this initial load.
      tempChess.loadPgn(pgnString);

      if (tempChess.history().length > 0 || pgnString.trim() !== "") {
        const headers = tempChess.header();
        initialPgnFen.current = headers.FEN || new Chess().fen();

        const sanMovesFromChessJs = tempChess.history();
        
        // Create base verbose moves from a clean replay
        const replayChess = new Chess(initialPgnFen.current);
        const populatedMoves: PgnMoveWithComments[] = sanMovesFromChessJs.map(san => {
          const move = replayChess.move(san);
          if (!move) throw new Error(`Failed to replay SAN move: ${san}`); // Should not happen
          return { ...move, comments: [] } as unknown as PgnMoveWithComments; // Initialize with empty comments, assert type via unknown
        });

        // Comment Extraction and Association
        let movetext = pgnString;
        // Strip headers from the PGN string to get only the movetext
        const headerEndMatch = movetext.match(/]\s*(\n|\r\n|\r)\s*(\n|\r\n|\r)/); // Find end of last header
        if (headerEndMatch && headerEndMatch.index !== undefined) {
            movetext = movetext.substring(headerEndMatch.index + headerEndMatch[0].length);
        } else {
            // Fallback: try to find the start of moves if headers are minimal or absent
            const firstMoveMatch = movetext.match(/\s*1\.\s/);
            if (firstMoveMatch && firstMoveMatch.index !== undefined) {
                movetext = movetext.substring(firstMoveMatch.index);
            }
        }
        movetext = movetext.replace(/\r\n|\r/g, "\n").trim(); // Normalize newlines and trim

        let searchStartIndex = 0;
        const commentRegex = /{([^}]+)}/g;

        for (let i = 0; i < populatedMoves.length; i++) {
          const currentPopulatedMove = populatedMoves[i];
          const moveSan = currentPopulatedMove.san;
          
          // Escape special characters in SAN for regex search, e.g., '+', '#'.
          // More robust SAN finding might be needed for very complex/ambiguous SANs.
          const escapedSan = moveSan.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          
          const sanMatch = movetext.substring(searchStartIndex).match(new RegExp(escapedSan));

          if (sanMatch && sanMatch.index !== undefined) {
            const sanStartIndexInSubstring = sanMatch.index;
            const sanEndIndexInSubstring = sanStartIndexInSubstring + moveSan.length;
            
            // Region for comments is after the current SAN
            let commentSearchStartIndex = searchStartIndex + sanEndIndexInSubstring;
            let commentSearchEndIndex = movetext.length;

            // Determine end of comment region: start of next move SAN or next move number
            if (i + 1 < populatedMoves.length) {
              const nextMoveSan = populatedMoves[i+1].san;
              const escapedNextSan = nextMoveSan.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
              const nextSanMatch = movetext.substring(commentSearchStartIndex).match(new RegExp(escapedNextSan));
              if (nextSanMatch && nextSanMatch.index !== undefined) {
                commentSearchEndIndex = commentSearchStartIndex + nextSanMatch.index;
              }
            }
            
            // Also consider next move number as a delimiter
            const nextMoveNumberRegex = /\s*\d+\.(?:\.\.)?\s/;
            const nextMoveNumberMatch = movetext.substring(commentSearchStartIndex).match(nextMoveNumberRegex);
            if (nextMoveNumberMatch && nextMoveNumberMatch.index !== undefined) {
                 if ((commentSearchStartIndex + nextMoveNumberMatch.index) < commentSearchEndIndex) {
                    commentSearchEndIndex = commentSearchStartIndex + nextMoveNumberMatch.index;
                 }
            }
            
            const commentRegion = movetext.substring(commentSearchStartIndex, commentSearchEndIndex);
            let match;
            while ((match = commentRegex.exec(commentRegion)) !== null) {
              currentPopulatedMove.comments?.push(match[0]); // Store the full comment string e.g. "{White: Text}"
            }
            searchStartIndex = commentSearchStartIndex; // Update searchStartIndex for the next SAN search
          } else {
            // This might happen if SAN notation in PGN string differs subtly from chess.js output
            // or if PGN is malformed. For now, we log and skip comments for this move.
            console.warn(`SAN move "${moveSan}" not found in PGN string starting from index ${searchStartIndex}. Comments might be missed for this move.`);
            // Attempt to find the next move number to resync, or just advance past where we expected the SAN.
             const nextMoveNumPattern = movetext.substring(searchStartIndex).match(/\s*\d+\.(?:\.\.)?\s*/);
             if(nextMoveNumPattern && nextMoveNumPattern.index !== undefined){
                searchStartIndex += nextMoveNumPattern.index + nextMoveNumPattern[0].length;
             } else {
                searchStartIndex += moveSan.length + 1; // Heuristic advance
             }
          }
        }

        setVerboseHistory(populatedMoves);
        setHistory(populatedMoves.map(m => m.san));
        setCurrentMoveIndex(-1);
        
        chessRef.current = new Chess(initialPgnFen.current || undefined);
        if (boardInstance.current) boardInstance.current.position(initialPgnFen.current || chessRef.current.fen(), false);
        if (boardArrowRef.current) boardArrowRef.current.clear();
        
        setEvaluationScore(0);
        navigateToMove(-1);
      } else {
        alert("Error loading PGN or PGN is empty. Please check the PGN format.");
      }
    } catch (error) {
      console.error("Error parsing PGN:", error);
      alert("An unexpected error occurred while loading the PGN.");
    }
  }, [pgnString, navigateToMove]);

  useEffect(() => {
    if (!boardRef.current || boardInstance.current) return;
    const newBoard = Chessboard(boardRef.current, {
      draggable: false, 
      position: chessRef.current.fen(), 
      snapbackSpeed: 100, snapSpeed: 50, appearSpeed: 100, moveSpeed: 100 
    });
    boardInstance.current = newBoard;
    if (typeof ChessboardArrow !== 'undefined' && ChessboardArrow) boardArrowRef.current = new ChessboardArrow(boardRef.current);
    else console.warn("ChessboardArrow library is not loaded.");
    applyThemeCallback(currentThemeRef.current);

    // Log DOM structure for inspection
    if (boardRef.current) {
      console.log("PGNViewerPage: boardRef is current.");
      const chessboardEl = boardRef.current.querySelector('[class*="chessboard-"]');
      console.log(chessboardEl ? "PGNViewerPage: Chessboard element found." : "PGNViewerPage: Chessboard element NOT found.");
      const canvasEl = boardRef.current.querySelector('canvas');
      console.log(canvasEl ? "PGNViewerPage: Canvas element found." : "PGNViewerPage: Canvas element NOT found.");
      if (chessboardEl) {
        console.log("PGNViewerPage: Chessboard element class list:", chessboardEl.classList.toString());
      }
      if (canvasEl) {
        console.log("PGNViewerPage: Canvas parent element tagName:", canvasEl.parentElement?.tagName);
        console.log("PGNViewerPage: Canvas parent element id:", canvasEl.parentElement?.id);
      }
    } else {
      console.log("PGNViewerPage: boardRef is NOT current at logging point.");
    }
    
    // Load PGN if passed via prop on initial mount
    if (initialPgnString) {
        loadPgn(); // This will also call navigateToMove(-1)
    } else {
        navigateToMove(-1); // Ensure initial position is set even without passed PGN
    }

    return () => { if (boardRef.current) boardRef.current.innerHTML = ''; boardInstance.current = null; boardArrowRef.current = null; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applyThemeCallback, initialPgnString]); // loadPgn and navigateToMove are stable due to useCallback

  useEffect(() => {
    const resizeBoard = () => {
      if (boardRef.current && boardInstance.current) { const isMobile = window.innerWidth < 768; const newBoardSize = isMobile ? Math.min(window.innerWidth - 40, 320) : 560; boardRef.current.style.width = `${newBoardSize}px`; setBoardSize(newBoardSize); boardInstance.current.resize?.(); }};
    resizeBoard(); window.addEventListener("resize", resizeBoard);
    return () => window.removeEventListener("resize", resizeBoard);
  }, [setBoardSize]); 

  useEffect(() => { if (boardInstance.current) applyThemeCallback(currentTheme); }, [currentTheme, applyThemeCallback]);

  // useEffect for keyboard navigation
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const targetElement = event.target as HTMLElement;
      if (targetElement.tagName === 'INPUT' || targetElement.tagName === 'TEXTAREA' || targetElement.isContentEditable) {
        return; // Don't interfere with text input
      }

      if (event.key === "ArrowLeft") {
        event.preventDefault();
        navigateToMove(currentMoveIndex - 1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        navigateToMove(currentMoveIndex + 1);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [currentMoveIndex, navigateToMove]); // navigateToMove is a dependency

  const changeTheme = (themeName: ThemeName): void => { setCurrentTheme(themeName); if (typeof localStorage !== 'undefined') localStorage.setItem("chessTheme", themeName); };
  // const AnimatedTotal = ({ color }: { color: "w" | "b" }) => { const playerWhosePiecesWereCaptured = color; const total = Object.entries(captured[playerWhosePiecesWereCaptured]).reduce((sum, [p, count]) => sum + (pieceValues[p.toUpperCase()] || 0) * count, 0); return (<div className={`relative bg-${color === 'w' ? 'black' : 'white'} text-${color === 'w' ? 'white' : 'black'} rounded-lg w-10 h-10 flex items-center justify-center text-sm`}><span className="font-mono">{total > 0 ? `+${total}`: total}</span></div>); };

  const parsePgnComment = (commentStr: string): { speaker: string; text: string } | null => {
    const speakerTextRegex = /^{([A-Za-z]+):\s*(.*)}$/; // Matches {Speaker: Text}
    const generalCommentRegex = /^{([^:]+)}$/; // Matches {General Text} (no colon)

    const speakerMatch = commentStr.match(speakerTextRegex);
    if (speakerMatch) {
      return { speaker: speakerMatch[1], text: speakerMatch[2] };
    }
    const generalMatch = commentStr.match(generalCommentRegex);
    if (generalMatch) {
      return { speaker: "Note", text: generalMatch[1] };
    }
    return { speaker: "Raw", text: commentStr }; // Fallback for unparsed or malformed
  };

  return (
    <>
      <div className="min-h-screen p-2 sm:p-3 bg-slate-800 text-slate-100 bg-[url('/img/chess-pieces.jpg')] bg-cover bg-center bg-blend-multiply selection:bg-emerald-500 selection:text-white">
        <div className="w-full max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-[1fr_auto_1fr] xl:grid-cols-[2fr_3fr_2fr] gap-3 sm:gap-4 items-start">
          <div className="order-2 lg:order-1 lg:col-span-1 space-y-3 sm:space-y-4 bg-slate-700/70 backdrop-blur-sm p-3 sm:p-4 rounded-lg shadow-xl border border-slate-600 max-h-[calc(100vh-100px)] overflow-y-auto">
            <div className="p-2 bg-slate-600/50 rounded-lg">
              <h3 className="text-lg sm:text-xl font-semibold mb-2 text-center">PGN Input</h3>
              <textarea 
                className="w-full h-32 p-2 text-sm text-slate-900 bg-slate-50 rounded-md border border-slate-300 focus:ring-blue-500 focus:border-blue-500 placeholder-slate-400"
                placeholder="Paste PGN here..."
                value={pgnString}
                onChange={(e) => setPgnString(e.target.value)}
              />
              <button 
                onClick={loadPgn} 
                className="mt-2 w-full bg-blue-600 text-base sm:text-lg cursor-pointer hover:bg-blue-700 text-white py-2 px-4 rounded-lg shadow-md hover:shadow-lg transition-all font-medium"
              >
                Load PGN
              </button>
            </div>
            <div className="p-2 bg-slate-600/50 rounded-lg">
              <h3 className="text-lg sm:text-xl font-semibold mb-2 text-center">Board Theme</h3>
              <div className="flex gap-2 sm:gap-3 justify-center">
                {(Object.keys(themes) as ThemeName[]).map(themeKey => ( <button key={themeKey} onClick={() => changeTheme(themeKey)} className={`size-7 sm:size-8 rounded-full transition-all duration-200 cursor-pointer border-2 ${currentTheme === themeKey ? "ring-4 ring-blue-500 ring-offset-2 ring-offset-slate-700 scale-110 border-blue-400" : "ring-1 ring-slate-500 border-transparent hover:ring-blue-400"}`} style={{ background: themes[themeKey].displayColor }} aria-label={`${themeKey} theme`} /> ))}
              </div>
            </div>
            <div className="p-2 bg-slate-600/50 rounded-lg">
                <h2 className="text-lg sm:text-xl text-center font-semibold text-slate-100 mb-2">Game Moves</h2>
                <div 
                    ref={pgnMovesContainerRef} // Assign ref here
                    className="moves-table max-h-60 overflow-y-auto bg-slate-800/50 p-2 rounded scrollbar-thin scrollbar-thumb-slate-500 scrollbar-track-slate-700"
                >
                {(history.length === 0 && !initialPgnFen.current) && <p className="text-center text-slate-400 italic">Load a PGN to see moves.</p>}
                {(history.length === 0 && initialPgnFen.current) && <p className="text-center text-slate-400 italic">PGN loaded (initial position set). No moves in history.</p>}
                {Array.from({ length: Math.ceil(history.length / 2) }).map((_, i) => {
                    const whiteMoveIndex = i * 2;
                    const blackMoveIndex = i * 2 + 1;
                    const whiteMove = history[whiteMoveIndex]; 
                    const blackMove = history[blackMoveIndex];
                    const renderPieceIcon = (moveSan: string, isWhitePlayerMove: boolean) => { if (!moveSan) return null; let pieceType: PieceSymbol = 'p'; if (moveSan.startsWith('K')) pieceType = 'k'; else if (moveSan.startsWith('Q')) pieceType = 'q'; else if (moveSan.startsWith('R')) pieceType = 'r'; else if (moveSan.startsWith('B')) pieceType = 'b'; else if (moveSan.startsWith('N')) pieceType = 'n'; else if (moveSan.startsWith('O-O')) pieceType = 'k'; const pieceColor = isWhitePlayerMove ? 'w' : 'b'; return <img src={`/img/chesspieces/wikipedia/${pieceColor}${pieceType.toUpperCase()}.png`} alt={`${pieceColor}${pieceType}`} className="w-4 h-4 inline mr-1" />; };
                    return (
                    <div key={i} className="flex items-center gap-2 py-1 text-sm border-b border-slate-700 last:border-b-0">
                        <div className="text-slate-400 font-medium w-6 text-right">{i + 1}.</div>
                        <div 
                            data-move-idx={whiteMoveIndex} // Add data attribute
                            className={`flex-1 p-1 rounded min-w-[60px] text-center cursor-pointer hover:bg-slate-500/30 ${currentMoveIndex === whiteMoveIndex ? 'bg-blue-500/30 ring-1 ring-blue-400' : 'bg-slate-200/10'}`}
                            onClick={() => whiteMove && navigateToMove(whiteMoveIndex)}
                        >
                            {whiteMove && <span className="text-slate-50">{renderPieceIcon(whiteMove, true)}{whiteMove}</span>}
                        </div>
                        <div 
                            data-move-idx={blackMoveIndex} // Add data attribute
                            className={`flex-1 p-1 rounded min-w-[60px] text-center cursor-pointer hover:bg-slate-500/30 ${currentMoveIndex === blackMoveIndex ? 'bg-blue-500/30 ring-1 ring-blue-400' : 'bg-slate-900/20'}`}
                            onClick={() => blackMove && navigateToMove(blackMoveIndex)}
                        >
                            {blackMove && <span className="text-slate-50">{renderPieceIcon(blackMove, false)}{blackMove}</span>}
                        </div>
                    </div>);
                })}</div>
                 <div className="flex justify-center gap-2 mt-3">
                    <button onClick={() => navigateToMove(-1)} disabled={currentMoveIndex <= -1 || (history.length === 0 && !initialPgnFen.current) } className="px-3 py-1.5 bg-slate-600 hover:bg-slate-500 disabled:bg-slate-700 disabled:text-slate-500 rounded-md text-sm font-medium transition-colors">{"|<"}</button>
                    <button onClick={() => navigateToMove(currentMoveIndex - 1)} disabled={currentMoveIndex <= -1 || (history.length === 0 && !initialPgnFen.current)} className="px-3 py-1.5 bg-slate-600 hover:bg-slate-500 disabled:bg-slate-700 disabled:text-slate-500 rounded-md text-sm font-medium transition-colors">{"<"}</button>
                    <button onClick={() => navigateToMove(currentMoveIndex + 1)} disabled={currentMoveIndex >= history.length - 1 || history.length === 0} className="px-3 py-1.5 bg-slate-600 hover:bg-slate-500 disabled:bg-slate-700 disabled:text-slate-500 rounded-md text-sm font-medium transition-colors">{">"}</button>
                    <button onClick={() => navigateToMove(history.length - 1)} disabled={currentMoveIndex >= history.length - 1 || history.length === 0} className="px-3 py-1.5 bg-slate-600 hover:bg-slate-500 disabled:bg-slate-700 disabled:text-slate-500 rounded-md text-sm font-medium transition-colors">{">|"}</button>
                </div>
            </div>
          </div>
          <div className="order-1 lg:order-2 lg:col-span-1 flex justify-center items-start relative">
            <div className="flex items-stretch">
                <div className="ml-1 sm:ml-2 flex items-center"><EvaluationBar evaluation={evaluationScore} height={boardSize} /></div>
                <div ref={boardRef} style={{ width: `${boardSize}px`, height: `${boardSize}px` }} className="shadow-2xl rounded-md overflow-hidden border-4 border-slate-600" />                
            </div>
          </div>

          {/* Right-hand panel for Comments */}
          <div className="order-3 lg:order-3 lg:col-span-1 space-y-3 sm:space-y-4 bg-slate-700/70 backdrop-blur-sm p-3 sm:p-4 rounded-lg shadow-xl border border-slate-600 max-h-[calc(100vh-100px)] overflow-y-auto">
            <div className="p-2 bg-slate-600/50 rounded-lg">
              <h3 className="text-lg sm:text-xl font-semibold mb-3 text-center">Move Comments</h3>
              <div className="space-y-2 text-sm">
                {currentMoveIndex >= 0 && verboseHistory[currentMoveIndex] && verboseHistory[currentMoveIndex].comments && verboseHistory[currentMoveIndex].comments!.length > 0 ? (
                  verboseHistory[currentMoveIndex].comments!.map((commentStr, idx) => {
                    const parsed = parsePgnComment(commentStr);
                    if (parsed) {
                      return (
                        <div key={idx} className="p-2 bg-slate-800/60 rounded">
                          <strong className="text-blue-300">{parsed.speaker}:</strong>
                          <p className="text-slate-200 whitespace-pre-wrap break-words">{parsed.text}</p>
                        </div>
                      );
                    }
                    return ( // Fallback for unparseable comments, though parsePgnComment should handle most.
                        <div key={idx} className="p-2 bg-slate-800/60 rounded">
                            <p className="text-slate-300 italic whitespace-pre-wrap break-words">{commentStr}</p>
                        </div>
                    );
                  })
                ) : (
                  <p className="text-center text-slate-400 italic">
                    {currentMoveIndex === -1 && !(initialPgnFen.current && history.length === 0) ? "Select a move to see comments." : "No comments for this move."}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
};
export default PGNViewerPage;