import { Chess, type Square } from "chess.js";

// Helper function to determine if a specific piece is directly attacking the king
export const isPieceAtSquareDirectlyAttacking = (
  chessInstance: Chess,
  pieceSq: Square,
  targetKingSq: Square,
  attackerColor: "w" | "b"
): boolean => {
  // Create a new temporary Chess instance from the FEN of the original instance
  const tempChess = new Chess(chessInstance.fen());

  // Get the piece at the specified square to ensure it's the correct piece we're isolating.
  const pieceToIsolate = tempChess.get(pieceSq);
  if (!pieceToIsolate || pieceToIsolate.color !== attackerColor) {
    // This piece is not the attacker's color or doesn't exist, so it can't be the direct attacker.
    return false;
  }

  // Iterate through all squares on the board to remove other pieces of the attacker's color.
  const squares: Square[] = [];
  for (let r = 1; r <= 8; r++) {
    for (let c = 0; c < 8; c++) {
      squares.push(`${String.fromCharCode(97 + c)}${r}` as Square);
    }
  }

  for (const s of squares) {
    if (s === pieceSq) {
      // Don't remove the piece we are testing for direct attack.
      continue;
    }
    const piece = tempChess.get(s);
    if (piece && piece.color === attackerColor) {
      tempChess.remove(s);
    }
  }

  // After removing all other pieces of the attackerColor,
  // check if targetKingSq is attacked by any remaining piece of attackerColor.
  // Since we've removed all other pieces of attackerColor, this effectively checks
  // if the piece originally at pieceSq is the one attacking targetKingSq.
  return tempChess.isAttacked(targetKingSq, attackerColor);
};

// Function to find the true source of a check, especially for discovered checks.
export const getTrueAttackerSquare = (
  chessInstance: Chess,
  kingSq: Square,
  checkingColor: "w" | "b", // The color of the side delivering the check
  movedPieceSq: Square // The square the last piece moved TO
): Square | null => {
  // 1. Check if the moved piece is directly attacking the king.
  if (isPieceAtSquareDirectlyAttacking(chessInstance, movedPieceSq, kingSq, checkingColor)) {
    return movedPieceSq;
  } else {
    // 3. Else (it's a discovered check or a complex scenario).
    const discoverCheckInstance = new Chess(chessInstance.fen());
    const pieceThatMoved = discoverCheckInstance.get(movedPieceSq);
    if (pieceThatMoved) {
      // Remove the piece that just moved to isolate other attackers
      discoverCheckInstance.remove(movedPieceSq);
    }

    const files = ["a", "b", "c", "d", "e", "f", "g", "h"];
    const ranks = ["1", "2", "3", "4", "5", "6", "7", "8"];

    for (const file of files) {
      for (const rank of ranks) {
        const s = (file + rank) as Square;
        const potentialAttackerPiece = discoverCheckInstance.get(s);
        if (potentialAttackerPiece && potentialAttackerPiece.color === checkingColor) {
          // Check if this piece is now attacking the king
          if (isPieceAtSquareDirectlyAttacking(discoverCheckInstance, s, kingSq, checkingColor)) {
            return s;
          }
        }
      }
    }
  }
  // Fallback: if no other attacker is found
  // This might indicate an issue or a very unusual position.
  // Returning null is better to indicate uncertainty or that the moved piece wasn't the direct/discovered attacker.
  return null;
};
