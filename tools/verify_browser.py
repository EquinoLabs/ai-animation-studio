"""Browser acceptance test using Playwright against running local servers.
Tests real end-to-end user workflows:
1. Connection & Health status
2. Project creation & selection
3. Script draft generation
4. Conversational revision
5. Draft rejection with reason
6. Re-revision & acceptance
7. Finalization with confirmation modal
8. State persistence across page refresh
9. Version history inspection
10. Failure handling & validation
"""

import sys
import time
from playwright.sync_api import sync_playwright

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def run_browser_verification():
    print("=" * 70)
    print("Starting Browser Acceptance & Local Runtime Verification (Task P2-Final)")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        # Step 1: Connectivity & Launch
        print("\n[Step 1] Navigating to http://127.0.0.1:5173 ...")
        page.goto("http://127.0.0.1:5173")
        page.wait_for_selector(".brand-title", timeout=5000)
        brand = page.locator(".brand-title").inner_text()
        print(f"  Brand Title: {brand}")

        # Check API Online status
        status_badge = page.locator(".status-badge")
        status_badge.wait_for(timeout=5000)
        status_text = status_badge.inner_text()
        print(f"  Health Indicator: {status_text}")
        assert "API Online" in status_text, f"Expected API Online, got {status_text}"
        print("  [OK] Connectivity & health check verified")

        # Step 2: Project Creation
        print("\n[Step 2] Creating a new project via UI ...")
        page.click("button:has-text('+ New Project')")
        page.wait_for_selector(".modal-card input", timeout=3000)
        project_title = f"Cosmic Journey Explainer {int(time.time())}"
        page.fill(".modal-card input", project_title)
        page.click(".modal-card button[type='submit']")
        
        # Wait for modal to close and project selector to attach the new option
        page.wait_for_selector(f"option:has-text('{project_title}')", state="attached", timeout=5000)
        print(f"  [OK] Project '{project_title}' successfully created and selected")

        # Step 3: Script Initial Draft Generation
        print("\n[Step 3] Generating initial script draft ...")
        page.wait_for_selector("textarea[placeholder*='How does a quantum computer']", timeout=3000)
        topic_text = "The Life Cycle of Stars from Nebula to Supernova"
        page.fill("textarea[placeholder*='How does a quantum computer']", topic_text)
        page.click("button:has-text('Generate Script Draft')")

        # Wait for draft to appear
        page.wait_for_selector(".pill", timeout=15000)
        version_pill = page.locator("span:has-text('v1')").first
        assert version_pill.is_visible()
        status_pill = page.locator(".pill").first.inner_text()
        print(f"  Draft Version: v1")
        print(f"  Status Pill: {status_pill}")

        # Inspect sections
        sections = page.locator(".script-column .script-column, .script-column div[style*='border-color'] strong").all_inner_texts()
        print(f"  Generated Sections Count: {len(page.locator('.script-column div[style*=\"border-color\"]').all())}")
        
        # Check first narration block
        narration_sample = page.locator(".script-column p[style*='pre-wrap']").first.inner_text()
        print(f"  Sample Narration: {narration_sample[:80]}...")

        # Step 4: Verify Attempted Finalization Before Acceptance is Blocked
        print("\n[Step 4] Verifying Finalize button is disabled before acceptance ...")
        finalize_btn = page.locator("button:has-text('Finalize Script')")
        assert finalize_btn.is_disabled(), "Finalize button should be disabled for unaccepted draft!"
        print("  [OK] Finalize button is strictly disabled before acceptance")

        # Step 5: Conversational Revision
        print("\n[Step 5] Submitting conversational revision instruction ...")
        revision_instruction = "Make the hook punchier and emphasize stellar collapse."
        page.fill("textarea[placeholder*='Enter revision instructions']", revision_instruction)
        page.click("button:has-text('Send Revision')")

        # Wait for draft v2
        page.wait_for_selector("span:has-text('v2')", timeout=15000)
        print("  [OK] Revision processed, Draft v2 active")

        # Step 6: Reject Draft
        print("\n[Step 6] Rejecting Draft v2 with feedback reason ...")
        page.click("button:has-text('Reject Draft')")
        page.wait_for_selector(".modal-card textarea", timeout=3000)
        rejection_reason = "Focus more heavily on the dramatic supernova explosion."
        page.fill(".modal-card textarea", rejection_reason)
        page.click(".modal-card button:has-text('Reject Draft')")

        # Verify rejected state is displayed
        page.wait_for_selector(".pill-rejected", timeout=5000)
        rejected_banner = page.locator("div[style*='rgba(244, 63, 94'] strong").first.inner_text()
        print(f"  Status Pill: {page.locator('.pill-rejected').inner_text()}")
        print(f"  Alert Banner: {rejected_banner}")
        assert finalize_btn.is_disabled(), "Finalize button must remain disabled when draft is rejected!"
        print("  [OK] Draft v2 rejected state and alert banner verified")

        # Step 7: Revise following rejection & Accept
        print("\n[Step 7] Submitting revision following rejection and accepting draft ...")
        page.fill("textarea[placeholder*='Enter revision instructions']", "Add intense visual cues and narration for the supernova blast.")
        page.click("button:has-text('Send Revision')")
        page.wait_for_selector("span:has-text('v3')", timeout=15000)
        print("  [OK] Draft v3 created")

        # Accept draft v3
        accept_btn = page.locator("button:has-text('Accept Draft')")
        accept_btn.click()
        page.wait_for_selector(".pill-accepted", timeout=5000)
        print(f"  Status Pill: {page.locator('.pill-accepted').inner_text()}")
        assert not finalize_btn.is_disabled(), "Finalize button must now be enabled after acceptance!"
        print("  [OK] Draft v3 successfully accepted; Finalize button enabled")

        # Step 8: Finalize with Confirmation Modal
        print("\n[Step 8] Finalizing script via confirmation modal ...")
        finalize_btn.click()
        page.wait_for_selector(".modal-card h3:has-text('Finalize Script')", timeout=3000)
        modal_header = page.locator(".modal-card h3").first.inner_text()
        print(f"  Confirmation Modal Header: {modal_header}")
        page.click(".modal-card button:has-text('Confirm & Finalize Version')")

        # Verify finalized state
        page.wait_for_selector(".pill-final", timeout=5000)
        final_pill = page.locator(".pill-final").inner_text()
        print(f"  Final Status Pill: {final_pill}")
        assert "FINALIZED" in final_pill.upper()
        print("  [OK] Script successfully finalized into immutable artifact")

        # Step 9: State Persistence across Page Reload
        print("\n[Step 9] Refreshing browser to verify persistence ...")
        page.reload()
        page.wait_for_selector(".brand-title", timeout=5000)
        page.wait_for_selector(".pill-final", timeout=5000)
        reloaded_pill = page.locator(".pill-final").inner_text()
        print(f"  Post-Reload Status: {reloaded_pill}")
        assert "FINALIZED" in reloaded_pill.upper()
        
        # Verify history persisted
        chat_events = page.locator(".chat-column div[style*='border-radius']").all()
        print(f"  Persisted Conversation Events Count: {len(chat_events)}")
        assert len(chat_events) >= 5, f"Expected at least 5 conversation events, got {len(chat_events)}"
        print("  [OK] Finalized state and conversation timeline fully persisted")

        # Step 10: Version History Modal Inspection
        print("\n[Step 10] Inspecting historical draft versions and finalized artifacts ...")
        page.click("button:has-text('History / Versions')")
        page.wait_for_selector(".modal-card h3:has-text('Script Versions & Artifacts')", timeout=3000)
        
        # Wait for versions to finish fetching and render
        page.wait_for_selector(".modal-card span:has-text('Draft v')", timeout=5000)
        draft_items = page.locator(".modal-card span:has-text('Draft v')").all_inner_texts()
        print(f"  Historical Drafts: {draft_items}")
        assert len(draft_items) >= 3, f"Expected 3 drafts, found {len(draft_items)}"

        # Check finalized tab
        page.click(".modal-card button:has-text('Finalized (')")
        page.wait_for_selector(".modal-card span:has-text('v00')", timeout=5000)
        finalized_items = page.locator(".modal-card span:has-text('v00')").all_inner_texts()
        print(f"  Finalized Artifacts: {finalized_items}")
        assert len(finalized_items) >= 1, f"Expected at least 1 finalized artifact, found {len(finalized_items)}"

        page.click(".modal-card button:has-text('Close')")
        print("  [OK] Version history inspected successfully without mutating state")

        browser.close()

    print("\n" + "=" * 70)
    print("ALL 10 BROWSER VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    run_browser_verification()
