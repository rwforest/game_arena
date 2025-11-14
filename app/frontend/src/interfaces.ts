export interface IAnalystComment {
  ply: number;
  comment: string;
  speaker?: string; // Make original fields optional if they are not always present
  text?: string;    // Make original fields optional
}

export type ChessboardArrowType = {
    drawArrow: (
        from: string,
        to: string,
        options?: { clear?: boolean; fillColor?: string }
    ) => void;
    clear: () => void;
};

export type Theme = {
    white: string;
    black: string;
    displayColor: string;
};

export type ThemeName = "default" | "blue" | "green" | "gray" | "orange";

export interface IGroupedAnalysisComment {
  move: string; // e.g., "e4", "Nf3", or move number like "1. e4"
  whiteComment?: string;
  blackComment?: string;
  analystComment?: string; // General analyst comment for the FEN after the last move in this group (either white's or black's)
  iconMoveSan?: string; // The SAN of the move to determine the icon (e.g., "e4", "Nf3")
  iconMoveColor?: 'w' | 'b'; // The color of the player who made the iconMoveSan
  blackIconMoveSan?: string; // The SAN of Black's move in the pair, for its icon
  blackIconMoveColor?: 'w' | 'b'; // The color for Black's move icon (should be 'b')
}
