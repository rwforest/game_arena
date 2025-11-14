// In api-utils.ts
export interface IApiRawMoveComment {
  speaker: string;
  text: string;
}

export async function fetchCommentsForFen(fen: string): Promise<IApiRawMoveComment[]> {
    const response = await fetch("https://chess-engine-fn-app-huhwfbdcevfdevg2.westus-01.azurewebsites.net/api/llm_comments", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({ fen }), // Only FEN is sent
    });

    if (!response.ok) {
        const errorBody = await response.text();
        console.error(`API Error (${response.status}) for FEN ${fen}: ${errorBody}`);
        return []; // Return empty array on error for consistent handling
    }
    
    try {
        const data = await response.json();
        return data || []; // Ensure an array is always returned
    } catch (jsonError) {
        console.error(`JSON parsing error for FEN ${fen}:`, jsonError);
        return []; // Return empty on JSON error
    }
}