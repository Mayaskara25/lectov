import os
import shutil
import subprocess
import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file
from litellm import completion
from pypdf import PdfReader

load_dotenv()
# ---------- setup ----------

MODEL = os.getenv("LITELLM_MODEL")
UPLOAD_DIR = "uploads"
SCENE_DIR = "generated/scenes"
VIDEO_DIR = "generated/videos"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SCENE_DIR, exist_ok=True)
os.makedirs(VIDEO_DIR, exist_ok=True)

app = Flask(__name__)

# ---------- prompts ----------
SYSTEM_PROMPT = """
You are an expert Manim animation engineer.

Generate ONLY runnable Python code.

Requirements:
- Use Manim Community Edition.
- Return ONLY python code.
- Main class MUST be named GeneratedScene.
- Use smooth animations.
- Make animations educational and delightful.
- Ensure objects never overlap.
- Use arrange() and next_to().
- Keep text inside screen bounds.
- Use modern Manim APIs.
- Avoid LaTeX unless absolutely necessary.
- Avoid extremely heavy rendering.
- Never use overlapping coordinates.
- Keep rendering lightweight.

The code MUST begin with:

from manim import *

class GeneratedScene(Scene):
    def construct(self):
        ...
"""

REPAIR_PROMPT = """
You repair broken Manim scripts.

You will receive:
1. Original code
2. Error output

Return ONLY corrected runnable Python code.
"""


# ---------- utils ----------
def clean_code(code: str):
    code = code.replace("```python", "")
    code = code.replace("```", "")
    return code.strip()


def save_code(path: str, code: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)


def extract_pdf_text(path):
    reader = PdfReader(path)

    text = ""

    for page in reader.pages:
        text += page.extract_text() or ""

    return text


def extract_text(path):
    if path.endswith(".pdf"):
        return extract_pdf_text(path)

    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------- llm calls ----------


def llm(messages):
    response = completion(model=MODEL, messages=messages, temperature=0.3)

    return response.choices[0].message.content


def generate_code(lecture_text: str):
    code = llm(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": lecture_text},
        ]
    )

    return clean_code(code)


def repair_code(code: str, error: str):
    fixed = llm(
        [
            {"role": "system", "content": REPAIR_PROMPT},
            {
                "role": "user",
                "content": f"""
Original Code:

{code}

Error:

{error}
""",
            },
        ]
    )

    return clean_code(fixed)


# ---------- validation ----------


def syntax_check(scene_path: str):
    result = subprocess.run(
        ["python", "-m", "py_compile", scene_path],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        return False, result.stderr

    return True, None


def render_check(scene_path: str):
    result = subprocess.run(
        ["manim", "-ql", scene_path, "GeneratedScene"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        return False, result.stderr

    return True, None


def render_scene(scene_path: str, output_name: str):
    result = subprocess.run(
        ["manim", "-pqh", scene_path, "GeneratedScene"],
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        raise Exception(result.stderr)

    generated_video = (
        "media/videos/"
        f"{os.path.splitext(os.path.basename(scene_path))[0]}"
        "/480p15/GeneratedScene.mp4"
    )

    final_output = f"{VIDEO_DIR}/{output_name}.mp4"

    shutil.copy(generated_video, final_output)

    return final_output


# ---------- main ----------
def run_pipeline(file_path: str):
    job_id = str(uuid.uuid4())

    scene_path = f"{SCENE_DIR}/{job_id}.py"

    print("Extracting lecture text...")

    lecture_text = extract_text(file_path)

    print("Generating Python code...")

    code = generate_code(lecture_text)

    save_code(scene_path, code)

    MAX_RETRIES = 3

    syntax_ok, error = syntax_check(scene_path)

    retries = 0

    while not syntax_ok and retries < MAX_RETRIES:
        print("Fixing syntax errors...")

        code = repair_code(code, error)

        save_code(scene_path, code)

        syntax_ok, error = syntax_check(scene_path)

        retries += 1

    if not syntax_ok:
        return {"status": "failed", "stage": "syntax", "error": error}
    render_ok, error = render_check(scene_path)

    retries = 0

    while not render_ok and retries < MAX_RETRIES:
        print("Fixing render errors...")

        code = repair_code(code, error)

        save_code(scene_path, code)

        render_ok, error = render_check(scene_path)

        retries += 1

    if not render_ok:
        return {"status": "failed", "stage": "render", "error": error}
    print("Rendering final animation...")

    video_path = render_scene(scene_path, job_id)

    return {
        "status": "success",
        "job_id": job_id,
        "scene_path": scene_path,
        "video_path": video_path,
    }


# ---------- flask ----------
@app.route("/")
def home():
    return {"status": "running"}


@app.route("/generate", methods=["POST"])
def generate():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    uploaded_file = request.files["file"]

    file_path = os.path.join(UPLOAD_DIR, uploaded_file.filename)

    uploaded_file.save(file_path)

    result = run_pipeline(file_path)

    return jsonify(result)


@app.route("/video/<filename>")
def video(filename):
    return send_file(os.path.join(VIDEO_DIR, filename), mimetype="video/mp4")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6001, debug=True, use_reloader=False)
