import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const VITE_PROXY_BASE = 'http://127.0.0.1:5173/api/v1';

function resetTestProjectBaseline() {
  try {
    const root = path.resolve(process.cwd(), '..');
    const v001Path = path.join(root, 'projects', 'proj_test_completed_research', 'script', 'versions', 'v001.json');
    const currentPath = path.join(root, 'projects', 'proj_test_completed_research', 'script', 'current.json');
    if (fs.existsSync(v001Path)) {
      fs.copyFileSync(v001Path, currentPath);
    }
  } catch {
    // Ignore if not accessible
  }
}

async function checkServerOnline(): Promise<boolean> {
  try {
    const res = await fetch(`${VITE_PROXY_BASE}/health`, { signal: AbortSignal.timeout(1000) });
    return res.ok;
  } catch {
    return false;
  }
}

describe('Phase 6 Checkpoints 2 & 3: Live Frontend/Backend Integration Verification', async () => {
  resetTestProjectBaseline();
  const isOnline = await checkServerOnline();

  // =========================================================================
  // 1. Live Proxy & Health
  // =========================================================================
  it('verifies that frontend dev server (Vite) proxies to backend FastAPI server', async (t) => {
    if (!isOnline) {
      t.skip('Vite dev server is offline (127.0.0.1:5173). Skipping live integration probe.');
      return;
    }
    const res = await fetch(`${VITE_PROXY_BASE}/health`);
    assert.equal(res.status, 200);
    const data = await res.json();
    assert.equal(data.status, 'ok');
    assert.equal(data.service, 'ai-animation-studio');
  });

  // =========================================================================
  // 2. Representative State: Project without research
  // =========================================================================
  it('fetches research overview for project without research and receives safe empty state', async (t) => {
    if (!isOnline) {
      t.skip('Vite dev server offline.');
      return;
    }
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
  it('fetches research overview with completed run, budget metrics, and 4 query lifecycle states', async (t) => {
    if (!isOnline) {
      t.skip('Vite dev server offline.');
      return;
    }
    const res = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/research`);
    assert.equal(res.status, 200);
    const data = await res.json();

    assert.equal(data.project_id, 'proj_test_completed_research');
    assert.equal(data.has_research, true);
    assert.equal(data.active_run_id, 'rrun_quantum_verif_01');
    assert.equal(data.status, 'completed');

    // Budget metrics verification
    assert.equal(data.budget_summary.search_calls_used, 3);
    assert.equal(data.budget_summary.extract_calls_used, 2);

    // 4 Lifecycle Query states
    const planned = data.queries.find((q: any) => q.status === 'planned');
    const completed = data.queries.find((q: any) => q.status === 'completed');
    const skipped = data.queries.find((q: any) => q.status === 'skipped');
    const failed = data.queries.find((q: any) => q.status === 'failed');

    assert.ok(planned, 'Planned query must exist');
    assert.ok(completed, 'Completed query must exist');
    assert.ok(skipped, 'Skipped query must exist');
    assert.ok(failed, 'Failed query must exist');

    assert.equal(skipped.skip_reason, 'Search call budget exhausted (max 3 calls reached).');
    assert.equal(failed.error_message, 'Provider HTTP 503 service temporarily unavailable.');
  });

  // =========================================================================
  // 4. Evidence Classification: Candidate, Selected, Used, and Unused
  // =========================================================================
  it('strictly distinguishes candidate, selected, used, and unused evidence from live overview', async (t) => {
    if (!isOnline) {
      t.skip('Vite dev server offline.');
      return;
    }
    const res = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/research`);
    const data = await res.json();

    const retrievedIds: string[] = data.retrieved_evidence_ids;
    const selectedIds: string[] = data.selected_evidence_ids;
    const usedIds: string[] = data.used_evidence_ids;
    const unusedIds: string[] = data.unused_evidence_ids;

    assert.equal(retrievedIds.length, 4);
    assert.equal(selectedIds.length, 3);
    assert.equal(usedIds.length, 1);
    assert.equal(unusedIds.length, 3);

    assert.ok(!selectedIds.includes('evi_cand_unselected'));
    assert.ok(selectedIds.includes('evi_selected_unused'));
    assert.ok(!usedIds.includes('evi_selected_unused'));
    assert.ok(unusedIds.includes('evi_selected_unused'));
    assert.ok(usedIds.includes('evi_used_in_narration'));

    const candSelected = data.selected_evidence.find((e: any) => e.evidence_id === 'evi_cand_selected');
    assert.equal(candSelected.selection_rationale, 'Empirical measurement of interface loss at cryogenic temperatures.');

    const unusedSelected = data.selected_evidence.find((e: any) => e.evidence_id === 'evi_selected_unused');
    assert.equal(unusedSelected.selection_rationale, null);
  });

  // =========================================================================
  // 5. Evidence Brief & Outbound Link Safety
  // =========================================================================
  it('retrieves evidence brief markdown and validates safe source outbound links', async (t) => {
    if (!isOnline) {
      t.skip('Vite dev server offline.');
      return;
    }
    const resBrief = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/research/brief`);
    assert.equal(resBrief.status, 200);
    const briefData = await resBrief.json();
    assert.match(briefData.brief_markdown, /# Research Evidence Brief/);

    const resOverview = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/research`);
    const overviewData = await resOverview.json();
    for (const src of overviewData.sources) {
      assert.match(src.url, /^https?:\/\//);
      assert.ok(src.publisher.length > 0);
    }
  });

  // =========================================================================
  // 6. Graceful 404 handling: Missing brief and unavailable fact-check report
  // =========================================================================
  it('handles missing evidence brief and missing fact-check report with correct HTTP 404', async (t) => {
    if (!isOnline) {
      t.skip('Vite dev server offline.');
      return;
    }
    const resBrief = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_missing_brief/research/brief`);
    assert.equal(resBrief.status, 404);
    const errBrief = await resBrief.json();
    assert.match(errBrief.detail, /no evidence brief found/i);

    const resFc = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_missing_brief/script/fact-check`);
    assert.equal(resFc.status, 404);
    const errFc = await resFc.json();
    assert.match(errFc.detail, /no current script/i);
  });

  // =========================================================================
  // 7. Stale data prevention on project switching
  // =========================================================================
  it('verifies that fetching distinct projects returns isolated data without cross-talk', async (t) => {
    if (!isOnline) {
      t.skip('Vite dev server offline.');
      return;
    }
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

  // =========================================================================
  // 8. Phase 6 Checkpoint 3: Live Fact-Check & Conversational Claim Challenge
  // =========================================================================
  it('challenges a script claim via live proxy and receives structured summary without auto-finalizing', async (t) => {
    if (!isOnline) {
      t.skip('Vite dev server offline.');
      return;
    }

    // Read current fact check
    const fcRes = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/script/fact-check`);
    if (fcRes.status === 200) {
      const fcData = await fcRes.json();
      assert.ok(Array.isArray(fcData.assessments));
    }

    // Fetch current active script to dynamically identify the active claim
    const currentScriptRes = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/script`);
    assert.equal(currentScriptRes.status, 200);
    const currentScriptData = await currentScriptRes.json();
    const activeClaim = currentScriptData.script.claims.find((c: any) => c.link_status === 'linked');
    assert.ok(activeClaim, 'An active linked claim must exist for testing challenge');

    // Post challenge: remove the active claim (deterministic surgery)
    const challengeRes = await fetch(
      `${VITE_PROXY_BASE}/projects/proj_test_completed_research/script/challenge`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          claim_id: activeClaim.claim_id,
          action: 'remove',
          instruction: `Remove claim ${activeClaim.claim_id} from narration and mark unlinked.`,
        }),
      }
    );

    assert.equal(challengeRes.status, 200);
    const challengeData = await challengeRes.json();
    assert.ok(challengeData.challenge_summary);
    assert.equal(challengeData.challenge_summary.action, 'remove');
    assert.equal(challengeData.challenge_summary.target_claim_id, activeClaim.claim_id);
    assert.equal(challengeData.challenge_summary.outcome, 'removed_and_unlinked');
    assert.ok(challengeData.challenge_summary.explanation.length > 0);

    // Verify the updated script status remains draft
    const scriptRes = await fetch(`${VITE_PROXY_BASE}/projects/proj_test_completed_research/script`);
    assert.equal(scriptRes.status, 200);
    const scriptData = await scriptRes.json();
    assert.equal(scriptData.script.status, 'draft', 'Challenged script must remain draft and not auto-finalize');

    // Verify claim is now unlinked in the updated script
    const unlinkedClaim = scriptData.script.claims.find((c: any) => c.claim_id === activeClaim.claim_id);
    assert.ok(unlinkedClaim);
    assert.equal(unlinkedClaim.link_status, 'unlinked');
  });
});
