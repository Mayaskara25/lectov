import logging
import os

from celery.result import AsyncResult
from flask import Flask, jsonify, request, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from celery_app import celery
from config import settings
from tasks import run_pipeline

log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_bytes

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=settings.redis_url,
)

ALLOWED_EXTENSIONS = {".txt", ".pdf"}
ALLOWED_MIME_TYPES = {"text/plain", "application/pdf"}


def _validate_upload(uploaded_file) -> tuple[str, int] | None:
    """Return (error_message, status_code) if invalid, or None if valid."""
    filename = uploaded_file.filename or ""
    if not filename:
        return "Uploaded file has no filename", 400

    _, ext = os.path.splitext(filename)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        return f"Only {', '.join(ALLOWED_EXTENSIONS)} files are accepted", 415

    mime = uploaded_file.content_type or ""
    if mime and mime.split(";")[0].strip() not in ALLOWED_MIME_TYPES:
        return f"Unexpected MIME type: {mime}", 415

    # Reject path-traversal characters in the original filename component.
    basename = os.path.basename(filename)
    if basename != filename.replace("/", "").replace("\\", ""):
        return "Invalid filename", 400

    return None


@app.route("/")
def home():
    return jsonify({"status": "running"})


@app.route("/generate", methods=["POST"])
@limiter.limit("10 per minute")
def generate():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    uploaded_file = request.files["file"]
    err_result = _validate_upload(uploaded_file)
    if err_result is not None:
        return jsonify({"error": err_result[0]}), err_result[1]

    import uuid
    original_name = os.path.basename(uploaded_file.filename or "upload")
    safe_name = f"{uuid.uuid4()}_{original_name}"
    file_path = os.path.join(settings.upload_dir, safe_name)
    uploaded_file.save(file_path)

    task = run_pipeline.delay(file_path)  # type: ignore[attr-defined]
    log.info("enqueued job_id=%s file=%s", task.id, safe_name)

    return jsonify({"job_id": task.id, "status": "queued"}), 202


@app.route("/status/<job_id>")
def status(job_id: str):
    result = AsyncResult(job_id, app=celery)

    if result.state == "PENDING":
        return jsonify({"job_id": job_id, "status": "pending"})

    if result.state == "PROGRESS":
        return jsonify({
            "job_id": job_id,
            "status": "running",
            "stage": result.info.get("stage"),
            "model": result.info.get("model", ""),
            "planner_model": result.info.get("planner_model", ""),
        })

    if result.state == "SUCCESS":
        return jsonify({"job_id": job_id, **result.result})

    if result.state == "FAILURE":
        return jsonify({"job_id": job_id, "status": "failed", "error": str(result.info)}), 500

    return jsonify({"job_id": job_id, "status": result.state})


@app.route("/video/<filename>")
def video(filename: str):
    return send_file(
        os.path.join(settings.video_dir, filename), mimetype="video/mp4"
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app.run(host="0.0.0.0", port=6001, debug=True, use_reloader=False)
