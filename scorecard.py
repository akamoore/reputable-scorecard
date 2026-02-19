#!/usr/bin/env python3
"""
Reputable Health — Recruitment Difficulty Scorecard
────────────────────────────────────────────────────
Reads an IRB protocol, study design doc, or eligibility screening file
and returns a full recruitment difficulty scorecard.

Usage:
    python scorecard.py <path_to_document> [options]

Options:
    --target-n N           Override or provide target sample size
    --json                 Output raw JSON instead of formatted scorecard
    --save PATH            Save scorecard to a .txt file
    --api-key KEY          Anthropic API key (or set ANTHROPIC_API_KEY env var)

Examples:
    python scorecard.py nitrate_irb.pdf
    python scorecard.py hop_box_study_design.docx --target-n 35
    python scorecard.py screening_questions.pdf --json
    python scorecard.py protocol.pdf --save scorecard_output.txt

Requires:
    pip install anthropic pyyaml
"""

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path

# ─── ANSI colors ──────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
GREY   = "\033[90m"
WHITE  = "\033[97m"
BG_DARK = "\033[48;5;235m"


# ══════════════════════════════════════════════════════════════════════════════
# 1. DOCUMENT EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def extract_text(path: str) -> str:
    """
    Extract plain text from a PDF, DOCX, or TXT file.
    Supports: standard PDFs, Reputable zip-archive PDFs, DOCX, TXT.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = p.suffix.lower()

    # Plain text
    if suffix == ".txt":
        return p.read_text(errors="replace")

    # Try as ZIP archive (Reputable PDF export format + DOCX)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            # Reputable PDF export: contains .txt files
            txt_files = sorted([n for n in names if n.endswith(".txt")])
            if txt_files:
                return "\n".join(
                    z.read(n).decode("utf-8", errors="replace") for n in txt_files
                )
            # DOCX: word/document.xml
            if "word/document.xml" in names:
                xml = z.read("word/document.xml").decode("utf-8", errors="replace")
                text = re.sub(r"<[^>]+>", " ", xml)
                return re.sub(r" {2,}", " ", text).strip()

    # Plain-text DOCX
    if suffix in (".docx", ".doc"):
        try:
            return p.read_text(errors="replace")
        except Exception:
            pass

    # Standard PDF via pypdf
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            if pages:
                return "\n\n".join(pages)
            raise ValueError("PDF appears to be scanned/image-only — no extractable text found.")
        except ImportError:
            raise ImportError(
                "pypdf is required to read standard PDFs. Run: pip3 install pypdf"
            )

    raise ValueError(
        f"Could not extract text from '{path}'. Supported formats: PDF, DOCX, TXT."
    )


# ══════════════════════════════════════════════════════════════════════════════
# 2. CLAUDE API PARSING
# ══════════════════════════════════════════════════════════════════════════════

EXTRACTION_PROMPT = """You are a clinical research analyst for Reputable Health, a real-world evidence study platform.

You will be given the text of a study document (IRB protocol, study design, screening questions, or landing page).
Extract all recruitment-relevant information and return ONLY a valid JSON object — no markdown, no preamble.

Return this exact structure (use null for any field you cannot determine):

{
  "study_name": "string — inferred name of the study",
  "study_type": "RCT | RWE | VEP | null",
  "category": "one of: sleep_energy_coffee | hydration_general_wellness | skin_beauty | womens_longevity | cognitive_health | chronic_pain_inflammation | sexual_health",
  "target_n": integer or null,
  "n_arms": integer or null,
  "duration_days": integer or null,
  "target_age_min": integer or null,
  "target_age_max": integer or null,
  "target_gender": "all | female | male | null",
  "wearable": "Oura | WHOOP | Fitbit | Garmin | multi_device | none | null",
  "baseline_days_required": integer or null,
  "has_at_home_labs": true | false | null,
  "has_shipped_product": true | false | null,
  "screening_question_count": integer or null,
  "positive_inclusion_level": "none | demographic | lifestyle_habit | specific_score_req | medical_condition",
  "positive_inclusion_description": "string — what participants MUST have or be",
  "key_exclusions": ["array of strings — each major exclusion criterion"],
  "screening_questions": ["array of strings — each screening question verbatim or paraphrased"],
  "recruitment_risk_flags": [
    {
      "severity": "red | yellow | green",
      "factor": "short label for the risk factor",
      "detail": "1-2 sentence explanation of why this slows recruitment and approximately what % of population it affects"
    }
  ]
}

Severity guide for recruitment_risk_flags:
- RED: Eliminates >50% of otherwise eligible applicants, or requires very rare trait (e.g. specific diagnosed condition, narrow validated scale score range, rare device ownership like WHOOP, gender + age combo under 25yr span)
- YELLOW: Eliminates 20-50% of applicants (e.g. common wearable ownership like Oura, moderate age restriction, recent trial washout, lifestyle habit requirement)
- GREEN: Minor friction, eliminates <20% (e.g. US residency, English fluency, general health requirement, pregnancy exclusion)

Be thorough — flag EVERY screening question or eligibility criterion that has any recruitment impact. Do not omit anything.

Document text:
"""


def parse_with_claude(text: str, api_key: str, target_n_override: int = None) -> dict:
    """
    Send document text to Claude, get back a structured study profile.
    """
    try:
        import anthropic
    except ImportError:
        print(f"\n{RED}Error:{RESET} anthropic package not installed.")
        print("Run: pip install anthropic\n")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print(f"{GREY}  Sending document to Claude for analysis...{RESET}")

    # Truncate very long documents to ~50k chars (well within context)
    doc_text = text[:50000] if len(text) > 50000 else text

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": EXTRACTION_PROMPT + doc_text,
            }
        ],
    )

    raw = message.content[0].text.strip()

    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        profile = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"\n{RED}Error parsing Claude response:{RESET} {e}")
        print(f"Raw response:\n{raw[:500]}")
        sys.exit(1)

    # Apply CLI override
    if target_n_override:
        profile["target_n"] = target_n_override

    return profile


# ══════════════════════════════════════════════════════════════════════════════
# 3. SCORING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        # Fallback inline config if config.yaml not found
        return _inline_config()
    try:
        import yaml
        with open(config_path) as f:
            return yaml.safe_load(f)
    except ImportError:
        return _inline_config()


def _inline_config() -> dict:
    """Inline fallback config — mirrors config.yaml exactly."""
    return {
        "bands": [
            {"label": "Easy",      "min": 0,  "max": 29,  "cpo_est": "$0–$12",  "notes": "Organic/VEP likely sufficient"},
            {"label": "Moderate",  "min": 30, "max": 44,  "cpo_est": "$12–$18", "notes": "Light paid spend"},
            {"label": "Hard",      "min": 45, "max": 59,  "cpo_est": "$18–$30", "notes": "Standard paid campaign required"},
            {"label": "Very Hard", "min": 60, "max": 74,  "cpo_est": "$30–$60", "notes": "Heavy spend or specialized channels"},
            {"label": "Extreme",   "min": 75, "max": 100, "cpo_est": "$60+",    "notes": "Clinical population"},
        ],
        "sample_size": {
            "tiers": [
                {"max_n": 15,    "pts": 2},
                {"max_n": 30,    "pts": 4},
                {"max_n": 50,    "pts": 6},
                {"max_n": 80,    "pts": 8},
                {"max_n": 120,   "pts": 11},
                {"max_n": 200,   "pts": 14},
                {"max_n": 99999, "pts": 18},
            ]
        },
        "screening_questions": {"pts_per_question": 1.0, "max_pts": 15},
        "positive_inclusion": {
            "levels": {
                "none":               0,
                "demographic":        4,
                "lifestyle_habit":    4,
                "specific_score_req": 14,
                "medical_condition":  18,
            }
        },
        "wearable": {
            "device_pts": {"none": 0, "multi_device": 0, "Oura": 3, "Garmin": 4, "WHOOP": 11},
            "baseline_pts": {0: 0, 14: 1, 28: 2, 30: 2},
            "wearable_eligibility_condition_pts": 1,
        },
        "study_design": {
            "type_pts": {"VEP": 0, "RWE_organic": 2, "RWE_paid": 5, "RCT_2arm": 8, "RCT_3arm": 10}
        },
        "participant_burden": {
            "duration_pts": [
                {"max_days": 30,    "pts": 0},
                {"max_days": 60,    "pts": 3},
                {"max_days": 90,    "pts": 5},
                {"max_days": 99999, "pts": 7},
            ],
            "at_home_labs_pts": 6,
            "shipped_product_pts": 2,
        },
        "category_sensitivity": {
            "categories": {
                "sleep_energy_coffee": 1, "hydration_general_wellness": 2,
                "skin_beauty": 3, "womens_longevity": 5, "cognitive_health": 6,
                "chronic_pain_inflammation": 9, "sexual_health": 12,
            }
        },
    }


def score_profile(profile: dict, cfg: dict) -> dict:
    """
    Score a parsed study profile. Returns full breakdown dict.
    """
    n   = profile.get("target_n")
    sq  = profile.get("screening_question_count")
    inc = profile.get("positive_inclusion_level", "none")
    cat = (profile.get("category") or "").lower()
    device = profile.get("wearable") or "none"
    baseline = profile.get("baseline_days_required") or 0
    has_labs = profile.get("has_at_home_labs") or False
    has_product = profile.get("has_shipped_product") or False
    duration = profile.get("duration_days") or 0
    study_type = profile.get("study_type") or "VEP"
    n_arms = profile.get("n_arms") or 1
    gender = profile.get("target_gender") or "all"
    age_min = profile.get("target_age_min")
    age_max = profile.get("target_age_max")

    breakdown = {}

    # ── Sample size ───────────────────────────────────────────────────────
    n_pts = 0
    if n:
        for tier in cfg["sample_size"]["tiers"]:
            if n <= tier["max_n"]:
                n_pts = tier["pts"]
                break
    breakdown["sample_size"] = {
        "pts": n_pts, "max": 18,
        "note": f"N = {n}" if n else "N unknown"
    }

    # ── Screening questions ───────────────────────────────────────────────
    sq_c = cfg["screening_questions"]
    sq_pts = min((sq or 0) * sq_c["pts_per_question"], sq_c["max_pts"])
    breakdown["screening_questions"] = {
        "pts": sq_pts, "max": 15,
        "note": f"{sq} questions" if sq else "Count unknown"
    }

    # ── Positive inclusion ────────────────────────────────────────────────
    inc_levels = cfg["positive_inclusion"]["levels"]
    inc_pts = inc_levels.get(inc, 0)
    breakdown["positive_inclusion"] = {
        "pts": inc_pts, "max": 18,
        "note": profile.get("positive_inclusion_description") or inc
    }

    # ── Wearable ──────────────────────────────────────────────────────────
    dev_pts_map = cfg["wearable"]["device_pts"]
    if device in ("multi_device", "none"):
        dev_pts = dev_pts_map.get(device, 0)
    else:
        dev_pts = dev_pts_map.get(device, 3)

    bl_pts_map = cfg["wearable"]["baseline_pts"]
    bl_pts = 0
    for threshold in sorted(bl_pts_map.keys()):
        if baseline >= threshold:
            bl_pts = bl_pts_map[threshold]

    w_pts = min(dev_pts + bl_pts, 12)
    wearable_note = device if device not in ("none", "multi_device") else ("multi-device accepted" if device == "multi_device" else "no device required")
    if baseline > 0:
        wearable_note += f" + {baseline}d baseline"
    breakdown["wearable_friction"] = {"pts": w_pts, "max": 12, "note": wearable_note}

    # ── Study design ──────────────────────────────────────────────────────
    type_pts_map = cfg["study_design"]["type_pts"]
    if study_type == "VEP":
        design_key = "VEP"
    elif study_type == "RWE":
        design_key = "RWE_paid"   # Conservative: assume paid since we don't know yet
    elif study_type == "RCT":
        design_key = "RCT_3arm" if n_arms >= 3 else "RCT_2arm"
    else:
        design_key = "VEP"
    d_pts = type_pts_map.get(design_key, 0)
    breakdown["study_design"] = {"pts": d_pts, "max": 10, "note": f"{design_key} ({n_arms} arms)"}

    # ── Participant burden ────────────────────────────────────────────────
    burden_cfg = cfg["participant_burden"]
    dur_pts = 0
    for tier in burden_cfg["duration_pts"]:
        if duration <= tier["max_days"]:
            dur_pts = tier["pts"]
            break
    labs_pts = burden_cfg["at_home_labs_pts"] if has_labs else 0
    prod_pts = burden_cfg["shipped_product_pts"] if has_product else 0
    b_pts = dur_pts + labs_pts + prod_pts
    burden_parts = [f"{duration}d duration"]
    if has_labs:
        burden_parts.append("at-home labs")
    if has_product:
        burden_parts.append("shipped product")
    breakdown["participant_burden"] = {"pts": b_pts, "max": 15, "note": ", ".join(burden_parts)}

    # ── Category sensitivity ──────────────────────────────────────────────
    cat_map = cfg["category_sensitivity"]["categories"]
    c_pts = 1  # default lowest
    for key, pts in cat_map.items():
        if key in cat:
            c_pts = pts
            break
    breakdown["category_sensitivity"] = {"pts": c_pts, "max": 12, "note": f"category: {cat or 'unknown'}"}

    total = round(sum(v["pts"] for v in breakdown.values()))
    total = max(0, min(100, total))

    band_info = next(
        (b for b in cfg["bands"] if b["min"] <= total <= b["max"]),
        cfg["bands"][-1]
    )

    return {
        "total_score": total,
        "band": band_info["label"],
        "cpo_estimate_range": band_info["cpo_est"],
        "band_notes": band_info["notes"],
        "breakdown": breakdown,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. TIME & SPEND ESTIMATES
# ══════════════════════════════════════════════════════════════════════════════

def estimate_time_and_spend(profile: dict, score: int, band: str) -> dict:
    """
    Estimate time to recruit and ad spend based on score, N, and band.

    Benchmarks (from Reputable historical data):
      Small (≤40):   21 days base
      Medium (≤150): 45 days base
      Large (>150):  75 days base

    Time multiplier by difficulty:
      Easy (0-29):      0.5–0.8× base (organic, slower but no ad lever)
      Moderate (30-44): 0.8–1.0× base
      Hard (45-59):     1.0–1.3× base
      Very Hard (60-74): 1.3–1.8× base
      Extreme (75+):    1.8–2.5× base

    Ad spend decision:
      Score < 30:  Organic only, $0
      Score 30-44: Light paid, ~30% of recruits via ads
      Score 45-59: Moderate paid, ~60% via ads
      Score 60-74: Heavy paid, ~80% via ads
      Score 75+:   Full paid + supplemental channels
    """
    n = profile.get("target_n")

    # Base days by N
    if n is None:
        base_days = 45  # default medium
    elif n <= 40:
        base_days = 21
    elif n <= 150:
        base_days = 45
    else:
        base_days = 75

    # Time multiplier
    if score < 30:
        time_lo, time_hi = base_days * 0.8, base_days * 1.5   # organic is slower
    elif score < 45:
        time_lo, time_hi = base_days * 0.8, base_days * 1.1
    elif score < 60:
        time_lo, time_hi = base_days * 1.0, base_days * 1.4
    elif score < 75:
        time_lo, time_hi = base_days * 1.3, base_days * 2.0
    else:
        time_lo, time_hi = base_days * 1.8, base_days * 3.0

    time_lo = max(5, round(time_lo))
    time_hi = max(time_lo + 7, round(time_hi))

    # Ad spend
    needs_ads = score >= 30

    if not needs_ads or n is None:
        spend_lo = spend_hi = 0
        spend_note = "Organic / partner-dependent recruitment likely sufficient"
    else:
        # CPO range from band
        cpo_ranges = {
            "Moderate":  (12, 18),
            "Hard":      (18, 30),
            "Very Hard": (30, 60),
            "Extreme":   (60, 120),
        }
        cpo_lo, cpo_hi = cpo_ranges.get(band, (18, 30))

        # Fraction of N expected to come via paid channels
        paid_fractions = {
            "Moderate":  (0.2, 0.4),
            "Hard":      (0.5, 0.7),
            "Very Hard": (0.7, 0.9),
            "Extreme":   (0.9, 1.0),
        }
        frac_lo, frac_hi = paid_fractions.get(band, (0.5, 0.7))

        spend_lo = round(cpo_lo * n * frac_lo)
        spend_hi = round(cpo_hi * n * frac_hi)
        spend_note = f"Based on ${cpo_lo}–${cpo_hi} CPO × N={n} × {int(frac_lo*100)}–{int(frac_hi*100)}% paid share"

    return {
        "time_lo_days": time_lo,
        "time_hi_days": time_hi,
        "needs_ads": needs_ads,
        "spend_lo": spend_lo,
        "spend_hi": spend_hi,
        "spend_note": spend_note,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 5. FORMATTED SCORECARD OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

BAND_COLORS = {
    "Easy":      GREEN,
    "Moderate":  CYAN,
    "Hard":      YELLOW,
    "Very Hard": RED,
    "Extreme":   RED + BOLD,
}

FLAG_ICONS = {"red": "🔴", "yellow": "🟡", "green": "🟢"}

def bar(pts, max_pts, width=12):
    filled = round((pts / max_pts) * width) if max_pts else 0
    return ("▓" * filled).ljust(width)


def format_scorecard(
    filename: str,
    profile: dict,
    score_result: dict,
    estimates: dict,
) -> str:
    lines = []
    W = 72  # card width

    def rule(char="═"):
        return char * W

    def row(label, value, color=""):
        label_str = f"  {label}"
        value_str = f"{color}{value}{RESET}" if color else value
        return f"{label_str:<28}{value_str}"

    total   = score_result["total_score"]
    band    = score_result["band"]
    bcolor  = BAND_COLORS.get(band, WHITE)
    breakdown = score_result["breakdown"]

    lines.append(f"\n{BOLD}{rule()}{RESET}")
    lines.append(f"{BOLD}  REPUTABLE HEALTH — RECRUITMENT SCORECARD{RESET}")
    lines.append(rule("─"))
    lines.append(row("Study:",    profile.get("study_name") or "Unknown"))
    lines.append(row("Document:", Path(filename).name))
    lines.append(row("Type:",     f"{profile.get('study_type') or '—'}, {profile.get('n_arms') or 1} arm(s)"))
    lines.append(row("Target N:", str(profile.get("target_n") or "unknown")))
    lines.append(row("Duration:", f"{profile.get('duration_days') or '—'} days"))
    lines.append(rule("─"))

    # ── Difficulty score ──────────────────────────────────────────────────
    score_bar_width = 40
    filled = round((total / 100) * score_bar_width)
    score_bar = bcolor + ("█" * filled) + GREY + ("░" * (score_bar_width - filled)) + RESET
    lines.append(f"\n  {BOLD}DIFFICULTY SCORE{RESET}")
    lines.append(f"  {score_bar}  {bcolor}{BOLD}{total}/100  {band.upper()}{RESET}")
    lines.append(f"  {GREY}{score_result['band_notes']}{RESET}\n")
    lines.append(rule("─"))

    # ── Breakdown ─────────────────────────────────────────────────────────
    lines.append(f"\n  {BOLD}SCORE BREAKDOWN{RESET}")
    lines.append(f"  {'Dimension':<26} {'Pts':>4} / {'Max':>3}   {'':12}  Note")
    lines.append(f"  {'─'*65}")
    for dim, data in breakdown.items():
        pts = data["pts"]
        mx  = data["max"]
        note = data["note"]
        b = bar(pts, mx)
        dim_label = dim.replace("_", " ").title()
        lines.append(f"  {dim_label:<26} {pts:>4.0f} / {mx:>3}   {b}  {GREY}{note}{RESET}")
    lines.append(rule("─"))

    # ── Time & spend ──────────────────────────────────────────────────────
    lines.append(f"\n  {BOLD}RECRUITMENT PROJECTIONS{RESET}")
    t_lo = estimates["time_lo_days"]
    t_hi = estimates["time_hi_days"]
    lines.append(row("Est. days to recruit:", f"{t_lo}–{t_hi} days"))

    if estimates["needs_ads"]:
        lines.append(row("Ad spend required:", f"{YELLOW}YES{RESET}"))
        lines.append(row("Est. ad spend:", f"${estimates['spend_lo']:,} – ${estimates['spend_hi']:,}"))
        lines.append(f"  {GREY}  {estimates['spend_note']}{RESET}")
    else:
        lines.append(row("Ad spend required:", f"{GREEN}NO — organic likely sufficient{RESET}"))

    lines.append("")
    lines.append(rule("─"))

    # ── Risk flags ────────────────────────────────────────────────────────
    flags = profile.get("recruitment_risk_flags") or []
    if flags:
        lines.append(f"\n  {BOLD}ELIGIBILITY RISK FLAGS{RESET}")

        reds    = [f for f in flags if f["severity"] == "red"]
        yellows = [f for f in flags if f["severity"] == "yellow"]
        greens  = [f for f in flags if f["severity"] == "green"]

        for severity, group in [("red", reds), ("yellow", yellows), ("green", greens)]:
            if not group:
                continue
            icon = FLAG_ICONS[severity]
            for flag in group:
                factor = flag.get("factor", "")
                detail = flag.get("detail", "")
                lines.append(f"\n  {icon} {BOLD}{factor}{RESET}")
                # Wrap detail at ~65 chars
                words = detail.split()
                current_line = "     "
                for word in words:
                    if len(current_line) + len(word) + 1 > 70:
                        lines.append(f"  {GREY}{current_line}{RESET}")
                        current_line = "     " + word
                    else:
                        current_line += " " + word
                if current_line.strip():
                    lines.append(f"  {GREY}{current_line}{RESET}")

        lines.append("")

    # ── Screening questions ───────────────────────────────────────────────
    questions = profile.get("screening_questions") or []
    if questions:
        lines.append(rule("─"))
        lines.append(f"\n  {BOLD}SCREENING QUESTIONS EXTRACTED ({len(questions)}){RESET}")
        for i, q in enumerate(questions, 1):
            # Wrap long questions
            words = q.split()
            current_line = ""
            first = True
            for word in words:
                if len(current_line) + len(word) + 1 > 63:
                    prefix = f"  {i:>2}. " if first else "      "
                    lines.append(f"{prefix}{GREY}{current_line.strip()}{RESET}")
                    first = False
                    current_line = word
                else:
                    current_line += " " + word
            if current_line.strip():
                prefix = f"  {i:>2}. " if first else "      "
                lines.append(f"{prefix}{GREY}{current_line.strip()}{RESET}")
        lines.append("")

    lines.append(rule())
    lines.append("")

    return "\n".join(lines)


def strip_ansi(text: str) -> str:
    """Remove ANSI color codes for plain-text file output."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


# ══════════════════════════════════════════════════════════════════════════════
# 6. MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Reputable Health Recruitment Difficulty Scorecard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("document", help="Path to IRB protocol, study design, or screening questions file")
    parser.add_argument("--target-n", type=int, help="Override or provide target sample size")
    parser.add_argument("--json",     action="store_true", help="Output raw JSON")
    parser.add_argument("--save",     type=str, help="Save scorecard to this file path")
    parser.add_argument("--api-key",  type=str, help="Anthropic API key (or set ANTHROPIC_API_KEY)")
    args = parser.parse_args()

    # ── API key ───────────────────────────────────────────────────────────
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"\n{RED}Error:{RESET} No API key provided.")
        print("Set ANTHROPIC_API_KEY environment variable or use --api-key\n")
        sys.exit(1)

    # ── Extract ───────────────────────────────────────────────────────────
    print(f"\n{BOLD}Reputable Health — Recruitment Scorecard{RESET}")
    print(f"{GREY}{'─'*40}{RESET}")
    print(f"  Document: {args.document}")

    try:
        print(f"{GREY}  Extracting text...{RESET}")
        text = extract_text(args.document)
        word_count = len(text.split())
        print(f"{GREY}  Extracted {word_count:,} words{RESET}")
    except (FileNotFoundError, ValueError) as e:
        print(f"\n{RED}Error:{RESET} {e}\n")
        sys.exit(1)

    # ── Parse ─────────────────────────────────────────────────────────────
    cfg     = load_config()
    profile = parse_with_claude(text, api_key, target_n_override=args.target_n)

    print(f"{GREY}  Study identified: {profile.get('study_name', 'Unknown')}{RESET}")
    print(f"{GREY}  Scoring...{RESET}\n")

    # ── Score ─────────────────────────────────────────────────────────────
    score_result = score_profile(profile, cfg)
    estimates    = estimate_time_and_spend(profile, score_result["total_score"], score_result["band"])

    # ── Output ────────────────────────────────────────────────────────────
    if args.json:
        output = {
            "profile":   profile,
            "score":     score_result,
            "estimates": estimates,
        }
        print(json.dumps(output, indent=2))
    else:
        scorecard = format_scorecard(args.document, profile, score_result, estimates)
        print(scorecard)

        if args.save:
            save_path = Path(args.save)
            save_path.write_text(strip_ansi(scorecard))
            print(f"{GREEN}✓ Saved to {save_path}{RESET}\n")


if __name__ == "__main__":
    main()
