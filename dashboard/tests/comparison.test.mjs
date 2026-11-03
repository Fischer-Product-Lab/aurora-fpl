import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { matchedPair, firstObservableChange, numericDelta } from '../app/comparison-model.mjs';
const root = new URL('../public/data/', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('manifest.json', root), 'utf8'));
const runs = await Promise.all(manifest.artifacts.filter((ref) => ref.type === 'run').map(async (ref) => JSON.parse(await readFile(new URL(ref.path, root), 'utf8'))));

test('every manifest-defined pair resolves in both directions with matching conditions', () => {
  const relations = manifest.relationships.filter((relation) => relation.type === 'matched_control_for');
  assert.equal(relations.length, 6);
  for (const relation of relations) {
    const pair = matchedPair(runs, manifest.relationships, relation.source_artifact_id);
    assert.ok(pair);
    assert.equal(pair.control.id, relation.source_artifact_id);
    assert.equal(pair.fault.id, relation.target_artifact_id);
    assert.deepEqual(matchedPair(runs, manifest.relationships, relation.target_artifact_id), pair);
    assert.equal(pair.control.metadata.seed, pair.fault.metadata.seed);
    assert.equal(pair.control.data.strategy, pair.fault.data.strategy);
  }
});

test('baseline, missing relationships and incomplete pairs never manufacture a comparison', () => {
  assert.equal(matchedPair(runs, manifest.relationships, 'clean-full-orchestration'), null);
  assert.equal(matchedPair(runs, [], 'worker-fault-with-fallback'), null);
  assert.equal(matchedPair(runs.filter((run) => run.id !== 'worker-control-with-fallback'), manifest.relationships, 'worker-fault-with-fallback'), null);
});

test('mismatched seeds, variants, studies, arms and strategies are rejected', () => {
  for (const [field, value] of [['seed', 9], ['variant', 'other'], ['study', 'other'], ['study_arm', 'baseline']]) {
    const altered = structuredClone(runs);
    altered.find((run) => run.id === 'worker-fault-with-fallback').metadata[field] = value;
    assert.equal(matchedPair(altered, manifest.relationships, 'worker-fault-with-fallback'), null);
  }
  const altered = structuredClone(runs);
  altered.find((run) => run.id === 'worker-fault-with-fallback').data.strategy = 'different';
  assert.equal(matchedPair(altered, manifest.relationships, 'worker-fault-with-fallback'), null);
});

test('first change preserves exact source events for the worker, planning and budget study', () => {
  for (const [id, expectedAt, expectedSequence] of [['worker-fault-with-fallback', 2552, 28], ['planning-fault-with-critic', 4770, 37], ['budget-fault-with-fallback', 2652, 32]]) {
    const { control, fault } = matchedPair(runs, manifest.relationships, id);
    const change = firstObservableChange(control, fault);
    assert.equal(change.faultEvent.kind, 'fault_injected');
    assert.equal(change.faultEvent.at_ms, expectedAt);
    assert.equal(change.faultEvent.sequence, expectedSequence);
    assert.equal(change.faultEvent, fault.data.trace[change.matchingEvents]);
    assert.equal(change.controlEvent, control.data.trace[change.matchingEvents]);
  }
});

test('the shared worker fault is not mistaken for a budget intervention', () => {
  const { control, fault } = matchedPair(runs, manifest.relationships, 'budget-fault-with-fallback');
  const change = firstObservableChange(control, fault);
  assert.ok(change.faultEvent.metadata.some(([key, value]) => key === 'fault_name' && value === 'fallback_budget_exhaustion'));
});

test('negative controls accurately show no observable change and no fabricated intervention', () => {
  const { control, fault } = matchedPair(runs, manifest.relationships, 'budget-fault-negative-control');
  assert.equal(firstObservableChange(control, fault), null);
  assert.equal(numericDelta(control.data.cost_units_used, fault.data.cost_units_used), 0);
});

test('metadata-only changes are excluded explicitly; truncated and extended traces are visible', () => {
  const original = runs[0];
  const altered = structuredClone(original);
  altered.data.trace[0].metadata.push(['run_id', 'different']);
  assert.equal(firstObservableChange(original, altered), null);
  altered.data.trace.pop();
  const change = firstObservableChange(original, altered);
  assert.equal(change.faultEvent, null);
  assert.equal(change.controlEvent, original.data.trace.at(-1));
  assert.equal(firstObservableChange(altered, original).controlEvent, null);
});

test('missing and non-finite observations never become zero in deltas', () => {
  for (const missing of [null, undefined, NaN, Infinity, '2']) {
    assert.equal(numericDelta(missing, 2), null);
    assert.equal(numericDelta(2, missing), null);
  }
  assert.equal(numericDelta(4, 3), -1);
  assert.equal(numericDelta(4, 4), 0);
  assert.equal(numericDelta(3, 4), 1);
});
