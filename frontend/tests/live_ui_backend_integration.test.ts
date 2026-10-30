import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const VITE_PROXY_BASE = 'http://127.0.0.1:5173/api/v1';
const BACKEND_BASE = 'http://127.0.0.1:8000/api/v1';

describe('Phase 6 Checkpoint 2: Live Frontend/Backend Integration Verification', () => {
  // =========================================================================
  // 1. Live Proxy & Health
  // =========================================================================
  it('verifies that frontend dev server (Vite) proxies to backend FastAPI server', async () => {
    const res = await fetch(`${VITE_PROXY_BASE}/health`);
    assert.equal(res.status, 200);
    const data = await res.json();
    assert.equal(data.status, 'ok');
    assert.equal(data.service, 'ai-animation-studio');
  });

  // =========================================================================
  // 2. Representative State: Project without research
  // =========================================================================
  it('fetches research overview for project without research and receives safe empty state', async () => {
    const res = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_no_research/research`);
    assert.equal(res.status, 200);
    const data = await res.json();

    assert.equal(data.project_id, 'proj_test_no_research');
    assert.equal(data.has_research, false);
    assert.equal(data.active_run_id, null);
    assert.equal(data.queries.length, 0);
    assert.equal(data.sources.length, 0);
    assert.equal(data.selected_evidence.length, 0);
    assert.equal(data.used_evidence_ids.length, 0);
    assert.equal(data.unused_evidence_ids.length, 0);
    assert.ok(data.disclaimer);
  });

  // =========================================================================
  // 3. Representative State: Project with completed research & full query lifecycle
  // =========================================================================
  it('fetches research overview with completed run, budget metrics, and 4 query lifecycle states', async () => {
    const res = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/research`);
    assert.equal(res.status, 200);
    const data = await res.json();

    assert.equal(data.project_id, 'proj_test_completed_research');
    assert.equal(data.has_research, true);
    assert.equal(data.active_run_id, 'rrun_quantum_verif_01');
    assert.equal(data.status, 'completed');

    // Budget expenditures
    assert.equal(data.budget_summary.search_calls_used, 3);
    assert.equal(data.budget_summary.extract_calls_used, 2);
    assert.equal(data.budget_summary.total_chars_extracted, 18450);
    assert.equal(data.budget_summary.max_search_calls, 3);
    assert.equal(data.budget_summary.max_extract_calls, 3);

    // Queries: completed, skipped, planned, failed
    assert.equal(data.queries.length, 4);
    const qMap = new Map(data.queries.map((q: any) => [q.query_id, q]));

    // Completed
    const qComp: any = qMap.get('qry_comp_01');
    assert.ok(qComp);
    assert.equal(qComp.status, 'completed');
    assert.equal(qComp.result_count, 2);

    // Skipped with recorded reason
    const qSkip: any = qMap.get('qry_skip_01');
    assert.ok(qSkip);
    assert.equal(qSkip.status, 'skipped');
    assert.match(qSkip.skip_reason, /budget exhausted/i);

    // Planned
    const qPlan: any = qMap.get('qry_plan_01');
    assert.ok(qPlan);
    assert.equal(qPlan.status, 'planned');
    assert.equal(qPlan.result_count, 0);

    // Failed with error message
    const qFail: any = qMap.get('qry_fail_01');
    assert.ok(qFail);
    assert.equal(qFail.status, 'failed');
    assert.match(qFail.error_message, /503/);
  });

  // =========================================================================
  // 4. Evidence lifecycle distinctions on live backend
  // =========================================================================
  it('strictly distinguishes candidate, selected, used, and unused evidence from live overview', async () => {
    const res = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/research`);
    assert.equal(res.status, 200);
    const data = await res.json();

    const eviMap = new Map(data.selected_evidence.map((e: any) => [e.evidence_id, e]));

    // 1. Candidate WITHOUT selection
    const candUnselected: any = eviMap.get('evi_cand_unselected');
    assert.ok(candUnselected);
    assert.equal(candUnselected.status, 'candidate');
    assert.equal(candUnselected.is_selected, false);
    assert.equal(candUnselected.is_used_in_current_script, false);
    assert.equal(candUnselected.selection_rationale, 'Key reference for non-linear inductance and artificial atom physics.');
    assert.ok(!data.selected_evidence_ids.includes('evi_cand_unselected'));
    assert.ok(data.unused_evidence_ids.includes('evi_cand_unselected'));

    // 2. Candidate WITH selection (selected but unused)
    const candSelected: any = eviMap.get('evi_cand_selected');
    assert.ok(candSelected);
    assert.equal(candSelected.status, 'candidate');
    assert.equal(candSelected.is_selected, true);
    assert.equal(candSelected.is_used_in_current_script, false);
    assert.equal(candSelected.selection_rationale, 'Empirical measurement of interface loss at cryogenic temperatures.');
    assert.ok(data.selected_evidence_ids.includes('evi_cand_selected'));
    assert.ok(data.unused_evidence_ids.includes('evi_cand_selected'));

    // 3. Retrieved excerpt, selected but unused in script
    const selUnused: any = eviMap.get('evi_selected_unused');
    assert.ok(selUnused);
    assert.equal(selUnused.status, 'retrieved');
    assert.equal(selUnused.is_selected, true);
    assert.equal(selUnused.is_used_in_current_script, false);
    assert.equal(selUnused.selection_rationale, null); // Null preserved
    assert.ok(data.selected_evidence_ids.includes('evi_selected_unused'));
    assert.ok(data.unused_evidence_ids.includes('evi_selected_unused'));

    // 4. Retrieved excerpt, selected AND actively used in script narration
    const usedInNarr: any = eviMap.get('evi_used_in_narration');
    assert.ok(usedInNarr);
    assert.equal(usedInNarr.status, 'retrieved');
    assert.equal(usedInNarr.is_selected, true);
    assert.equal(usedInNarr.is_used_in_current_script, true);
    assert.deepEqual(usedInNarr.used_by_claims, ['clm_01']);
    assert.equal(usedInNarr.selection_rationale, null); // Null preserved
    assert.ok(data.selected_evidence_ids.includes('evi_used_in_narration'));
    assert.ok(data.used_evidence_ids.includes('evi_used_in_narration'));
    assert.ok(!data.unused_evidence_ids.includes('evi_used_in_narration'));
  });

  // =========================================================================
  // 5. Live Evidence Brief inspection & Safe Sources
  // =========================================================================
  it('retrieves evidence brief markdown and validates safe source outbound links', async () => {
    // Brief endpoint
    const resBrief = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/research/brief`);
    assert.equal(resBrief.status, 200);
    const briefData = await resBrief.json();
    assert.ok(briefData.brief_markdown.includes('# Research Evidence Brief: Quantum Superposition & Hardware Limits'));
    assert.ok(briefData.brief_markdown.toLowerCase().includes('transmon qubits'));

    // Sources verification
    const resOverview = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/research`);
    const overviewData = await resOverview.json();
    assert.equal(overviewData.sources.length, 2);

    for (const src of overviewData.sources) {
      assert.ok(src.url.startsWith('https://'), `Source URL must use safe HTTPS: ${src.url}`);
      assert.ok(!src.url.includes('projects/'), 'Must not expose internal paths');
      assert.ok(src.title.length > 0);
      assert.ok(src.publisher.length > 0);
    }
  });

  // =========================================================================
  // 6. Graceful 404 handling: Missing brief and unavailable fact-check report
  // =========================================================================
  it('handles missing evidence brief and missing fact-check report with correct HTTP 404', async () => {
    // Missing brief
    const resBrief = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_missing_brief/research/brief`);
    assert.equal(resBrief.status, 404);
    const errBrief = await resBrief.json();
    assert.match(errBrief.detail, /no evidence brief found/i);

    // Missing fact-check report
    const resFc = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_missing_brief/script/fact-check`);
    assert.equal(resFc.status, 404);
    const errFc = await resFc.json();
    assert.match(errFc.detail, /no current script/i);
  });

  // =========================================================================
  // 7. Stale data prevention on project switching
  // =========================================================================
  it('verifies that fetching distinct projects returns isolated data without cross-talk', async () => {
    const res1 = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/research`);
    const data1 = await res1.json();

    const res2 = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_no_research/research`);
    const data2 = await res2.json();

    assert.equal(data1.project_id, 'proj_test_completed_research');
    assert.equal(data1.has_research, true);
    assert.equal(data1.selected_evidence.length, 4);

    assert.equal(data2.project_id, 'proj_test_no_research');
    assert.equal(data2.has_research, false);
    assert.equal(data2.selected_evidence.length, 0);
  });
});
