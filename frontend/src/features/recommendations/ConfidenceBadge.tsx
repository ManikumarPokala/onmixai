// Confidence band — labeled HONESTLY as evidence strength, never model certainty (ADR 0016).
// The tooltip states this explicitly so the UI never implies the model is "95% sure".

const LABEL: Record<string, string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

const EVIDENCE_NOTE =
  'Reflects the strength of the retrieved evidence this recommendation stands on — not the model’s certainty.'

export function ConfidenceBadge({ band }: { band: string }) {
  return (
    <span className={`confidence-badge confidence-badge--${band}`} title={EVIDENCE_NOTE}>
      <span className="confidence-badge__label">Evidence strength</span>
      <strong>{LABEL[band] ?? band}</strong>
    </span>
  )
}
