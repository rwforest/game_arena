import React, { useEffect, useRef, useState, type CSSProperties } from 'react';

interface EvaluationBarProps {
  evaluation: number; // Typically a centipawn value, e.g., 100 means +1 pawn for white
  height: number; // Height of the bar in pixels
}

const EvaluationBar: React.FC<EvaluationBarProps> = ({ evaluation, height }) => {
  const MAX_EVALUATION = 1000;
  const normalizedEval = Math.max(-MAX_EVALUATION, Math.min(MAX_EVALUATION, evaluation));
  const whiteAdvantageRatio = (normalizedEval + MAX_EVALUATION) / (2 * MAX_EVALUATION);

  const whiteHeightPercent = Math.max(0, Math.min(100, whiteAdvantageRatio * 100));
  const blackHeightPercent = 100 - whiteHeightPercent;
  const circleDiameter = 16;

  const circleBottomPositionPercent = whiteHeightPercent;

  const [isGlowing, setIsGlowing] = useState(false);
  const prevEvalRef = useRef(evaluation);

  useEffect(() => {
    if (evaluation !== prevEvalRef.current) {
      setIsGlowing(true);
      const timer = setTimeout(() => setIsGlowing(false), 700);
      prevEvalRef.current = evaluation;
      return () => clearTimeout(timer);
    }
  }, [evaluation]);

  const containerStyle: CSSProperties = {
    height: `${height}px`,
    width: '24px',
    position: 'relative',
    display: 'flex',
    flexDirection: 'column-reverse', // <== Inverted to put White at bottom
    borderRadius: '4px',
    overflow: 'visible',
    border: '1px solid #4A5568',
    backgroundColor: '#2D3748',
  };

  const whiteBarStyle: CSSProperties = {
    height: `${whiteHeightPercent}%`,
    backgroundColor: 'rgba(237, 242, 247, 0.9)',
    transition: 'height 0.3s ease-in-out',
  };

  const blackBarStyle: CSSProperties = {
    height: `${blackHeightPercent}%`,
    backgroundColor: 'rgba(26, 32, 44, 0.9)',
    transition: 'height 0.3s ease-in-out',
  };

  const circleStyle: CSSProperties = {
    position: 'absolute',
    bottom: `${circleBottomPositionPercent}%`, // <== From the bottom now
    left: '50%',
    transform: 'translate(-50%, 50%)',
    width: `${circleDiameter}px`,
    height: `${circleDiameter}px`,
    backgroundColor: '#48BB78',
    borderRadius: '50%',
    border: '2px solid #F7FAFC',
    zIndex: 10,
    transition: 'bottom 0.3s ease-in-out',
    animation: isGlowing ? 'evalGlowAnimation 0.7s ease-in-out' : 'none',
  };

  return (
    <div style={containerStyle}>
      <div style={whiteBarStyle} />
      <div style={blackBarStyle} />
      <div style={circleStyle} />
    </div>
  );
};

export default EvaluationBar;
