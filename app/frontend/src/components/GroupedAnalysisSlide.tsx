import type { IGroupedAnalysisComment } from "../interfaces";
type PieceSymbol = "p" | "n" | "b" | "r" | "q" | "k";

// Copied renderPieceIcon function (ideally this would be in a shared utility)
const renderPieceIcon = (moveSan: string | undefined, moveColor: 'w' | 'b' | undefined) => {
    if (!moveSan || !moveColor) return null;
    let pieceType: PieceSymbol = 'p'; // Default to pawn
    if (moveSan.startsWith('K')) pieceType = 'k';
    else if (moveSan.startsWith('Q')) pieceType = 'q';
    else if (moveSan.startsWith('R')) pieceType = 'r';
    else if (moveSan.startsWith('B')) pieceType = 'b';
    else if (moveSan.startsWith('N')) pieceType = 'n';
    // For castling, chess.js SAN is 'O-O' or 'O-O-O'. The icon is typically the King.
    else if (moveSan.startsWith('O-O')) pieceType = 'k';
    // Add other piece prefixes if necessary (e.g., for different languages if SAN changes)

    // Determine the color character for the image path
    const pieceColorChar = moveColor === 'w' ? 'w' : 'b';
    
    return (
        <img 
            src={`/img/chesspieces/wikipedia/${pieceColorChar}${pieceType.toUpperCase()}.png`} 
            alt={`${pieceColorChar}${pieceType}`} 
            className="w-5 h-5 inline mr-2" // Adjusted size and margin
        />
    );
};


const promptSvg = <svg
    xmlns="http://www.w3.org/2000/svg"
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className="lucide lucide-terminal-icon lucide-terminal size-5"
>
    <path d="M12 19h8" />
    <path d="m4 17 6-6-6-6" />
</svg>;

export default function GroupedAnalysisSlide({ comment }: { comment: IGroupedAnalysisComment }) {
    if (!comment) {
        return null;
    }

    // Destructure new props for the icon
    const { move, whiteComment, blackComment, analystComment, iconMoveSan, iconMoveColor, blackIconMoveSan, blackIconMoveColor } = comment as any; // Cast to any temporarily

    let whiteMoveDisplay = "";
    let blackMoveDisplay = "";
    let moveNumberDisplay = "";

    if (move) {
        const moveParts = move.split(" ... ");
        // Regex to extract move number (e.g., "1.") and the actual move (e.g., "e4")
        const firstPartMatch = moveParts[0].match(/^(\d+\.)\s*(.*)$/);
        if (firstPartMatch) {
            moveNumberDisplay = firstPartMatch[1]; // e.g., "1."
            whiteMoveDisplay = firstPartMatch[2];  // e.g., "e4"
        } else {
            // Fallback if no move number is found (e.g. if move starts with "..." for black)
            // or if it's just a single move without a number (less likely with current setup)
            whiteMoveDisplay = moveParts[0];
        }

        if (moveParts.length > 1) {
            blackMoveDisplay = moveParts[1]; // e.g., "e5"
        }
    }

    return (
        // The main container for a single analysis slide
        <div className="space-y-3">
            {/* Move display - Added text-gray-800 and icon rendering */}
            <h3 className="text-xl font-semibold mb-3 p-3 bg-gray-100 rounded text-center text-gray-800 flex items-center justify-center">
                {moveNumberDisplay && <span className="mr-1">{moveNumberDisplay}</span>}
                {whiteMoveDisplay && (
                    <>{renderPieceIcon(iconMoveSan, iconMoveColor)}<span>{whiteMoveDisplay}</span></>
                )}
                {blackMoveDisplay && (
                    <><span className="mx-2">...</span>{renderPieceIcon(blackIconMoveSan, blackIconMoveColor)}<span>{blackMoveDisplay}</span></>
                )}
                {(!whiteMoveDisplay && !blackMoveDisplay && move) && <span>{move}</span> /* Fallback for unexpected format */}
            </h3>

            {/* Container for White, Black, and Analyst comments, arranged horizontally on medium screens and up */}
            <div className="flex flex-col md:flex-row md:space-x-3 space-y-3 md:space-y-0">
                {/* White's Comment Block */}
                {whiteComment && (
                    <div className="mb-2 p-3 border rounded bg-white flex-1 shadow"> {/* flex-1 allows it to grow */}
                        <strong className="flex items-center gap-2 text-gray-700">
                            <img
                                src={`/img/chesspieces/wikipedia/wK.png`}
                                alt="White King"
                                className="size-5 object-contain"
                            />
                            White's Comment:
                        </strong>
                        <p className="pl-7 pt-1 text-gray-800 whitespace-pre-wrap">{whiteComment}</p> {/* whitespace-pre-wrap to respect newlines */}
                    </div>
                )}

                {/* Black's Comment Block */}
                {blackComment && (
                    <div className="mb-2 p-3 border rounded bg-white flex-1 shadow">
                        <strong className="flex items-center gap-2 text-gray-700">
                            <img
                                src={`/img/chesspieces/wikipedia/bK.png`}
                                alt="Black King"
                                className="size-5 object-contain"
                            />
                            Black's Comment:
                        </strong>
                        <p className="pl-7 pt-1 text-gray-800 whitespace-pre-wrap">{blackComment}</p> {/* whitespace-pre-wrap to respect newlines */}
                    </div>
                )}

                {/* Analyst's Comment Block */}
                {analystComment && (
                     <div className="mb-2 p-3 border rounded bg-white flex-1 shadow">
                        <strong className="flex items-center gap-2 text-gray-700">
                            {promptSvg}
                            Analyst's View:
                        </strong>
                        <p className="pl-7 pt-1 text-gray-800 whitespace-pre-wrap">{analystComment}</p> {/* whitespace-pre-wrap to respect newlines */}
                    </div>
                )}
            </div>
        </div>
    );
}
