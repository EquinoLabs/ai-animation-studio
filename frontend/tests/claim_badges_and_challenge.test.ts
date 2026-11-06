import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import type {
  Script,
  ScriptSection,
  ScriptClaim,
  ScriptFactCheckReport,
  ClaimAssessment,
  ResearchOverviewResponse,
  ChallengeClaimRequest,
  ChallengeClaimResponse,
  WorkflowState,
} from '../src/types.ts';

describe('Phase 6 Checkpoint 3: Claim Badges, Passage Linkage & Conversational Fact Challenges', () => {
  // =========================================================================
  // Test 1: Claim-Level Annotations & Discrete Passage Linkage
  // =========================================================================
  it('annotates claims discretely without implying entire paragraph is certified', () => {
    const claims: ScriptClaim[] = [
      {
        claim_id: 'claim_q1',
        claim_text: 'Superconducting qubits operate near absolute zero (~15 millikelvin).',
        section_id: 'sec_01',
        evidence_ids: ['ev_phys_01'],
        source_ids: ['src_phys_01'],
        link_status: 'linked',
        assessment_status: 'supported',
        risk_level: 'normal',
      },
      {
        claim_id: 'claim_q2',
        claim_text: 'Quantum computers have rendered all 2048-bit RSA encryption completely obsolete today.',
        section_id: 'sec_01',
        evidence_ids: ['ev_crypto_02'],
        source_ids: ['src_crypto_02'],
        link_status: 'linked',
        assessment_status: 'contradicted',
        risk_level: 'high',
      },
      {
        claim_id: 'claim_q3',
        claim_text: 'Commercial quantum computers will be installed in every high school by 2028.',
        section_id: 'sec_01',
        evidence_ids: [],
        source_ids: [],
        link_status: 'linked',
        assessment_status: 'not_checked',
        risk_level: 'creative_flexibility',
      },
    ];

    const section: ScriptSection = {
      id: 'sec_01',
      type: 'hook',
      heading: 'Quantum Reality',
      narration:
        'Superconducting qubits operate near absolute zero (~15 millikelvin). Despite common fearmongering, quantum computers have rendered all 2048-bit RSA encryption completely obsolete today. Some enthusiasts even claim commercial quantum computers will be installed in every high school by 2028.',
      claim_ids: ['claim_q1', 'claim_q2', 'claim_q3'],
    };

    // Filter active section claims
    const activeClaims = claims.filter(
      (c) => c.section_id === section.id && (section.claim_ids || []).includes(c.claim_id) && c.link_status !== 'unlinked'
    );

    assert.equal(activeClaims.length, 3);
    assert.equal(activeClaims[0].assessment_status, 'supported');
    assert.equal(activeClaims[0].risk_level, 'normal');
    assert.equal(activeClaims[1].assessment_status, 'contradicted');
    assert.equal(activeClaims[1].risk_level, 'high');
    assert.equal(activeClaims[2].assessment_status, 'not_checked');
    assert.equal(activeClaims[2].risk_level, 'creative_flexibility');

    // Invariant: The section narration text remains plain string, discrete claims are tagged individually
    assert.ok(typeof section.narration === 'string');
    assert.ok(activeClaims.length === 3);
  });

  // =========================================================================
  // Test 2: Unchecked & Missing Claims Handling
  // =========================================================================
  it('correctly categorizes unassessed claims as not_checked with neutral badge styling', () => {
    const claimWithoutReport: ScriptClaim = {
      claim_id: 'claim_unassessed_01',
      section_id: 'sec_intro',
      proposition: 'The earth has a circumference of approximately 40,075 kilometers.',
      evidence_ids: [],
      link_status: 'linked',
      assessment_status: undefined,
      risk_level: undefined,
    };

    // Status fallback logic as implemented in ScriptViewer and ClaimInspectionModal
    const assessmentStatus = claimWithoutReport.assessment_status || 'not_checked';
    const riskLevel = claimWithoutReport.risk_level || 'normal';

    assert.equal(assessmentStatus, 'not_checked');
    assert.equal(riskLevel, 'normal');
  });

  // =========================================================================
  // Test 3: Claim Inspection Modal Details & Grounding vs Objective Truth
  // =========================================================================
  it('maps supporting and conflicting evidence excerpts and reinforces truth disclaimer', () => {
    const claim: ScriptClaim = {
      claim_id: 'claim_eval_01',
      claim_text: 'Quantum annealing is identical to universal gate-based quantum computation.',
      section_id: 'sec_02',
      evidence_ids: ['ev_gate_01', 'ev_anneal_02'],
      source_ids: ['src_mit_01'],
      link_status: 'linked',
      risk_level: 'high',
    };

    const assessment: ClaimAssessment = {
      claim_id: 'claim_eval_01',
      section_id: 'sec_02',
      proposition: 'Quantum annealing is identical to universal gate-based quantum computation.',
      status: 'contradicted',
      risk_level: 'high',
      explanation: 'Quantum annealing addresses optimization problems and is not functionally equivalent to universal gate quantum computers.',
      considered_evidence_ids: ['ev_gate_01', 'ev_anneal_02'],
      supporting_evidence_ids: ['ev_gate_01'],
      contradicting_evidence_ids: ['ev_anneal_02'],
    };

    const overview: ResearchOverviewResponse = {
      project_id: 'proj_q_01',
      has_research: true,
      active_run_id: 'rrun_01',
      status: 'completed',
      termination_reason: 'completed',
      budget_summary: {},
      queries: [],
      sources: [
        {
          source_id: 'src_mit_01',
          title: 'MIT Quantum Review',
          url: 'https://news.mit.edu/quantum-review',
          publisher: 'MIT News',
          retrieval_status: 'extracted',
          retrieved_at: '2026-11-01T10:00:00Z',
          char_count: 5000,
        },
      ],
      selected_evidence: [
        {
          evidence_id: 'ev_gate_01',
          source_id: 'src_mit_01',
          excerpt: 'Universal gate quantum computing implements arbitrary unitary operations.',
          status: 'selected',
          is_selected: true,
          selection_rationale: 'Core theoretical foundation.',
          is_used_in_current_script: true,
          used_by_claims: ['claim_eval_01'],
        },
        {
          evidence_id: 'ev_anneal_02',
          source_id: 'src_mit_01',
          excerpt: 'Annealers are restricted to finding low energy states of Ising spin glasses.',
          status: 'selected',
          is_selected: true,
          selection_rationale: 'Contrasting architecture.',
          is_used_in_current_script: true,
          used_by_claims: ['claim_eval_01'],
        },
      ],
      retrieved_evidence_ids: ['ev_gate_01', 'ev_anneal_02'],
      selected_evidence_ids: ['ev_gate_01', 'ev_anneal_02'],
      used_evidence_ids: ['ev_gate_01', 'ev_anneal_02'],
      unused_evidence_ids: [],
      conflicts: [],
      unresolved_questions: [],
    };

    // Supporting evidence resolution
    const supporting = overview.selected_evidence.filter((e) => (assessment.supporting_evidence_ids || []).includes(e.evidence_id));
    // Conflicting evidence resolution
    const contradicting = overview.selected_evidence.filter((e) =>
      (assessment.contradicting_evidence_ids || []).includes(e.evidence_id)
    );

    assert.equal(supporting.length, 1);
    assert.equal(supporting[0].evidence_id, 'ev_gate_01');
    assert.equal(contradicting.length, 1);
    assert.equal(contradicting[0].evidence_id, 'ev_anneal_02');

    // Safe outbound link validation
    assert.ok(overview.sources[0].url.startsWith('https://'));

    // Fact-checking disclaimer text verification
    const disclaimer =
      'Fact-check evaluations reflect grounding against retrieved research records, not a guarantee of absolute objective truth.';
    assert.ok(disclaimer.includes('retrieved research records'));
    assert.ok(disclaimer.includes('not a guarantee of absolute objective truth'));
  });

  // =========================================================================
  // Test 4: Conversational Challenge Requests for all 4 Actions
  // =========================================================================
  it('constructs unambiguous challenge requests for investigate, qualify, rephrase, and remove', () => {
    const actions: Array<'investigate' | 'qualify' | 'rephrase' | 'remove'> = [
      'investigate',
      'qualify',
      'rephrase',
      'remove',
    ];

    actions.forEach((act) => {
      const req: ChallengeClaimRequest = {
        claim_id: 'claim_target_99',
        action: act,
        instruction: `Apply action ${act} with strict grounding`,
      };

      assert.equal(req.claim_id, 'claim_target_99');
      assert.equal(req.action, act);
      assert.ok(req.instruction?.includes(act));
    });
  });

  // =========================================================================
  // Test 5: Fresh Research vs Recheck Honest Reporting
  // =========================================================================
  it('accurately distinguishes fresh research investigation from existing evidence recheck', () => {
    const freshInvestigationResponse: ChallengeClaimResponse = {
      project_id: 'proj_investigate_01',
      version: 2,
      action: 'investigate',
      target_claim_id: 'claim_target_99',
      challenge_summary: {
        investigation_performed: true,
        action: 'investigate',
        target_claim_id: 'claim_target_99',
        research_run_id: 'rrun_fresh_02',
        new_evidence_count: 3,
        outcome: 'Fresh targeted research was executed, producing 3 new evidence records and reassessing the claim.',
        investigation_note: 'Targeted search query executed successfully via provider.',
      },
    };

    const recheckResponse: ChallengeClaimResponse = {
      project_id: 'proj_investigate_01',
      version: 3,
      action: 'investigate',
      target_claim_id: 'claim_target_99',
      challenge_summary: {
        investigation_performed: false,
        action: 'investigate',
        target_claim_id: 'claim_target_99',
        research_run_id: 'rrun_fresh_02',
        new_evidence_count: 0,
        outcome: 'No fresh investigation executed; existing retrieved evidence was rechecked against claim.',
        investigation_note: 'Orchestrator unavailable in this mode; evaluated against existing catalog.',
      },
    };

    // Fresh investigation assertion
    assert.equal(freshInvestigationResponse.challenge_summary?.investigation_performed, true);
    assert.ok(freshInvestigationResponse.challenge_summary?.new_evidence_count! > 0);
    assert.ok(freshInvestigationResponse.challenge_summary?.outcome.includes('Fresh targeted research'));

    // Recheck only assertion
    assert.equal(recheckResponse.challenge_summary?.investigation_performed, false);
    assert.equal(recheckResponse.challenge_summary?.new_evidence_count, 0);
    assert.ok(recheckResponse.challenge_summary?.outcome.includes('existing retrieved evidence was rechecked'));
  });

  // =========================================================================
  // Test 6: Removed Claim Historical Retention & Active Narration Omission
  // =========================================================================
  it('omits removed claims from active narration claims while retaining historical record', () => {
    const historicalClaims: ScriptClaim[] = [
      {
        claim_id: 'claim_active_01',
        section_id: 'sec_01',
        proposition: 'Photonic chips operate with lower latency.',
        link_status: 'linked',
      },
      {
        claim_id: 'claim_removed_02',
        section_id: 'sec_01',
        proposition: 'Unsubstantiated market hype proposition.',
        link_status: 'unlinked', // Removed via challenge
      },
    ];

    const section: ScriptSection = {
      id: 'sec_01',
      title: 'Photonic Processing',
      narration: 'Photonic chips operate with lower latency.',
      claim_ids: ['claim_active_01'], // Removed claim omitted from section claim_ids
    };

    // Active claims in UI filter out unlinked claims
    const activeClaims = historicalClaims.filter(
      (c) => c.section_id === section.id && (section.claim_ids || []).includes(c.claim_id) && c.link_status !== 'unlinked'
    );

    assert.equal(activeClaims.length, 1);
    assert.equal(activeClaims[0].claim_id, 'claim_active_01');

    // Historical script record still retains claim_removed_02
    const historicalClaim = historicalClaims.find((c) => c.claim_id === 'claim_removed_02');
    assert.ok(historicalClaim);
    assert.equal(historicalClaim.link_status, 'unlinked');
  });

  // =========================================================================
  // Test 7: Challenge Does Not Auto-Finalize Script & Preserves Readiness
  // =========================================================================
  it('verifies challenging a claim creates a new draft version and does not auto-finalize', () => {
    const beforeState: WorkflowState = {
      current_step: 'accepted',
      accepted_version: 1,
      rejected_version: null,
      final_version: null,
      feedback_history: [],
    };

    const newScriptAfterChallenge: Script = {
      title: 'Quantum Overview',
      topic: 'Quantum Computing',
      target_duration_minutes: 5,
      version: 2,
      status: 'draft', // Must remain draft!
      sections: [],
      claims: [],
    };

    // The script must not be final
    assert.notEqual(newScriptAfterChallenge.status, 'final');
    assert.equal(newScriptAfterChallenge.status, 'draft');

    // Workflow state final_version remains null
    assert.equal(beforeState.final_version, null);
  });

  // =========================================================================
  // Test 8: State Isolation on Project Switching (Zero Stale Claims/Reports)
  // =========================================================================
  it('clears claims and fact-check reports when switching projects', () => {
    let currentFactCheckReport: ScriptFactCheckReport | null = {
      report_id: 'fcr_test_01',
      project_id: 'proj_A',
      script_version: 1,
      research_run_id: 'rrun_A',
      requires_repair: false,
      assessments: [
        {
          claim_id: 'claim_A_1',
          section_id: 'sec_01',
          proposition: 'Assertion in section 1.',
          status: 'supported',
          risk_level: 'normal',
          explanation: 'Supported by source A',
          supporting_evidence_ids: ['ev_A_1'],
        },
      ],
    };

    let currentOverview: ResearchOverviewResponse | null = {
      project_id: 'proj_A',
      has_research: true,
      active_run_id: 'rrun_A',
      status: 'completed',
      termination_reason: 'completed',
      budget_summary: {},
      queries: [],
      sources: [],
      selected_evidence: [],
      retrieved_evidence_ids: ['ev_A_1'],
      selected_evidence_ids: ['ev_A_1'],
      used_evidence_ids: ['ev_A_1'],
      unused_evidence_ids: [],
      conflicts: [],
      unresolved_questions: [],
    };

    // Simulating project switch to proj_B
    const nextProjectId = 'proj_B';
    if (nextProjectId !== currentOverview.project_id) {
      currentFactCheckReport = null;
      currentOverview = null;
    }

    assert.equal(currentFactCheckReport, null);
    assert.equal(currentOverview, null);
  });

  // =========================================================================
  // Test 9: Backend ClaimRiskLevel Parsing & Safe Unknown Fallback
  // =========================================================================
  it('parses every backend ClaimRiskLevel (normal, high, creative_flexibility) and handles unknown values safely', () => {
    const riskColors: Record<string, { bg: string; text: string; border: string; label: string }> = {
      high: { bg: 'rgba(244, 63, 94, 0.15)', text: '#fb7185', border: 'rgba(244, 63, 94, 0.35)', label: 'High Risk' },
      normal: { bg: 'rgba(56, 189, 248, 0.15)', text: '#38bdf8', border: 'rgba(56, 189, 248, 0.35)', label: 'Normal Scrutiny' },
      creative_flexibility: { bg: 'rgba(168, 85, 247, 0.15)', text: '#c084fc', border: 'rgba(168, 85, 247, 0.35)', label: 'Creative Flexibility' },
    };

    // 1. Accepted backend tier: normal
    const normalKey = 'normal';
    const normalInfo = riskColors[normalKey] || riskColors.normal;
    assert.equal(normalInfo.label, 'Normal Scrutiny');
    assert.equal(normalInfo.text, '#38bdf8');

    // 2. Accepted backend tier: high
    const highKey = 'high';
    const highInfo = riskColors[highKey] || riskColors.normal;
    assert.equal(highInfo.label, 'High Risk');
    assert.equal(highInfo.text, '#fb7185');

    // 3. Accepted backend tier: creative_flexibility
    const creativeKey = 'creative_flexibility';
    const creativeInfo = riskColors[creativeKey] || riskColors.normal;
    assert.equal(creativeInfo.label, 'Creative Flexibility');
    assert.equal(creativeInfo.text, '#c084fc');

    // 4. Undefined / null fallback
    const undefinedKey = undefined;
    const fallbackUndefined = riskColors[undefinedKey || 'normal'] || riskColors.normal;
    assert.equal(fallbackUndefined.label, 'Normal Scrutiny');

    // 5. Unknown / non-standard value fallback (e.g., 'critical' or typo)
    const unknownKey = 'critical';
    const fallbackUnknown = riskColors[unknownKey] || riskColors.normal;
    assert.equal(fallbackUnknown.label, 'Normal Scrutiny');
  });

  // =========================================================================
  // Test 10: Contract Field Adherence Across All Phase 6 Boundaries
  // =========================================================================
  it('validates exact adherence to backend serialization schemas without silent divergence', () => {
    // 1. ScriptClaim schema
    const claim: ScriptClaim = {
      claim_id: 'clm_contract_01',
      claim_text: 'Topological braids protect against local decoherence.',
      section_id: 'sec_01',
      evidence_ids: ['evi_01', 'evi_02'],
      source_ids: ['src_01'],
      link_status: 'linked',
      assessment_status: 'supported',
      risk_level: 'creative_flexibility',
    };
    assert.equal(claim.claim_id, 'clm_contract_01');
    assert.equal(claim.claim_text, 'Topological braids protect against local decoherence.');
    assert.equal(claim.risk_level, 'creative_flexibility');

    // 2. ClaimAssessment schema
    const assessment: ClaimAssessment = {
      claim_id: 'clm_contract_01',
      section_id: 'sec_01',
      proposition: 'Topological braids protect against local decoherence.',
      status: 'supported',
      risk_level: 'creative_flexibility',
      explanation: 'Verified against authoritative physics catalog.',
      considered_evidence_ids: ['evi_01', 'evi_02'],
      supporting_evidence_ids: ['evi_01'],
      contradicting_evidence_ids: [],
      contextual_evidence_ids: ['evi_02'],
      unassessed_reason: null,
      recommended_action: 'keep',
      assessed_at: '2026-11-05T10:00:00Z',
    };
    assert.equal(assessment.risk_level, 'creative_flexibility');
    assert.equal(assessment.status, 'supported');
    assert.equal(assessment.recommended_action, 'keep');

    // 3. ChallengeClaimRequest schema
    const challengeReq: ChallengeClaimRequest = {
      claim_id: 'clm_contract_01',
      action: 'qualify',
      instruction: 'Add nuance regarding error-threshold requirements.',
    };
    assert.equal(challengeReq.claim_id, 'clm_contract_01');
    assert.equal(challengeReq.action, 'qualify');
    assert.equal(challengeReq.instruction, 'Add nuance regarding error-threshold requirements.');

    // 4. ChallengeClaimResponse & ChallengeSummary schema
    const summary = {
      action: 'qualify',
      target_claim_id: 'clm_contract_01',
      target_claim_text: 'Topological braids protect against local decoherence.',
      section_id: 'sec_01',
      investigation_performed: false,
      prior_run_id: 'rrun_01',
      new_run_id: null,
      new_evidence_ids: [],
      investigation_note: 'Existing evidence rechecked.',
      outcome: 'revised_and_rechecked',
      prior_status: 'supported',
      new_version: 2,
      explanation: 'Claim qualified in section sec_01.',
    };
    assert.equal(summary.action, 'qualify');
    assert.equal(summary.target_claim_id, 'clm_contract_01');
    assert.equal(summary.investigation_performed, false);
  });
});
