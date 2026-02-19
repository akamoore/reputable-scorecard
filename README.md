# Reputable Health — Recruitment Scorecard

An internal tool that reads a study document (IRB protocol, study design, or screening questions) and returns a full recruitment difficulty scorecard in seconds.

---

## What It Does

Drop in any study PDF and get back:

- **Difficulty score** (0–100) with band label (Easy → Extreme)
- **Estimated days to recruit**
- **Ad spend required** (Y/N) and estimated dollar range
- **Risk flags** (🔴🟡🟢) for every eligibility criterion that could slow recruitment
- **Full list of extracted screening questions**

The scoring rubric is calibrated against Reputable's historical CPO data across 22 studies.

---

## Files

| File | Description |
|------|-------------|
| `scorecard.py` | Main script — runs the scorecard |
| `config.yaml` | Scoring weights and rubric thresholds (edit to recalibrate) |
| `studies.json` | Historical study data used for calibration reference |

---

## Setup

### 1. Prerequisites

- Python 3.8+
- An Anthropic API key (get one at [console.anthropic.com](https://console.anthropic.com))

### 2. Install dependencies

```bash
pip3 install anthropic pyyaml pypdf
```

### 3. Set your API key

```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE' >> ~/.zshrc
source ~/.zshrc
```

On bash:
```bash
echo 'export ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE' >> ~/.bash_profile
source ~/.bash_profile
```

### 4. Clone this repo

```bash
git clone https://github.com/YOUR_USERNAME/reputable-scorecard.git
cd reputable-scorecard
```

---

## Usage

```bash
# Basic run
python3 scorecard.py path/to/irb_protocol.pdf

# Specify target N if not in the document
python3 scorecard.py study_design.pdf --target-n 50

# Save scorecard to a text file
python3 scorecard.py protocol.pdf --save scorecard_output.txt

# Output raw JSON (for logging or piping into other tools)
python3 scorecard.py protocol.pdf --json
```

---

## Supported File Types

| Format | Notes |
|--------|-------|
| `.pdf` | Standard PDFs (IRB protocols, study designs) |
| `.pdf` | Reputable platform exports (zip-archive format) |
| `.docx` | Word documents |
| `.txt` | Plain text |

---

## Scoring Dimensions

The difficulty score is built from 7 dimensions:

| Dimension | Max Points | What It Captures |
|-----------|-----------|-----------------|
| Sample Size | 18 | Larger N = harder to fill |
| Screening Questions | 15 | More questions = more funnel friction |
| Positive Inclusion | 18 | Must-have conditions/traits (biggest driver) |
| Wearable Friction | 12 | Device ownership gates (WHOOP >> Oura >> multi-device) |
| Study Design | 10 | RCT complexity, number of arms |
| Participant Burden | 15 | Duration, at-home labs, shipped product |
| Category Sensitivity | 12 | Topic appeal (sexual health >> cognitive >> sleep/energy) |

### Difficulty Bands

| Band | Score | Est. CPO | Guidance |
|------|-------|----------|----------|
| Easy | 0–29 | $0–$12 | Organic/VEP likely sufficient |
| Moderate | 30–44 | $12–$18 | Light paid spend |
| Hard | 45–59 | $18–$30 | Standard paid campaign required |
| Very Hard | 60–74 | $30–$60 | Heavy spend or specialized channels |
| Extreme | 75–100 | $60+ | Clinical population; consider fee-for-service panels |

---

## Recalibrating Weights

All scoring weights live in `config.yaml`. Edit that file to adjust thresholds without touching the code. After significant new studies complete, update the calibration anchors at the top of `config.yaml` to keep CPO estimates accurate.

---

## Notes

- Each scorecard run uses approximately $0.01–$0.03 of Anthropic API credits
- The API key is personal — do not commit it to this repo or share it in Slack
- `studies.json` contains internal CPO and ad spend data — keep this repo Private
