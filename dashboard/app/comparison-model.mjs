/**
 * Only explicit manifest relationships may establish a matched comparison.
 * No nearest-neighbour, title, ID, or outcome heuristics are used.
 * @typedef {import('./run-explorer').RunEnvelope} RunEnvelope
 * @typedef {import('./run-explorer').ArtifactRelationship} ArtifactRelationship
 */

/** @param {RunEnvelope[]} runs @param {ArtifactRelationship[]} relationships @param {string} selectedId */
export function matchedPair(runs, relationships, selectedId) {
  const relation = relationships.find((candidate) => candidate.type === 'matched_control_for'
    && [candidate.source_artifact_id, candidate.target_artifact_id].includes(selectedId));
  if (!relation) return null;
  const control = runs.find((run) => run.id === relation.source_artifact_id);
  const fault = runs.find((run) => run.id === relation.target_artifact_id);
  if (!control || !fault) return null;
  const fields = ['seed', 'variant', 'study'];
  if (fields.some((field) => control.metadata[field] == null
    || control.metadata[field] !== fault.metadata[field])) return null;
  if (control.data.strategy !== fault.data.strategy
    || control.metadata.study_arm !== 'matched_control'
    || fault.metadata.study_arm !== 'fault') return null;
  return { control, fault, relation };
}

/**
 * Compare observable event fields in recorded order, not run-specific IDs or
 * metadata. This is a first observable difference, not a causal inference.
 * @param {RunEnvelope} control @param {RunEnvelope} fault
 */
export function firstObservableChange(control, fault) {
  const fields = ['at_ms', 'kind', 'actor', 'task_id', 'message'];
  for (let index = 0; index < Math.max(control.data.trace.length, fault.data.trace.length); index += 1) {
    const left = control.data.trace[index] ?? null;
    const right = fault.data.trace[index] ?? null;
    if (!left || !right || fields.some((field) => left[field] !== right[field])) {
      return { controlEvent: left, faultEvent: right, matchingEvents: index };
    }
  }
  return null;
}

/** @param {number | null | undefined} control @param {number | null | undefined} fault */
export function numericDelta(control, fault) {
  if (typeof control !== 'number' || !Number.isFinite(control)
    || typeof fault !== 'number' || !Number.isFinite(fault)) return null;
  return fault - control;
}
