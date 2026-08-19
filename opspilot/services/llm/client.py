import os
import time

from google import genai
from google.genai import types
from dotenv import load_dotenv
from ...core.audit_log import log_event


# ============================================================
# GEMINI CONFIG
# ============================================================

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY environment variable is not set."
    )


# ============================================================
# CLIENT
# ============================================================

MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash"
)
GEMINI_REQUEST_TIMEOUT_MS = int(os.getenv("GEMINI_REQUEST_TIMEOUT_MS", "60000"))

client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT_MS),
)


# ============================================================
# ASK GEMINI
# ============================================================

def ask(prompt, *, response_json=False):

    start_time = time.perf_counter()

    log_event("llm.request", model=MODEL)

    last_error = None

    # Retry only transient provider/network failures.
    for attempt in range(3):

        try:

            if attempt > 0:

                log_event("llm.retry", attempt=attempt + 1)

            config = {
                "temperature": 0,
            }
            if response_json:
                config["response_mime_type"] = "application/json"

            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=config,
            )

            elapsed = (
                time.perf_counter()
                - start_time
            )

            log_event("llm.response", model=MODEL, elapsed_seconds=elapsed)

            text = response.text or ""

            return text

        except Exception as e:

            last_error = e

            elapsed = (
                time.perf_counter()
                - start_time
            )

            log_event(
                "llm.failure",
                model=MODEL,
                elapsed_seconds=elapsed,
                error_type=type(e).__name__,
            )

            error_text = str(e).upper()

            # Only retry temporary availability and timeout errors.
            temporary_error = (
                "429" in error_text
                or "500" in error_text
                or "502" in error_text
                or "503" in error_text
                or "504" in error_text
                or "UNAVAILABLE" in error_text
                or "SERVICE UNAVAILABLE" in error_text
                or "TIMEOUT" in error_text
                or "TIMED OUT" in error_text
                or "DEADLINE_EXCEEDED" in error_text
            )

            if not temporary_error:

                raise

            if attempt < 2:

                wait_seconds = 2 ** attempt

                log_event("llm.temporary_unavailable", wait_seconds=wait_seconds)

                time.sleep(
                    wait_seconds
                )

    log_event("llm.exhausted", model=MODEL)

    raise last_error
