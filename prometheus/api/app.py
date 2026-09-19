from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from prometheus import __version__
from prometheus.analysis.prompt_builder import build_reconstructed_prompt
from prometheus.analysis.remix import apply_remix
from prometheus.analysis.schema import AnalysisReport
from prometheus.api.dto import build_basic_result_dto, build_result_dto
from prometheus.api.jobs import Job, JobManager
from prometheus.api.payments import PaymentConfig, PaymentError, PaymentPending, PaymentService
from prometheus.config import PrometheusConfig
from prometheus.errors import PrometheusError
from prometheus.pipeline import PrometheusPipeline
from prometheus.storage import prepare_run_directory, prepare_scene_directory
from prometheus.video.probe import probe_video
from prometheus.video.sampler import FrameSampler
from prometheus.video.segmenter import SceneSegmenter
from prometheus.video.tools import VideoToolError, resolve_tool

_ROOT_DIR = Path(__file__).resolve().parents[2]
_WEB_DIR = _ROOT_DIR / "web"
_BUILT_WEB_DIR = _ROOT_DIR / "web_dist"
_ALLOWED_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm")
_SCENE_STAGE_RE = re.compile(r"Analyzing scene (\d+) of (\d+)")
_DEFAULT_NIMIQ_RECIPIENT = "NQ43 HJUE 9G1D 5LQF C752 5T7H EJ9M 4QEM 1CB1"
_DEFAULT_NIMIQ_RPC_URL = "https://rpc.testnet.nimiqwatch.com/"
_DEFAULT_NIMIQ_AMOUNT_LUNA = 1_000_000
_STAGED_UPLOAD_LIFETIME_SECONDS = 60 * 60
_UPLOAD_ATTEMPT_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_UPLOAD_PING_MAX_BYTES = 64 * 1024
_LOG = logging.getLogger("prometheus.api")


def _resolve_provider() -> str:
    env = os.environ.get("PROMETHEUS_PROVIDER")
    if env:
        return env
    if (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("PROMETHEUS_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    ):
        return "gemini"
    return "mock"


def _provider_is_ready(provider: str) -> bool:
    env_names = {
        "gemini": ("PROMETHEUS_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "openai": ("PROMETHEUS_OPENAI_API_KEY", "OPENAI_API_KEY"),
    }
    return any(os.environ.get(name) for name in env_names.get(provider, ()))


def _storage_path(value: Path | str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else _ROOT_DIR / path).resolve()


def _cors_origins(value: str | None) -> list[str]:
    return [origin.strip().rstrip("/") for origin in (value or "").split(",") if origin.strip()]


def _verify_writable_directory(path: Path) -> None:
    """Prove the local work directory can be used, without retaining data."""
    if not path.is_dir():
        raise OSError("required work directory is unavailable")
    probe = path / f".prometheus-ready-{uuid.uuid4().hex}"
    try:
        with probe.open("xb"):
            pass
    except OSError as exc:
        raise OSError("required work directory is not writable") from exc
    finally:
        probe.unlink(missing_ok=True)


def _verify_video_tool(name: str) -> None:
    executable = resolve_tool(name)
    try:
        subprocess.run(
            [executable, "-version"],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VideoToolError(f"{name} executable is unavailable") from exc


def _safe_upload_attempt_id(value: str | None) -> str:
    if value and _UPLOAD_ATTEMPT_ID_RE.fullmatch(value):
        return value
    return "missing" if not value else "invalid"


def _safe_origin(value: str | None) -> str:
    if not value:
        return "missing"
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "invalid"


def _log_upload_lifecycle(attempt_id: str, origin: str, lifecycle: str, status: int | None = None) -> None:
    fields = (
        f"upload_attempt={attempt_id} timestamp={datetime.now(timezone.utc).isoformat()} "
        f"reached_fastapi=true origin={origin} lifecycle={lifecycle}"
    )
    if status is not None:
        fields = f"{fields} status={status}"
    _LOG.info(fields)


def _log_upload_ping_lifecycle(ping_id: str, origin: str, lifecycle: str, status: int | None = None) -> None:
    fields = (
        f"upload_ping={ping_id} timestamp={datetime.now(timezone.utc).isoformat()} "
        f"reached_fastapi=true origin={origin} lifecycle={lifecycle}"
    )
    if status is not None:
        fields = f"{fields} status={status}"
    _LOG.info(fields)


def create_app(
    provider: str | None = None,
    model: str | None = None,
    output_dir: Path | str | None = None,
    upload_dir: Path | str | None = None,
    max_upload_bytes: int = 200 * 1024 * 1024,
    require_payment: bool | None = None,
    payment_service: PaymentService | None = None,
) -> FastAPI:
    provider = provider or _resolve_provider()
    output_root = _storage_path(output_dir or os.environ.get("PROMETHEUS_API_OUTPUT_DIR", "api_output"))
    upload_root = _storage_path(upload_dir or os.environ.get("PROMETHEUS_API_UPLOAD_DIR", "uploads"))
    output_root.mkdir(parents=True, exist_ok=True)
    upload_root.mkdir(parents=True, exist_ok=True)
    if require_payment is None:
        require_payment = provider != "mock"
    payment_network = os.environ.get("PROMETHEUS_NIMIQ_NETWORK", "testnet")
    if payment_network != "testnet":
        raise RuntimeError("Prometheus Mini App development is restricted to Nimiq testnet.")
    payment_service = payment_service or PaymentService(
        PaymentConfig(
            recipient=os.environ.get("PROMETHEUS_NIMIQ_RECIPIENT", _DEFAULT_NIMIQ_RECIPIENT),
            amount_luna=int(os.environ.get("PROMETHEUS_NIMIQ_AMOUNT_LUNA", _DEFAULT_NIMIQ_AMOUNT_LUNA)),
            rpc_url=os.environ.get("PROMETHEUS_NIMIQ_RPC_URL", _DEFAULT_NIMIQ_RPC_URL),
            network=payment_network,
            confirmations=int(os.environ.get("PROMETHEUS_NIMIQ_CONFIRMATIONS", "1")),
        ),
        database=os.environ.get("PROMETHEUS_PAYMENT_DB", str(output_root / "payments.db")),
    )

    app = FastAPI(title="Prometheus", version=__version__, docs_url="/api/docs")
    app.state.prometheus_startup_complete = False
    cors_origins = _cors_origins(os.environ.get("PROMETHEUS_CORS_ORIGINS"))
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type"],
        )
    manager = JobManager(max_workers=1)
    staged_uploads: dict[str, tuple[str, Path, float]] = {}
    staged_uploads_lock = threading.Lock()

    @app.on_event("startup")
    async def mark_startup_complete() -> None:
        app.state.prometheus_startup_complete = True

    @app.middleware("http")
    async def log_inspection_upload(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/upload-ping":
            ping_id = _safe_upload_attempt_id(request.query_params.get("upload_ping_id"))
            origin = _safe_origin(request.headers.get("origin"))
            _log_upload_ping_lifecycle(ping_id, origin, "received")
            try:
                response = await call_next(request)
            except BaseException:
                _log_upload_ping_lifecycle(ping_id, origin, "interrupted")
                raise
            _log_upload_ping_lifecycle(ping_id, origin, "response", response.status_code)
            return response

        if request.method != "POST" or request.url.path != "/api/inspect":
            return await call_next(request)

        attempt_id = _safe_upload_attempt_id(request.query_params.get("upload_attempt_id"))
        origin = _safe_origin(request.headers.get("origin"))
        _log_upload_lifecycle(attempt_id, origin, "received")
        try:
            response = await call_next(request)
        except BaseException:
            _log_upload_lifecycle(attempt_id, origin, "interrupted")
            raise
        _log_upload_lifecycle(attempt_id, origin, "response", response.status_code)
        return response

    def _cleanup_staged_uploads() -> None:
        cutoff = time.time() - _STAGED_UPLOAD_LIFETIME_SECONDS
        with staged_uploads_lock:
            expired = [
                staged_uploads.pop(upload_id)
                for upload_id, upload in list(staged_uploads.items())
                if upload[2] < cutoff
            ]
        for _, path, _ in expired:
            path.unlink(missing_ok=True)

    def _take_staged_upload(upload_id: str) -> tuple[str, Path] | None:
        _cleanup_staged_uploads()
        with staged_uploads_lock:
            upload = staged_uploads.pop(upload_id, None)
        if upload is None:
            return None
        return upload[0], upload[1]

    def _make_config(job_id: str) -> PrometheusConfig:
        config = PrometheusConfig()
        config.analyzer.provider = provider
        config.analyzer.model = model
        config.output.directory = output_root / job_id
        return config

    def _run_job(job: Job, video_path: Path) -> None:
        def _progress(stage: str) -> None:
            fields: dict[str, Any] = {"stage": stage}
            match = _SCENE_STAGE_RE.match(stage)
            if match:
                fields["stage"] = f"Analyzing scenes ({match.group(1)}/{match.group(2)})"
            manager.update(job.id, **fields)

        try:
            config = _make_config(job.id)
            pipeline = PrometheusPipeline(config=config)
            result = pipeline.run(video_path, progress=_progress)
            shutil.copyfile(video_path, result.output.run_dir / job.source_file)
            if require_payment:
                payment_service.finalize(job.id)
            manager.update(job.id, run_dir=result.output.run_dir, state="complete", stage="Complete")
        except Exception:
            shutil.rmtree(output_root / job.id, ignore_errors=True)
            raise
        finally:
            video_path.unlink(missing_ok=True)

    def _run_basic_job(job: Job, video_path: Path) -> None:
        try:
            config = _make_config(job.id)
            manager.update(job.id, stage="Inspecting video")
            metadata = probe_video(video_path)
            run_dir, _ = prepare_run_directory(video_path, config.output.directory)
            manager.update(job.id, stage="Detecting scene cuts")
            scenes = SceneSegmenter(config.segmentation).segment(metadata)
            sampler = FrameSampler(config.sampling)
            scene_data = []
            for scene in scenes:
                scene_dir = prepare_scene_directory(run_dir, scene)
                frames = sampler.sample_range(
                    metadata, scene.start, scene.end, 1, scene_dir / "frames"
                )
                scene_data.append({**scene.to_dict(), "frames": [frame.to_dict() for frame in frames]})
            (run_dir / "basic.json").write_text(
                json.dumps({"video": metadata.to_dict(), "scenes": scene_data}, indent=2),
                encoding="utf-8",
            )
            shutil.copyfile(video_path, run_dir / job.source_file)
            manager.update(job.id, run_dir=run_dir, state="complete", stage="Complete")
        except Exception:
            shutil.rmtree(output_root / job.id, ignore_errors=True)
            raise
        finally:
            video_path.unlink(missing_ok=True)

    async def _save_upload(file: UploadFile) -> tuple[str, Path]:
        filename = file.filename or ""
        if not filename.lower().endswith(_ALLOWED_EXTENSIONS):
            raise HTTPException(status_code=400, detail="Unsupported file type. Upload an MP4 video.")
        suffix = Path(filename).suffix.lower()
        destination = upload_root / f"{uuid.uuid4().hex}{suffix}"
        size = 0
        try:
            with destination.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_upload_bytes:
                        limit_mb = max_upload_bytes / 1024 / 1024
                        raise HTTPException(
                            status_code=413,
                            detail=f"Video exceeds the {limit_mb:g} MB limit.",
                        )
                    out.write(chunk)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        if size == 0:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        return filename, destination

    async def _validate_upload(destination: Path) -> None:
        try:
            await run_in_threadpool(probe_video, destination)
        except VideoToolError as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail=f"Video validation is unavailable: {exc}")
        except PrometheusError as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=f"Invalid video: {exc}")
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail=f"Video validation is unavailable: {exc}")

    @app.get("/api/health")
    def health() -> dict:
        analyzer_config = _make_config("health").analyzer
        return {
            "status": "ok",
            "version": __version__,
            "analyzer": {
                "provider": provider,
                "model": analyzer_config.resolved_model(),
            },
            "payment_required": require_payment,
        }

    @app.get("/api/ready")
    def ready() -> dict:
        """Check only local prerequisites for accepting a first upload."""
        if not app.state.prometheus_startup_complete:
            raise HTTPException(status_code=503, detail="Service startup is still in progress.")
        try:
            _verify_writable_directory(upload_root)
            _verify_writable_directory(output_root)
            _verify_video_tool("ffmpeg")
            _verify_video_tool("ffprobe")
            if manager is None:
                raise RuntimeError("analysis job manager is unavailable")
            config = _make_config("ready")
            if not config.analyzer.provider or not config.output.directory:
                raise RuntimeError("required analysis configuration is unavailable")
        except (OSError, RuntimeError, VideoToolError) as exc:
            raise HTTPException(status_code=503, detail=f"Service is not ready: {exc}") from exc
        return {"status": "ready"}

    @app.post("/api/upload-ping", status_code=204)
    async def upload_ping(file: UploadFile | None = File(default=None)) -> Response:
        """Prove the multipart POST path without creating work or storing media."""
        if file is not None:
            size = 0
            while chunk := await file.read(8192):
                size += len(chunk)
                if size > _UPLOAD_PING_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="Upload ping is limited to 64 KB.")
        return Response(status_code=204)

    @app.get("/api/payments/config")
    def payment_config() -> dict:
        config = payment_service.public_config()
        config["enabled"] = bool(require_payment and _provider_is_ready(provider))
        return config

    @app.post("/api/payments/quotes")
    def create_payment_quote(payload: dict | None = None) -> dict:
        if not require_payment or not _provider_is_ready(provider):
            raise HTTPException(status_code=503, detail="Advanced AI analysis is not configured.")
        source_job_id = payload.get("source_job_id") if isinstance(payload, dict) else None
        source_job = manager.get(source_job_id) if isinstance(source_job_id, str) else None
        if source_job is None or source_job.tier != "basic" or source_job.state != "complete":
            raise HTTPException(status_code=400, detail="A completed basic analysis is required before payment.")
        if source_job.run_dir is None or not (source_job.run_dir / source_job.source_file).is_file():
            raise HTTPException(status_code=404, detail="Basic analysis video file is missing.")
        return payment_service.create_quote(source_job_id)

    @app.post("/api/payments/quotes/{quote_id}/verify")
    def verify_payment_quote(quote_id: str, payload: dict) -> dict:
        tx_hash = payload.get("tx_hash") if isinstance(payload, dict) else None
        if tx_hash is not None and not isinstance(tx_hash, str):
            raise HTTPException(status_code=400, detail="Transaction hash must be a string.")
        try:
            return payment_service.verify(quote_id, tx_hash)
        except PaymentPending as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except PaymentError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/uploads", status_code=201)
    async def stage_upload(file: UploadFile = File(...)) -> dict:
        filename, destination = await _save_upload(file)
        await _validate_upload(destination)
        _cleanup_staged_uploads()
        upload_id = uuid.uuid4().hex
        with staged_uploads_lock:
            staged_uploads[upload_id] = (filename, destination, time.time())
        return {
            "upload_id": upload_id,
            "name": filename,
            "size": destination.stat().st_size,
        }

    @app.post("/api/inspect", status_code=202)
    async def inspect(file: UploadFile = File(...)) -> dict:
        filename, destination = await _save_upload(file)
        await _validate_upload(destination)
        job = manager.create(
            video_name=filename,
            tier="basic",
            source_file=f"source{destination.suffix.lower()}",
        )
        manager.submit(job.id, lambda: _run_basic_job(job, destination))
        return {"job_id": job.id}

    @app.post("/api/analyze", status_code=202)
    async def analyze(
        file: UploadFile | None = File(default=None),
        upload_id: str | None = Form(default=None),
        source_job_id: str | None = Form(default=None),
        payment_quote_id: str | None = Form(default=None),
        payment_token: str | None = Form(default=None),
    ) -> dict:
        if require_payment:
            source_job = manager.get(source_job_id) if source_job_id else None
            if source_job is None or source_job.tier != "basic" or source_job.state != "complete":
                raise HTTPException(status_code=400, detail="A completed basic analysis is required before advanced analysis.")
            source_path = source_job.run_dir / source_job.source_file if source_job.run_dir else None
            if source_path is None or not source_path.is_file():
                raise HTTPException(status_code=404, detail="Basic analysis video file is missing.")
            job = manager.create(video_name=source_job.video_name, tier="advanced", source_file=source_job.source_file)
            try:
                reserved_job_id = payment_service.reserve(
                    payment_quote_id or "", payment_token or "", source_job_id, job.id
                )
            except PaymentError as exc:
                manager.remove(job.id)
                raise HTTPException(status_code=402, detail=str(exc))
            if reserved_job_id != job.id:
                manager.remove(job.id)
                job = manager.get(reserved_job_id)
                if job is None:
                    raise HTTPException(status_code=409, detail="Payment is tied to an analysis job that is unavailable. Do not pay again; restore the server job state.")
                if job.state != "error":
                    return {"job_id": job.id}
                manager.update(job.id, state="processing", stage="Queued", error=None)
            destination = upload_root / f"{uuid.uuid4().hex}{source_path.suffix.lower()}"
            try:
                shutil.copyfile(source_path, destination)
                manager.submit(job.id, lambda: _run_job(job, destination))
            except Exception as exc:
                destination.unlink(missing_ok=True)
                manager.update(job.id, state="error", error=str(exc))
                raise HTTPException(status_code=503, detail=f"Could not start analysis for this payment: {exc}")
            return {"job_id": job.id}

        if file is None and not upload_id:
            raise HTTPException(status_code=400, detail="A staged video upload is required.")

        if upload_id:
            job = manager.create(video_name="Staged video", tier="advanced")
            staged = _take_staged_upload(upload_id)
            if staged is None:
                manager.remove(job.id)
                raise HTTPException(
                    status_code=404,
                    detail="Staged upload is unavailable. Upload the video again.",
                )
            filename, destination = staged
            job.video_name = filename
            job.source_file = f"source{destination.suffix.lower()}"
        else:
            filename, destination = await _save_upload(file)
            await _validate_upload(destination)
            job = manager.create(
                video_name=filename,
                tier="advanced",
                source_file=f"source{destination.suffix.lower()}",
            )

        try:
            manager.submit(job.id, lambda: _run_job(job, destination))
        except Exception:
            manager.remove(job.id)
            destination.unlink(missing_ok=True)
            raise
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job.")
        return manager.public_info(job)

    @app.get("/api/jobs/{job_id}/result")
    def job_result(job_id: str) -> dict:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job.")
        if job.state == "error":
            raise HTTPException(status_code=502, detail=job.error or "Analysis failed.")
        if job.state != "complete" and job.run_dir is None:
            raise HTTPException(status_code=409, detail="Analysis is still running.")
        if job.tier == "basic":
            return build_basic_result_dto(
                job.id, job.run_dir, job.video_name, job.source_file
            )
        return build_result_dto(job.id, job.run_dir, job.video_name, job.source_file)

    @app.post("/api/jobs/{job_id}/remix")
    def job_remix(job_id: str, payload: dict) -> dict:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job.")
        if job.state == "error":
            raise HTTPException(status_code=400, detail=job.error or "Analysis failed.")
        if job.state != "complete" or job.run_dir is None:
            raise HTTPException(status_code=409, detail="Analysis is still running.")
        if job.tier != "advanced":
            raise HTTPException(status_code=403, detail="Remix requires advanced analysis.")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Remix body must be a JSON object.")
        try:
            overrides = {
                str(key): value if isinstance(value, str) else str(value)
                for key, value in payload.items()
            }
            analysis_path = job.run_dir / "analysis.json"
            report = AnalysisReport.from_dict(json.loads(analysis_path.read_text(encoding="utf-8")))
            remixed, applied = apply_remix(report, overrides)
            prompt_markdown = build_reconstructed_prompt(remixed)
        except PrometheusError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"applied": applied, "prompt_markdown": prompt_markdown}

    @app.get("/api/jobs/{job_id}/frames/{file_path:path}")
    def job_frame(job_id: str, file_path: str) -> FileResponse:
        job = manager.get(job_id)
        if job is None or job.run_dir is None:
            raise HTTPException(status_code=404, detail="Frames not available.")
        base = job.run_dir.resolve()
        target = (base / file_path).resolve()
        if not target.is_relative_to(base):
            raise HTTPException(status_code=400, detail="Invalid frame path.")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Frame not found.")
        return FileResponse(target)

    static_dir = _BUILT_WEB_DIR if _BUILT_WEB_DIR.is_dir() else _WEB_DIR
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="web")
    return app


app = create_app()
