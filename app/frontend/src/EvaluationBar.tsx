interface EvaluationBarProps {
  evaluation: number; // in centipawns
  height?: number;     // Optional height for the bar
}

const EvaluationBar: React.FC<EvaluationBarProps> = ({ evaluation, height }) => {
  const maxEval = 1000; // Max centipawns for 100% bar fill for one side
  const barHeight = height || 300; // Use prop or default

  // Clamp evaluation to the range [-maxEval, maxEval]
  const clampedEval = Math.max(-maxEval, Math.min(maxEval, evaluation));

  // Calculate white's percentage (0 to 100)
  // Base is 50%, then add/subtract based on eval.
  // (clampedEval / maxEval) gives a value from -1 to 1.
  // Multiplying by 50 gives a range from -50 to 50.
  // Adding 50 shifts this to 0 to 100.
  const whitePercent = 50 + (clampedEval / maxEval) * 50;
  const blackPercent = 100 - whitePercent;

  const barStyle: React.CSSProperties = {
    width: '24px', // Slightly wider for better visual
    height: `${barHeight}px`, // Apply dynamic height
    border: '1px solid #555',
    display: 'flex',
    flexDirection: 'column', // Stack black on top of white
    backgroundColor: '#ccc', // Fallback/border color
    boxSizing: 'border-box',
  };

  const blackStyle: React.CSSProperties = {
    width: '100%',
    height: `${blackPercent}%`,
    backgroundColor: '#333', // Dark gray for black
    transition: 'height 0.5s ease-in-out', // Smooth transition
  };

  const whiteStyle: React.CSSProperties = {
    width: '100%',
    height: `${whitePercent}%`,
    backgroundColor: '#DDD', // Light gray for white
    transition: 'height 0.5s ease-in-out', // Smooth transition
  };

  return (
    <div style={barStyle}>
      <div style={blackStyle}></div>
      <div style={whiteStyle}></div>
    </div>
  );
};

export default EvaluationBar;
