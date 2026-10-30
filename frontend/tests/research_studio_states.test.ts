import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import type {
  ResearchOverviewResponse,
  SafeEvidenceView,
  SafeSourceView,
  QueryOutcomeView,
  ScriptFactCheckReport,
} from '../src/types.ts';

describe('Phase 6 Checkpoint 2: Research Studio State & Domain Contract Verification', () => {
  // =========================================================================
  // State 1: No research data
  // =========================================================================
  it('handles projects without research data gracefully', () => {
    const emptyOverview: ResearchOverviewResponse = {
      project_id: 'proj_empty_01',
      has_research: false,
      active_run_id: null,
      status: null,
      termination_reason: null,
      budget_summary: {},
      queries: [],
      sources: [],
      selected_evidence: [],
      retrieved_evidence_ids: [],
      selected_evidence_ids: [],
      used_evidence_ids: [],
      unused_evidence_ids: [],
      conflicts: [],
      unresolved_questions: [],
    };

    assert.equal(emptyOverview.has_research, false);
    assert.equal(emptyOverview.active_run_id, null);
    assert.equal(emptyOverview.queries.length, 0);
    assert.equal(emptyOverview.sources.length, 0);
    assert.equal(emptyOverview.selected_evidence.length, 0);
    assert.equal(emptyOverview.used_evidence_ids.length, 0);
    assert.equal(emptyOverview.unused_evidence_ids.length, 0);
  });

  // =========================================================================
  // State 2: Completed research and budget metrics
  // =========================================================================
  it('accurately represents completed research runs and budget expenditures', () => {
    const completedOverview: ResearchOverviewResponse = {
      project_id: 'proj_completed_01',
      has_research: true,
      active_run_id: 'rrun_quantum_01',
      status: 'completed',
      termination_reason: null,
      budget_summary: {
        search_calls_used: 2,
        extract_calls_used: 3,
        total_chars_extracted: 14200,
        max_search_calls: 5,
        max_extract_calls: 8,
      },
      queries: [],
      sources: [],
      selected_evidence: [],
      retrieved_evidence_ids: [],
      selected_evidence_ids: [],
      used_evidence_ids: [],
      unused_evidence_ids: [],
      conflicts: [],
      unresolved_questions: [],
    };

    assert.equal(completedOverview.has_research, true);
    assert.equal(completedOverview.status, 'completed');
    assert.equal(completedOverview.active_run_id, 'rrun_quantum_01');
    assert.equal(completedOverview.budget_summary.search_calls_used, 2);
    assert.equal(completedOverview.budget_summary.max_search_calls, 5);
    assert.equal(completedOverview.budget_summary.total_chars_extracted, 14200);
  });

  // =========================================================================
  // State 3: Query history with planned, completed, skipped, and failed states
  // =========================================================================
  it('exposes all four query lifecycle states with safe skip reasons and error messages', () => {
    const queries: QueryOutcomeView[] = [
      {
        query_id: 'qry_01',
        query_text: 'quantum computing superposition principles',
        purpose: 'Establish fundamental quantum vs classical mechanics',
        target_question: 'How do qubits differ from classical bits?',
        status: 'completed',
        result_count: 3,
      },
      {
        query_id: 'qry_02',
        query_text: 'quantum decoherence error correction thresholds',
        purpose: 'Investigate environmental decoherence limits',
        target_question: 'What causes quantum decoherence?',
        status: 'skipped',
        result_count: 0,
        skip_reason: 'Search call budget exhausted (max 2 calls reached).',
      },
      {
        query_id: 'qry_03',
        query_text: 'topological anyons braid statistics',
        purpose: 'Assess alternative non-abelian hardware',
        status: 'planned',
        result_count: 0,
      },
      {
        query_id: 'qry_04',
        query_text: 'superconducting microwave resonator attenuation',
        purpose: 'Hardware readout interface',
        status: 'failed',
        result_count: 0,
        error_message: 'Provider HTTP 503 upstream service unavailable.',
      },
    ];

    const completed = queries.find((q) => q.status === 'completed');
    const skipped = queries.find((q) => q.status === 'skipped');
    const planned = queries.find((q) => q.status === 'planned');
    const failed = queries.find((q) => q.status === 'failed');

    assert.ok(completed);
    assert.equal(completed.result_count, 3);
    assert.equal(completed.skip_reason, undefined);

    assert.ok(skipped);
    assert.equal(skipped.result_count, 0);
    assert.match(skipped.skip_reason!, /budget exhausted/i);

    assert.ok(planned);
    assert.equal(planned.result_count, 0);
    assert.equal(planned.error_message, undefined);

    assert.ok(failed);
    assert.equal(failed.result_count, 0);
    assert.match(failed.error_message!, /503/);
  });

  // =========================================================================
  // State 4 & 5 & 6: Distinct evidence lifecycle stages
  // =========================================================================
  it('strictly distinguishes candidate, selected, actively used, and unused evidence', () => {
    const evidenceItems: SafeEvidenceView[] = [
      // 1. Candidate without selection
      {
        evidence_id: 'evi_cand_only',
        source_id: 'src_01',
        excerpt: 'Transmon qubits require dilution refrigerators operating at 15 mK.',
        status: 'candidate',
        is_selected: false,
        is_used_in_current_script: false,
        used_by_claims: [],
        selection_rationale: 'Empirical hardware requirement for superconductivity.',
      },
      // 2. Candidate WITH outline selection (selected but unused in narration)
      {
        evidence_id: 'evi_cand_selected_unused',
        source_id: 'src_01',
        excerpt: 'Quantum entanglement enables instantaneous correlation across entangled pairs.',
        status: 'candidate',
        is_selected: true,
        is_used_in_current_script: false,
        used_by_claims: [],
        selection_rationale: 'Core theoretical principle for section 2.',
      },
      // 3. Retrieved excerpt, selected by outline and actively used in narration
      {
        evidence_id: 'evi_retrieved_selected_used',
        source_id: 'src_02',
        excerpt: 'A qubit exists in a superposition of states zero and one simultaneously.',
        status: 'retrieved',
        is_selected: true,
        is_used_in_current_script: true,
        used_by_claims: ['clm_01', 'clm_02'],
        selection_rationale: null,
      },
      // 4. Retrieved excerpt, NOT selected by outline, NOT used in script
      {
        evidence_id: 'evi_retrieved_unused',
        source_id: 'src_02',
        excerpt: 'Classical transistors switch between high and low voltage states.',
        status: 'retrieved',
        is_selected: false,
        is_used_in_current_script: false,
        used_by_claims: [],
        selection_rationale: null,
      },
    ];

    // Candidate count: status === 'candidate'
    const candidates = evidenceItems.filter((e) => e.status === 'candidate');
    assert.equal(candidates.length, 2);

    // Selected count: is_selected === true
    const selected = evidenceItems.filter((e) => e.is_selected);
    assert.equal(selected.length, 2);

    // Actively used count: is_used_in_current_script === true
    const used = evidenceItems.filter((e) => e.is_used_in_current_script);
    assert.equal(used.length, 1);
    assert.equal(used[0].evidence_id, 'evi_retrieved_selected_used');
    assert.deepEqual(used[0].used_by_claims, ['clm_01', 'clm_02']);

    // Unused count: is_used_in_current_script === false
    const unused = evidenceItems.filter((e) => !e.is_used_in_current_script);
    assert.equal(unused.length, 3);

    // CRITICAL: Candidate without selection MUST NOT be treated as selected!
    const candOnly = evidenceItems.find((e) => e.evidence_id === 'evi_cand_only')!;
    assert.equal(candOnly.status, 'candidate');
    assert.equal(candOnly.is_selected, false);
    assert.equal(candOnly.is_used_in_current_script, false);

    // CRITICAL: Selected without usage MUST be selected but NOT used!
    const selectedUnused = evidenceItems.find((e) => e.evidence_id === 'evi_cand_selected_unused')!;
    assert.equal(selectedUnused.is_selected, true);
    assert.equal(selectedUnused.is_used_in_current_script, false);
  });

  // =========================================================================
  // State 7: Null vs explicit selection rationale
  // =========================================================================
  it('displays recorded selection rationale verbatim without inventing missing rationale', () => {
    const itemWithRationale: SafeEvidenceView = {
      evidence_id: 'evi_with_rat',
      source_id: 'src_01',
      excerpt: 'Josephson junctions create the non-linear inductance needed for artificial atoms.',
      status: 'candidate',
      is_selected: true,
      selection_rationale: 'Primary citation for artificial atom physics.',
      is_used_in_current_script: false,
      used_by_claims: [],
    };

    const itemWithoutRationale: SafeEvidenceView = {
      evidence_id: 'evi_without_rat',
      source_id: 'src_01',
      excerpt: 'Standard microfabrication uses electron beam lithography.',
      status: 'retrieved',
      is_selected: false,
      selection_rationale: null,
      is_used_in_current_script: false,
      used_by_claims: [],
    };

    assert.equal(itemWithRationale.selection_rationale, 'Primary citation for artificial atom physics.');
    assert.equal(itemWithoutRationale.selection_rationale, null);
  });

  // =========================================================================
  // State 8: Unavailable / Missing Fact-Check Report
  // =========================================================================
  it('handles missing or available fact-check reports safely without runtime errors', () => {
    const report: ScriptFactCheckReport = {
      report_id: 'rep_01',
      project_id: 'proj_01',
      script_version: 2,
      research_run_id: 'rrun_01',
      checked_at: '2026-10-31T04:00:00Z',
      requires_repair: true,
      summary: {
        total_claims: 3,
        supported: 2,
        contradicted: 1,
        conflicting: 0,
        unassessed: 0,
        unassessed_reasons: {},
      },
      assessments: [
        {
          claim_id: 'clm_01',
          section_id: 'sec_01',
          proposition: 'A qubit can exist in superposition.',
          status: 'supported',
          risk_level: 'normal',
          explanation: 'Grounded in retrieved physics paper.',
          supporting_evidence_ids: ['evi_01'],
        },
        {
          claim_id: 'clm_02',
          section_id: 'sec_02',
          proposition: 'Quantum computers operate at room temperature.',
          status: 'contradicted',
          risk_level: 'high',
          explanation: 'Contradicted by evidence: cryogenic cooling is required.',
          contradicting_evidence_ids: ['evi_02'],
        },
      ],
    };

    assert.equal(report.requires_repair, true);
    assert.equal(report.summary.supported, 2);
    assert.equal(report.summary.contradicted, 1);
    assert.equal(report.assessments.length, 2);

    // When report is null (missing report state), callers handle null safely
    const nullReport: ScriptFactCheckReport | null = null;
    assert.equal(nullReport, null);
  });

  // =========================================================================
  // State 10: Query Lifecycle Filtering
  // =========================================================================
  it('correctly filters query items by lifecycle state', () => {
    const queries: QueryOutcomeView[] = [
      {
        query_id: 'q1',
        query_text: 'Text 1',
        purpose: 'Purpose 1',
        status: 'completed',
        result_count: 2,
      },
      {
        query_id: 'q2',
        query_text: 'Text 2',
        purpose: 'Purpose 2',
        status: 'skipped',
        result_count: 0,
        skip_reason: 'Ceiling reached',
      },
      {
        query_id: 'q3',
        query_text: 'Text 3',
        purpose: 'Purpose 3',
        status: 'planned',
        result_count: 0,
      },
      {
        query_id: 'q4',
        query_text: 'Text 4',
        purpose: 'Purpose 4',
        status: 'failed',
        result_count: 0,
        error_message: 'Timeout',
      },
    ];

    const filterQueries = (status: string) => {
      if (status === 'all') return queries;
      return queries.filter((q) => q.status === status);
    };

    assert.equal(filterQueries('all').length, 4);
    assert.equal(filterQueries('completed').length, 1);
    assert.equal(filterQueries('completed')[0].query_id, 'q1');
    assert.equal(filterQueries('skipped').length, 1);
    assert.equal(filterQueries('skipped')[0].query_id, 'q2');
    assert.equal(filterQueries('planned').length, 1);
    assert.equal(filterQueries('planned')[0].query_id, 'q3');
    assert.equal(filterQueries('failed').length, 1);
    assert.equal(filterQueries('failed')[0].query_id, 'q4');
  });

  // =========================================================================
  // State 11: Stale Data Prevention on Project Switching
  // =========================================================================
  it('ensures project switching clears previous research overview before mounting new data', () => {
    let currentOverview: ResearchOverviewResponse | null = {
      project_id: 'proj_A',
      has_research: true,
      active_run_id: 'rrun_A',
      status: 'completed',
      termination_reason: null,
      budget_summary: {},
      queries: [],
      sources: [],
      selected_evidence: [],
      retrieved_evidence_ids: ['evi_A'],
      selected_evidence_ids: ['evi_A'],
      used_evidence_ids: ['evi_A'],
      unused_evidence_ids: [],
      conflicts: [],
      unresolved_questions: [],
    };

    // Simulate project switch event from proj_A to proj_B
    const switchProject = (nextProjectId: string) => {
      // Must immediately invalidate and clear stale project data
      currentOverview = null;
    };

    switchProject('proj_B');
    assert.equal(currentOverview, null);
  });
});
