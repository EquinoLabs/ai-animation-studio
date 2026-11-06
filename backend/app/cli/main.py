"""Command-line interface for AI Animation Studio local project and pipeline management."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import List, Optional
import webbrowser

from backend.app.config import get_settings
from backend.app.domain.project import STAGE_NAMES
from backend.app.services.pipeline.orchestrator import (
    PipelineError,
    PipelineOrchestrator,
    PipelineResult,
    ScriptError,
    ScriptFinalizationError,
    ScriptNotFoundError,
    ScriptStorageError,
    ScriptValidationError,
)
from backend.app.services.project.service import (
    ProjectError,
    ProjectNotFoundError,
    ProjectSecurityError,
    ProjectService,
    ProjectValidationError,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="ai-animation-studio",
        description="Local-first AI Animation Studio project and pipeline management CLI",
    )

    # Primary Operations (mutually exclusive group)
    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument(
        "--new-project",
        action="store_true",
        help="Create a new local project (requires --title)",
    )
    action_group.add_argument(
        "--list-projects",
        action="store_true",
        help="List all existing local projects",
    )
    action_group.add_argument(
        "--script",
        action="store_true",
        help="Run the script generation stage for a project (requires --project)",
    )
    action_group.add_argument(
        "--plan-scenes",
        action="store_true",
        help="Run the scene planning stage for a project (requires --project)",
    )
    action_group.add_argument(
        "--script-to-voice",
        action="store_true",
        help="Run the script-to-voice narration stage for a project (requires --project)",
    )
    action_group.add_argument(
        "--video",
        action="store_true",
        help="Run the video production rendering stage for a project (requires --project)",
    )
    action_group.add_argument(
        "--full",
        action="store_true",
        help="Run the full animation pipeline in dependency order (requires --project)",
    )
    action_group.add_argument(
        "--serve",
        action="store_true",
        help="Start the local API web server on 127.0.0.1:8000",
    )
    action_group.add_argument(
        "--studio",
        action="store_true",
        help="Launch the full local studio (starts backend and frontend UI, opens browser)",
    )
    action_group.add_argument(
        "--script-builder",
        action="store_true",
        help="Open the Script Builder experience in browser (launches studio if not already running)",
    )
    action_group.add_argument(
        "--script-writing",
        action="store_true",
        help="Alias for --script: Run script generation stage for a project (requires --project)",
    )
    action_group.add_argument(
        "--scene-builder",
        action="store_true",
        help="Alias for --plan-scenes: Run scene planning stage for a project (requires --project)",
    )

    # Parameters
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind local API server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind local API server (default: 8000)",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Human-readable title when creating a project",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Project ID (e.g. proj_123456)",
    )
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=None,
        help="Custom root directory holding projects (default: projects/)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format",
    )

    # Script Builder sub-commands
    parser.add_argument(
        "--topic",
        type=str,
        default=None,
        help="Source topic for script generation (used with --script)",
    )
    parser.add_argument(
        "--instruction",
        type=str,
        default=None,
        help="Revision instruction for active script draft (used with --script)",
    )
    parser.add_argument(
        "--accept",
        action="store_true",
        help="Accept active script draft for finalization (used with --script)",
    )
    parser.add_argument(
        "--reject",
        action="store_true",
        help="Reject active script draft (used with --script)",
    )
    parser.add_argument(
        "--reason",
        type=str,
        default=None,
        help="Optional reason when rejecting a draft (used with --reject)",
    )
    parser.add_argument(
        "--finalize",
        action="store_true",
        help="Finalize accepted script draft into an immutable artifact (used with --script)",
    )
    parser.add_argument(
        "--show-script",
        action="store_true",
        help="Show active script draft (used with --script)",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Show conversation event history (used with --script)",
    )
    parser.add_argument(
        "--target-duration",
        type=float,
        default=5.0,
        help="Target video duration in minutes for script generation (default: 5.0)",
    )
    parser.add_argument(
        "--research-run-id",
        type=str,
        default=None,
        help="Explicit research run ID to ground script or outline (used with --script)",
    )
    parser.add_argument(
        "--allow-unresearched",
        action="store_true",
        help="Explicitly authorize unresearched script generation (used with --script)",
    )
    parser.add_argument(
        "--create-outline",
        action="store_true",
        help="Generate standalone outline without drafting narration (used with --script and --topic)",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["mock", "ollama"],
        default=None,
        help="LLM script generation provider: 'mock' (deterministic offline) or 'ollama' (local model)",
    )

    return parser


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is currently open and listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _launch_studio(host: str = "127.0.0.1", backend_port: int = 8000, frontend_port: int = 5173, open_browser: bool = True) -> int:
    """Launch the local development studio: backend API and frontend dev server."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    venv_python = repo_root / "backend" / ".venv" / "Scripts" / "python.exe"
    py_exec = str(venv_python) if venv_python.exists() else sys.executable

    print("=================================================================", flush=True)
    print("           AI ANIMATION STUDIO — LOCAL STUDIO LAUNCHER           ", flush=True)
    print("=================================================================", flush=True)

    started_procs: List[subprocess.Popen] = []

    # 1. Backend server check & spawn
    backend_url = f"http://{host}:{backend_port}"
    if _is_port_in_use(backend_port, host):
        print(f"[*] Backend API is already running on {backend_url}", flush=True)
    else:
        print(f"[*] Starting Backend server on {backend_url} ...", flush=True)
        cmd_backend = [py_exec, "-m", "uvicorn", "backend.app.main:app", "--host", host, "--port", str(backend_port)]
        backend_proc = subprocess.Popen(cmd_backend, cwd=str(repo_root))
        started_procs.append(backend_proc)

    # 2. Frontend server check & spawn
    frontend_url = f"http://{host}:{frontend_port}"
    if _is_port_in_use(frontend_port, host):
        print(f"[*] Frontend Studio is already running on {frontend_url}", flush=True)
    else:
        print(f"[*] Starting Frontend Studio on {frontend_url} ...", flush=True)
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        cmd_frontend = [npm_cmd, "--prefix", "frontend", "run", "dev", "--", "--host", host, "--port", str(frontend_port)]
        frontend_proc = subprocess.Popen(cmd_frontend, cwd=str(repo_root))
        started_procs.append(frontend_proc)

    # Allow brief startup time
    time.sleep(1.5)

    print("\n-----------------------------------------------------------------", flush=True)
    print(f"  Studio Web UI:   {frontend_url}", flush=True)
    print(f"  Backend API:     {backend_url}", flush=True)
    print("-----------------------------------------------------------------", flush=True)
    print("Press CTRL+C to stop all studio services.\n", flush=True)

    if open_browser:
        try:
            webbrowser.open(frontend_url)
        except Exception:
            pass

    if not started_procs:
        print("Both services are already active in the background. Press CTRL+C to exit launcher.", flush=True)

    try:
        while True:
            time.sleep(1)
            for p in started_procs:
                if p.poll() is not None:
                    print(f"\n[!] Process PID {p.pid} exited with code {p.returncode}.", flush=True)
                    return p.returncode
    except KeyboardInterrupt:
        print("\n[*] Shutting down studio services...", flush=True)
        for p in started_procs:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    p.terminate()
            except Exception:
                pass
        print("[*] All studio services stopped.", flush=True)
        return 0


def run_cli(args_list: Optional[List[str]] = None) -> int:
    """Execute CLI logic and return an appropriate process exit code."""
    parser = build_parser()
    args = parser.parse_args(args_list)

    import os
    settings = get_settings()
    if args.provider:
        os.environ["ANIM_LLM_SCRIPT_PROVIDER"] = args.provider
        settings.LLM_SCRIPT_PROVIDER = args.provider
    if args.projects_dir:
        settings.PROJECTS_DIR = args.projects_dir

    projects_dir = settings.PROJECTS_DIR
    project_service = ProjectService(projects_root=projects_dir)
    orchestrator = PipelineOrchestrator(project_service=project_service, settings=settings)

    # 1. New Project Command
    if args.new_project:
        if not args.title or not args.title.strip():
            sys.stderr.write("Error: --new-project requires a non-empty --title.\n")
            return 2
        try:
            project = project_service.create_project(
                title=args.title,
                project_id=args.project,
            )
            if args.json:
                print(project.metadata.model_dump_json(indent=2))
            else:
                print(f"Project created successfully: {project.metadata.project_id}")
                print(f"  Title: {project.metadata.title}")
                print(f"  Path:  {project.root_dir}")
            return 0
        except ProjectError as exc:
            sys.stderr.write(f"Error creating project: {exc}\n")
            return 1

    # Incompatible --title with other commands
    if args.title and not args.new_project:
        sys.stderr.write("Error: --title can only be used with --new-project.\n")
        return 2

    # 2. List Projects Command
    if args.list_projects:
        if args.project:
            sys.stderr.write("Error: --list-projects cannot be combined with --project.\n")
            return 2
        projects = project_service.list_projects()
        if args.json:
            print(json.dumps([p.model_dump() for p in projects], default=str, indent=2))
        else:
            if not projects:
                print("No projects found.")
            else:
                print(f"Found {len(projects)} project(s):")
                for p in projects:
                    print(f"  [{p.project_id}] {p.title} (Updated: {p.updated_at.isoformat()})")
        return 0

    # 3. Serve Local API Command
    if args.serve:
        import uvicorn
        print(f"Starting local API server on http://{args.host}:{args.port} ...", flush=True)
        print(f"  Active Script LLM Provider: {settings.LLM_SCRIPT_PROVIDER.upper()}", flush=True)
        if settings.LLM_SCRIPT_PROVIDER == "ollama":
            print(f"  Ollama Target: {settings.OLLAMA_MODEL} at {settings.OLLAMA_HOST}", flush=True)
        uvicorn.run("backend.app.main:app", host=args.host, port=args.port, reload=False)
        return 0

    # 4. Studio / Script Builder Command (Launches frontend and backend together)
    if args.studio or args.script_builder:
        return _launch_studio(host=args.host, backend_port=args.port, open_browser=True)

    # 5. Stage Execution Commands
    is_script = bool(args.script or args.script_writing)
    is_plan_scenes = bool(args.plan_scenes or args.scene_builder)
    stage_flags = {
        "script": is_script,
        "plan-scenes": is_plan_scenes,
        "script-to-voice": args.script_to_voice,
        "video": args.video,
        "full": args.full,
    }
    selected_stage = next((name for name, active in stage_flags.items() if active), None)

    if selected_stage:
        if not args.project:
            flag_name = "script-writing" if args.script_writing else ("scene-builder" if args.scene_builder else selected_stage)
            sys.stderr.write(f"Error: --{flag_name} requires --project <project_id>.\n")
            return 2

        # Script-specific sub-operations
        if is_script:
            has_subop = any([
                args.topic,
                args.instruction,
                args.accept,
                args.reject,
                args.finalize,
                args.show_script,
                args.history,
            ])
            if has_subop:
                try:
                    if args.topic:
                        if args.create_outline:
                            if not args.json:
                                print(f"Generating outline using provider: {settings.LLM_SCRIPT_PROVIDER.upper()}")
                            outline = orchestrator.create_outline(
                                project_id=args.project,
                                topic=args.topic,
                                target_duration_minutes=args.target_duration,
                                title=args.title,
                                research_run_id=args.research_run_id,
                                allow_unresearched=args.allow_unresearched,
                            )
                            if args.json:
                                print(outline.model_dump_json(indent=2))
                            else:
                                print(f"Outline created for project '{args.project}': [{outline.outline_id}] '{outline.topic}'")
                                print(f"  Narrative Structure: {outline.narrative_structure}")
                                print(f"  Sections: {len(outline.sections)}")
                                for s in outline.sections:
                                    print(f"    [{s.type.upper()}] {s.heading} (~{s.target_words} words)")
                            return 0

                        if not args.json:
                            print(f"Generating script using provider: {settings.LLM_SCRIPT_PROVIDER.upper()}")
                            if settings.LLM_SCRIPT_PROVIDER == "ollama":
                                print(f"  Target Ollama Model: {settings.OLLAMA_MODEL} ({settings.OLLAMA_HOST})")
                        script = orchestrator.start_script(
                            project_id=args.project,
                            topic=args.topic,
                            target_duration_minutes=args.target_duration,
                            research_run_id=args.research_run_id,
                            allow_unresearched=args.allow_unresearched,
                        )
                        if args.json:
                            print(script.model_dump_json(indent=2))
                        else:
                            print(f"Draft v{script.version} created for project '{args.project}': '{script.title}'")
                            print(f"  Sections: {len(script.sections)}")
                            for s in script.sections:
                                print(f"    [{s.type.upper()}] {s.heading}: {s.narration[:60]}...")
                        return 0

                    if args.instruction:
                        if not args.json:
                            print(f"Revising script using provider: {settings.LLM_SCRIPT_PROVIDER.upper()}")
                            if settings.LLM_SCRIPT_PROVIDER == "ollama":
                                print(f"  Target Ollama Model: {settings.OLLAMA_MODEL} ({settings.OLLAMA_HOST})")
                        script = orchestrator.revise_script(
                            project_id=args.project,
                            instruction=args.instruction,
                        )
                        if args.json:
                            print(script.model_dump_json(indent=2))
                        else:
                            print(f"Draft v{script.version} created for project '{args.project}': '{script.title}'")
                            print(f"  Sections: {len(script.sections)}")
                            for s in script.sections:
                                print(f"    [{s.type.upper()}] {s.heading}: {s.narration[:60]}...")
                        return 0

                    if args.accept:
                        script = orchestrator.accept_draft(args.project)
                        if args.json:
                            print(script.model_dump_json(indent=2))
                        else:
                            print(f"Draft v{script.version} accepted for project '{args.project}'. Ready for finalization.")
                        return 0

                    if args.reject:
                        script = orchestrator.reject_draft(args.project, reason=args.reason)
                        if args.json:
                            print(script.model_dump_json(indent=2))
                        else:
                            msg = f" Reason: '{args.reason}'" if args.reason else ""
                            print(f"Draft v{script.version} rejected for project '{args.project}'.{msg}")
                        return 0

                    if args.finalize:
                        script = orchestrator.finalize_script(args.project)
                        if args.json:
                            print(script.model_dump_json(indent=2))
                        else:
                            print(f"Script v{script.version} finalized for project '{args.project}': '{script.title}'")
                        return 0

                    if args.show_script:
                        script = orchestrator.get_script(args.project)
                        if args.json:
                            print(script.model_dump_json(indent=2))
                        else:
                            print(f"Script '{script.title}' [v{script.version}, status: {script.status}]")
                            for s in script.sections:
                                print(f"  [{s.type.upper()}] {s.heading}\n    {s.narration}")
                        return 0

                    if args.history:
                        events = orchestrator.get_conversation_history(args.project)
                        if args.json:
                            print(json.dumps([e.model_dump(mode="json") for e in events], indent=2))
                        else:
                            print(f"Conversation history for project '{args.project}' ({len(events)} events):")
                            for e in events:
                                ver = f" (v{e.script_version})" if e.script_version else ""
                                print(f"  [{e.timestamp.strftime('%H:%M:%S')}] {e.event_type}{ver}: {e.content or ''}")
                        return 0

                except (ScriptError, ProjectError, PipelineError) as exc:
                    sys.stderr.write(f"Error: {exc}\n")
                    return 1

        try:
            if selected_stage == "full":
                result: PipelineResult = orchestrator.run_full(args.project)
            else:
                result = orchestrator.run_stage(args.project, selected_stage)

            if args.json:
                out = {
                    "run_id": result.run_id,
                    "target_stage": result.target_stage,
                    "success": result.success,
                    "executed_stages": result.executed_stages,
                    "errors": result.errors,
                    "run_manifest_path": str(result.run_manifest_path),
                }
                print(json.dumps(out, indent=2))
            else:
                if result.success:
                    print(f"Pipeline run '{result.run_id}' completed successfully.")
                    print(f"  Stages executed: {', '.join(result.executed_stages)}")
                    print(f"  Manifest: {result.run_manifest_path}")
                else:
                    sys.stderr.write(f"Pipeline run '{result.run_id}' failed.\n")
                    for err in result.errors:
                        sys.stderr.write(f"  - {err}\n")
                    sys.stderr.write(f"  Manifest: {result.run_manifest_path}\n")

            return 0 if result.success else 1

        except ProjectNotFoundError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return 1
        except ProjectError as exc:
            sys.stderr.write(f"Project Error: {exc}\n")
            return 1
        except PipelineError as exc:
            sys.stderr.write(f"Pipeline Error: {exc}\n")
            return 1
        except Exception as exc:
            sys.stderr.write(f"Unexpected Execution Error: {exc}\n")
            return 1

    return 2


def main() -> None:
    """CLI script entry point."""
    sys.exit(run_cli())


if __name__ == "__main__":
    main()
