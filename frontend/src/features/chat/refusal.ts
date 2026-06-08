// Human copy for a refusal reason. A refusal is a first-class, designed state (ADR 0014) —
// it explains why and suggests a concrete next step (rephrase / upload), never a dead end.

const REFUSAL_COPY: Record<string, string> = {
  INSUFFICIENT_SOURCES:
    'I couldn’t find enough relevant information in your documents to answer that confidently. Try rephrasing, or upload documents that cover this topic.',
  UNGROUNDED_ANSWER:
    'I wasn’t able to ground an answer in your sources, so I won’t guess. Try rephrasing your question or narrowing it down.',
}

const GENERIC_REFUSAL =
  'I can’t answer that from the available sources. Try rephrasing, or add documents that cover it.'

export function refusalCopy(reason: string): string {
  return REFUSAL_COPY[reason] ?? GENERIC_REFUSAL
}
