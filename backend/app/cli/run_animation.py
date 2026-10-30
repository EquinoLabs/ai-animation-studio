"""CLI entry point for running the animation studio pipeline.

Usage:
    python -m backend.app.cli.run_animation --script data/my_video/script.json --from-stage scene_plan --resume
"""

import argparse
from pathlib import Path
from backend.app.services.pipeline.orchestrator import PipelineOrchestrator


def main():
    parser = argparse.ArgumentParser(description="Run the AI Animation Studio pipeline")
    parser.add_argument("--script", type=Path, required=True, help="Path to script.json")
    parser.add_argument("--from-stage", type=str, default=None, help="Stage name to start/resume from")
    parser.add_argument("--resume", action="store_true", help="Resume from last completed stage")

    args = parser.parse_args()

    orchestrator = PipelineOrchestrator()
    manifest = orchestrator.run_pipeline(
        script_path=args.script,
        from_stage=args.from_stage,
        resume=args.resume,
    )
    print(f"Pipeline run finished: {manifest.run_id}")


if __name__ == "__main__":
    main()
