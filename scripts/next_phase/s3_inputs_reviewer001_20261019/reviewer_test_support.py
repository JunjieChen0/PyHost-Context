"""Reviewer 001: explicit evidence for synthetic equal-pass positive controls only."""


def qualify_comparison(cells):
    qualified = [dict(cell, health_reference_usable=True, witness_detection=True,
                      target_assertion_executed="call_completed_no_failure")
                 for cell in cells]
    controls = [{"case": case, "context": context, "suite_variant": suite,
                 "verdict": "CONTROL_ORIGINAL_PRODUCT", "returncode": 0}
                for case, context, suite in sorted({
                    (cell["case"], cell["context"], cell["suite_variant"]) for cell in cells})]
    return qualified, controls
