type WinnerModalProps = {
  gameResult: string;
  onClose: () => void;
  onNewGame: () => void;
};

const WinnerModal = ({ gameResult, onClose, onNewGame }: WinnerModalProps) => {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75">
      <div className="relative bg-white/75 rounded-xl shadow-xl p-6 max-w-sm w-full text-center">
        <h2 className="text-2xl font-semibold mb-4">{gameResult}</h2>
        <div className="flex gap-4 justify-center">
          <button
            onClick={onNewGame}
            className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg cursor-pointer hover:bg-blue-700 transition"
          >
            Play Again
          </button>
          <button
            onClick={onClose}
            className="mt-4 px-4 py-2 bg-gray-600 text-white rounded-lg cursor-pointer hover:bg-gray-700 transition"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

export default WinnerModal;
