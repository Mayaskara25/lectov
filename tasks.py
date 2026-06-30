import json
import logging
import os
import shutil
import subprocess
import time
from typing import Optional

import openai
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from celery_app import celery
from config import settings
from litellm import completion
from litellm.exceptions import NotFoundError, RateLimitError, ServiceUnavailableError
from litellm.types.utils import ModelResponse
from pypdf import PdfReader
from typing import cast

log = logging.getLogger(__name__)

# ---------- prompts ----------

PLANNER_SYSTEM_PROMPT = """
You are an educational animation content planner.

Given lecture notes, produce a structured plan for a short Manim animation.
Output ONLY valid JSON. No markdown fences, no prose, no explanation.
Your response must start with { and end with }.

Schema:
{
  "title": "scene title string",
  "total_duration_seconds": <integer 30-120>,
  "sections": [
    {
      "id": <integer starting at 1>,
      "title": "section name",
      "duration_seconds": <integer 5-25>,
      "key_points": ["one brief point per entry"],
      "visualization": "<one of: title_card | formula_display | concept_list | worked_example | summary>",
      "formulas": ["valid LaTeX string for MathTex(), e.g. D(x^n) = nx^{n-1}"],
      "transition": "<one of: FadeIn | Write | GrowFromCenter>"
    }
  ],
  "color_scheme": {
    "titles": "BLUE",
    "body": "WHITE",
    "highlights": "YELLOW",
    "examples": "ORANGE"
  }
}

Rules:
- Include 3 to 6 sections only.
- total_duration_seconds must be between 30 and 120.
- Cover only the most important concepts — do not try to include everything.
- Each formula must be a valid LaTeX string that renders in MathTex().
- The "formulas" array may be empty [] for non-mathematical sections.
- 1 to 3 key_points per section.
"""

# Prepended to the user message when a plan is available.
_WRITER_PREAMBLE = (
    "Implement this animation plan as Manim Python code.\n"
    "Follow sections in order. Use formula strings directly inside MathTex().\n"
    "Use color_scheme values for Text() and MathTex() color= parameters.\n"
    "Match each section's duration_seconds with self.wait() call lengths.\n\n"
    "Plan:\n"
)

SYSTEM_PROMPT = """
You are an expert Manim animation engineer.

Generate ONLY runnable Python code.

Requirements:
- Use Manim Community Edition.
- Return ONLY python code.
- Main class MUST be named GeneratedScene.
- Use smooth animations.
- Make animations educational and delightful.
- Use modern Manim APIs.
- Use MathTex() for all mathematical expressions and formulas — LaTeX is installed.
- Use Text() for plain prose labels only.
- Avoid extremely heavy rendering.
- Keep rendering lightweight.

LAYOUT RULES (critical — objects must never overlap):
- NEVER position objects by chaining .next_to() calls on each other in a loop
  or list comprehension before each one has an established position. This
  causes objects to stack on top of each other.
- Instead, group related objects into a VGroup and call .arrange() on the
  group, e.g.:
    items = VGroup(text1, text2, text3).arrange(DOWN, buff=0.4)
    items.move_to(ORIGIN)
- If only one or two objects are on screen, use .move_to(UP*n) or
  .to_edge()/.next_to(other_object, DOWN, buff=0.5) with an object that is
  ALREADY positioned.
- Keep all text inside the visible frame (roughly x in [-6.5, 6.5],
  y in [-3.5, 3.5]).
- Use FadeOut or Transform to clear old content before introducing new
  content, rather than letting objects accumulate and overlap.

TIMING RULES (critical — the video must not be a single static frame):
- Every self.play(...) call MUST be followed by a self.wait(n) call
  (n >= 0.5 seconds) so the audience has time to read/see the result.
- The scene should have multiple sequential beats (intro -> explanation ->
  example -> conclusion), each separated by self.play() + self.wait(),
  not one giant simultaneous animation of everything at once.
- End the construct() method with a final self.wait(1) at minimum.

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
3. (Optional) The section titles the animation must cover

Fix the error. While fixing, also make sure:
- MathTex() is used for all mathematical expressions (LaTeX is installed).
- Objects are grouped with VGroup(...).arrange() rather than chained
  .next_to() calls on unpositioned objects.
- Every self.play(...) is followed by self.wait(n).
- The scene ends with a final self.wait(1).
- If section titles are provided, do not remove or reorder those sections.

Return ONLY corrected runnable Python code.
"""

# ---------- utils ----------

def clean_code(code: str) -> str:
    code = code.replace("```python", "").replace("```", "")
    return code.strip()


def save_code(path: str, code: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)


def extract_pdf_text(path: str) -> str:
    reader = PdfReader(path)
    return "".join(page.extract_text() or "" for page in reader.pages)


def extract_text(path: str) -> str:
    if path.endswith(".pdf"):
        return extract_pdf_text(path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------- llm calls ----------

_RETRIABLE = (
    RateLimitError, openai.RateLimitError,
    ServiceUnavailableError, openai.APIStatusError,
    openai.APIError, NotFoundError,
)

# Per-process tracking of the most recently used model for each role.
# Safe because worker_prefetch_multiplier=1 means one task per process at a time.
_active_model: str = ""    # writer / repair model
_planner_model: str = ""   # planner model


def llm_with_models(model_list: list[str], messages: list, temperature: float = 0.3) -> str:
    """Try each model in order, falling back on retriable errors."""
    global _active_model
    last_exc: Exception = RuntimeError("no models configured")
    for model in model_list:
        try:
            response = completion(
                model=model,
                messages=messages,
                temperature=temperature,
                stream=False,
                timeout=settings.llm_timeout,
            )
            _active_model = model
            if isinstance(response, ModelResponse):
                return cast(str, response.choices[0].message.content or "")
            response = cast(ModelResponse, response.model_response_creator())
            return cast(str, response.choices[0].message.content or "")
        except _RETRIABLE as exc:
            log.warning("model=%s unavailable, trying next fallback: %s", model, exc)
            last_exc = exc
    raise last_exc


def llm(messages: list) -> str:
    """Call the writer/repair model list."""
    return llm_with_models(settings.model_list(), messages)


def llm_planner(messages: list) -> str:
    """Call the planner model list (lower temperature for consistent JSON)."""
    global _planner_model
    result = llm_with_models(settings.planner_model_list(), messages, temperature=0.1)
    _planner_model = _active_model
    return result


# ---------- agents ----------

def plan_content(lecture_text: str) -> dict:
    """
    Planner agent: reads lecture text, returns a structured JSON brief.
    Falls back to an empty dict on failure — the writer then uses raw lecture text.
    """
    try:
        raw = llm_planner([
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": lecture_text},
        ])
        # Strip any accidental markdown fences the model adds despite instructions.
        cleaned = raw.strip()
        for fence in ("```json", "```"):
            cleaned = cleaned.removeprefix(fence).removesuffix("```").strip()
        plan = json.loads(cleaned)
        sections = plan.get("sections", [])
        log.info(
            "planner=%s title=%r sections=%d duration=%ds",
            _planner_model,
            plan.get("title", ""),
            len(sections),
            plan.get("total_duration_seconds", 0),
        )
        return plan
    except Exception as exc:
        log.warning("planner failed (%s) — falling back to direct generation", exc)
        return {}


def generate_code(lecture_text: str, plan: dict | None = None) -> str:
    """
    Writer agent: generates Manim Python from a plan (preferred) or raw text (fallback).
    """
    if plan:
        user_msg = _WRITER_PREAMBLE + json.dumps(plan, indent=2)
    else:
        user_msg = lecture_text
    return clean_code(llm([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]))


def repair_code(code: str, error: str, section_titles: list[str] | None = None) -> str:
    """
    Repair agent: fixes broken Manim code given the error output.
    Optionally receives section titles to prevent the model from dropping sections.
    """
    sections_note = ""
    if section_titles:
        joined = ", ".join(f'"{t}"' for t in section_titles)
        sections_note = f"\n\nSection titles that must remain in order: {joined}"
    return clean_code(llm([
        {"role": "system", "content": REPAIR_PROMPT},
        {
            "role": "user",
            "content": f"Original Code:\n\n{code}\n\nError:\n\n{error}{sections_note}",
        },
    ]))


# ---------- validation ----------

def syntax_check(scene_path: str) -> tuple[bool, Optional[str]]:
    result = subprocess.run(
        ["python", "-m", "py_compile", scene_path],
        capture_output=True, text=True, timeout=30,
    )
    return (True, None) if result.returncode == 0 else (False, result.stderr)


def render_check(scene_path: str) -> tuple[bool, Optional[str]]:
    result = subprocess.run(
        ["manim", "-ql", scene_path, "GeneratedScene"],
        capture_output=True, text=True, timeout=120,
    )
    return (True, None) if result.returncode == 0 else (False, result.stderr)


def render_scene(scene_path: str, output_name: str) -> str:
    result = subprocess.run(
        ["manim", "-qh", scene_path, "GeneratedScene"],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    scene_stem = os.path.splitext(os.path.basename(scene_path))[0]
    generated_video = f"media/videos/{scene_stem}/1080p60/GeneratedScene.mp4"
    final_output = f"{settings.video_dir}/{output_name}.mp4"
    shutil.copy(generated_video, final_output)

    # Remove intermediate media tree to prevent unbounded disk growth.
    shutil.rmtree(f"media/videos/{scene_stem}", ignore_errors=True)

    return final_output


# ---------- celery task ----------

@celery.task(bind=True, task_time_limit=600, task_soft_time_limit=540)
def run_pipeline(self: Task, file_path: str) -> dict:
    job_id = self.request.id
    scene_path = f"{settings.scene_dir}/{job_id}.py"

    try:
        def state(stage: str) -> None:
            self.update_state(state="PROGRESS", meta={
                "stage": stage,
                "model": _active_model,
                "planner_model": _planner_model,
            })

        # ── extract ──────────────────────────────────────────────────────────
        state("extracting")
        t0 = time.perf_counter()
        lecture_text = extract_text(file_path)
        log.info("job=%s stage=extracting elapsed=%.2fs", job_id, time.perf_counter() - t0)

        # ── plan ─────────────────────────────────────────────────────────────
        state("planning")
        t0 = time.perf_counter()
        plan = plan_content(lecture_text)
        section_titles = [s["title"] for s in plan.get("sections", [])]
        log.info(
            "job=%s stage=planning planner=%s elapsed=%.2fs sections=%s",
            job_id, _planner_model, time.perf_counter() - t0, section_titles,
        )

        # ── generate ─────────────────────────────────────────────────────────
        state("generating")
        t0 = time.perf_counter()
        code = generate_code(lecture_text, plan or None)
        save_code(scene_path, code)
        log.info(
            "job=%s stage=generating writer=%s elapsed=%.2fs used_plan=%s",
            job_id, _active_model, time.perf_counter() - t0, bool(plan),
        )

        # ── syntax repair loop ───────────────────────────────────────────────
        state("syntax_check")
        syntax_ok, error = syntax_check(scene_path)
        retries = 0
        while not syntax_ok and retries < settings.max_retries:
            state(f"syntax_repair_{retries + 1}")
            log.info("job=%s stage=syntax_repair attempt=%d", job_id, retries + 1)
            t0 = time.perf_counter()
            code = repair_code(code, error or "", section_titles or None)
            save_code(scene_path, code)
            syntax_ok, error = syntax_check(scene_path)
            log.info(
                "job=%s stage=syntax_repair attempt=%d model=%s elapsed=%.2fs ok=%s",
                job_id, retries + 1, _active_model, time.perf_counter() - t0, syntax_ok,
            )
            retries += 1

        if not syntax_ok:
            log.warning("job=%s failed stage=syntax error=%s", job_id, error)
            return {"status": "failed", "stage": "syntax", "error": error}

        # ── render repair loop ───────────────────────────────────────────────
        state("render_check")
        render_ok, error = render_check(scene_path)
        retries = 0
        while not render_ok and retries < settings.max_retries:
            state(f"render_repair_{retries + 1}")
            log.info("job=%s stage=render_repair attempt=%d", job_id, retries + 1)
            t0 = time.perf_counter()
            code = repair_code(code, error or "", section_titles or None)
            save_code(scene_path, code)
            render_ok, error = render_check(scene_path)
            log.info(
                "job=%s stage=render_repair attempt=%d model=%s elapsed=%.2fs ok=%s",
                job_id, retries + 1, _active_model, time.perf_counter() - t0, render_ok,
            )
            retries += 1

        if not render_ok:
            log.warning("job=%s failed stage=render error=%s", job_id, error)
            return {"status": "failed", "stage": "render", "error": error}

        # ── final render ─────────────────────────────────────────────────────
        state("final_render")
        t0 = time.perf_counter()
        video_path = render_scene(scene_path, job_id)
        log.info("job=%s stage=final_render elapsed=%.2fs path=%s", job_id, time.perf_counter() - t0, video_path)

        return {
            "status": "success",
            "job_id": job_id,
            "scene_path": scene_path,
            "video_path": video_path,
            "plan_title": plan.get("title", "") if plan else "",
            "sections": section_titles,
        }

    except (RateLimitError, openai.RateLimitError) as exc:
        log.warning("job=%s rate_limited (attempt %d): %s", job_id, self.request.retries + 1, exc)
        raise self.retry(exc=exc, countdown=30, max_retries=3)

    except (ServiceUnavailableError, openai.APIStatusError) as exc:
        log.warning("job=%s service_unavailable (attempt %d): %s", job_id, self.request.retries + 1, exc)
        raise self.retry(exc=exc, countdown=15, max_retries=3)

    except SoftTimeLimitExceeded:
        log.error("job=%s soft_time_limit_exceeded", job_id)
        return {"status": "failed", "stage": "timeout", "error": "job exceeded time limit"}
