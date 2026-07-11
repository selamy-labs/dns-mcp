from pathlib import Path


def test_test_workflow_cancels_only_stale_pull_request_runs() -> None:
    workflow = Path(".github/workflows/test.yml").read_text()

    assert (
        "concurrency:\n"
        "  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}\n"
        "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n"
    ) in workflow


def test_vulture_workflow_uses_shared_dead_code_gate() -> None:
    workflow = Path(".github/workflows/vulture-check.yml").read_text()

    assert "uses: selamy-labs/.github/.github/workflows/vulture-check.yml@main" in workflow
    assert "paths: src/ tests/" in workflow
    assert "min-confidence: 80" in workflow
