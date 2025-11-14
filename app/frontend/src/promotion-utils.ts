import { Chess, type Square } from "chess.js";

export const showPromotionDialog = async (pieceColor: 'w' | 'b'): Promise<string> => {
  return new Promise<string>(resolve => {
    const pieces = ["q", "r", "b", "n"];
    const modal = document.createElement("div");
    modal.style.position = "fixed";
    modal.style.top = "0";
    modal.style.left = "0";
    modal.style.width = "100%";
    modal.style.height = "100%";
    modal.style.backgroundColor = "rgba(0,0,0,0.7)";
    modal.style.display = "flex";
    modal.style.justifyContent = "center";
    modal.style.alignItems = "center";
    modal.style.zIndex = "1000";

    const container = document.createElement("div");
    container.style.backgroundColor = "white";
    container.style.padding = "20px";
    container.style.borderRadius = "10px";
    container.style.display = "flex";
    container.style.gap = "10px";
    container.style.flexDirection = "column";
    container.style.alignItems = "center";

    const title = document.createElement("h3");
    title.textContent = "Select piece to promote:";
    title.style.margin = "0 0 10px 0";
    container.appendChild(title);

    const buttonsContainer = document.createElement("div");
    buttonsContainer.style.display = "flex";
    buttonsContainer.style.gap = "10px";

    pieces.forEach(p => {
      const btn = document.createElement("div");
      btn.style.cursor = "pointer";
      btn.style.padding = "10px";
      btn.style.borderRadius = "5px";
      btn.style.backgroundColor = "#f0f0f0";
      btn.style.display = "flex";
      btn.style.flexDirection = "column";
      btn.style.alignItems = "center";

      const img = document.createElement("img");
      img.src = `/img/chesspieces/wikipedia/${pieceColor}${p.toUpperCase()}.png`;
      img.width = 40;
      img.height = 40;

      const label = document.createElement("span");
      label.textContent = p.toUpperCase();
      label.style.marginTop = "5px";

      btn.appendChild(img);
      btn.appendChild(label);

      btn.onclick = () => {
        document.body.removeChild(modal);
        resolve(p);
      };
      buttonsContainer.appendChild(btn);
    });

    container.appendChild(buttonsContainer);
    modal.appendChild(container);
    document.body.appendChild(modal);
  });
};

export const isPromotionMove = (chess: Chess, source: Square, target: Square): boolean => {
  const piece = chess.get(source);
  return piece?.type === "p" && 
    ((piece.color === "w" && target[1] === "8") || 
     (piece.color === "b" && target[1] === "1"));
};