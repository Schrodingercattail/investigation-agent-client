"""Per-turn response language selection (bounded: zh/en only).

Priority (per the language work package):
1. an EXPLICIT answer-language request in the current turn ("用英文回答",
   "in English", "回答用中文", ...) — overrides everything else;
2. the dominant natural language of the request BODY itself;
3. None — the language genuinely cannot be determined; the caller keeps
   its existing default behavior.

The decision is based on the CURRENT user request ONLY — never on the
previous assistant turn, session history, tool payloads, RP data, or
finding titles. Mixed technical terms (tool names, "ML", IDs) do not
flip the detection.
"""

import re

ZH = "zh"
EN = "en"

# --- explicit answer-language requests (checked FIRST) ----------------------
_EXPLICIT_ZH = re.compile(
    r"用中文(回答|回复|说明|解释)|以中文(回答|回复)|中文回答|回答用中文|"
    r"请?(?:用|以)中文")
_EXPLICIT_EN = re.compile(
    r"answer\s+in\s+english|respond\s+in\s+english|in\s+english,?\s+please|"
    r"用英文(回答|回复|说明|解释)|以英文(回答|回复)|英文回答|回答用英文|"
    r"请?(?:用|以)英文",
    re.IGNORECASE)

# --- body-language ratio ----------------------------------------------------
_CJK = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")


def detect_response_language(text: str | None) -> str | None:
    """Return "zh", "en", or None (undeterminable) for one user request."""
    t = text or ""
    if not t.strip():
        return None
    if _EXPLICIT_EN.search(t):
        return EN
    if _EXPLICIT_ZH.search(t):
        return ZH
    cjk = len(_CJK.findall(t))
    latin = len(_LATIN.findall(t))
    if cjk == 0 and latin == 0:
        return None
    # Body-language ratio over NON-SPACE characters: English technical
    # terms (tool names, "ML", ids) contribute Latin letters without
    # changing the sentence's language — a CJK share of the non-space
    # text at/above 30% (with at least 2 CJK chars, i.e. a real phrase)
    # is a zh sentence even when an English product term rides along.
    non_space = len("".join(t.split()))
    cjk_ratio = cjk / non_space if non_space else 0.0
    if cjk >= 2 and cjk_ratio >= 0.30:
        return ZH
    if cjk == 0 and latin > 0:
        return EN
    if latin == 0 and cjk > 0:
        return ZH
    if cjk_ratio < 0.10:
        return EN
    return None
