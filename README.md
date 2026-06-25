# Interview Evaluation Service

Analyze interview recordings from a URL: extract questions asked, evaluate candidate answers, and generate actionable feedback for both the candidate and interviewer.

## What it does

1. **Downloads** audio/video from a recording link (direct file URLs, YouTube, Loom, etc.)
2. **Transcribes** the recording using OpenAI Whisper
3. **Extracts** each question the interviewer asked
4. **Evaluates** how well the candidate answered (score 0–10, quality rating, strengths/gaps)
5. **Generates feedback** to improve future interview outcomes

## Quick start

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure API key

```bash
cp .env.example .env
# Edit .env and set your OPENAI_API_KEY
```

### 3. Run the server

```bash
uvicorn app.main:app --reload --reload-dir app --port 8002
```

Open http://localhost:8002 for the web UI, or http://localhost:8002/docs for the API explorer.

## Share with your team

One person deploys the service; teammates open a URL in their browser. They do **not** need to clone the repo or have an OpenAI API key.

### 1. Deploy on a shared machine

Use any computer or VM your team can reach (office server, cloud VM, etc.):

```bash
git clone https://github.com/supermattjayallen/interview-eval
cd interview-eval
cp .env.example .env
```

Edit `.env`:

```bash
OPENAI_API_KEY=sk-your-org-key-here
TEAM_USERNAME=your-team
TEAM_PASSWORD=pick-a-strong-shared-password
```

Start the service:

```bash
docker compose up -d --build
```

The service listens on port **8002**. Teammates visit:

```
http://<server-ip-or-hostname>:8002
```

The browser will prompt for the team username and password. Share those credentials with your teammates (not the OpenAI key).

### 2. What teammates do

1. Open the URL above
2. Enter the team username/password when prompted
3. Paste a recording link and analyze, or use **Prepare for interview**

No local setup required.

### 3. Shared data

All analyses and job descriptions are stored on the server (`data/` volume). Everyone benefits from the same question bank for interview prep.

### 4. Security notes

- Keep `OPENAI_API_KEY` only on the server — never share it with teammates
- Always set `TEAM_USERNAME` and `TEAM_PASSWORD` before exposing the service on a network
- For production use, put HTTPS in front (e.g. Caddy or nginx with a TLS certificate)
- Restrict network access with a firewall or VPN if the service is not on the public internet

### Windows VPS (no Docker)

On a Windows server without Docker:

```powershell
cd interview-eval
powershell -ExecutionPolicy Bypass -File .\scripts\install-vps.ps1
# Edit .env — set OPENAI_API_KEY (team login is created automatically)
powershell -ExecutionPolicy Bypass -File .\scripts\start-server.ps1
```

Optional: start automatically on boot:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register-startup-task.ps1
```

Also open port **8002** in your cloud provider's firewall (security group), not only Windows Firewall.

## API usage

### Async (recommended for long recordings)

```bash
# Start analysis
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "recording_url": "https://example.com/interview.mp4",
    "role_title": "Senior Backend Engineer",
    "role_description": "Python, distributed systems, PostgreSQL",
    "evaluation_criteria": ["system design depth", "communication clarity"]
  }'

# Poll for results
curl http://localhost:8000/analyze/{job_id}
```

### Sync (blocks until complete)

```bash
curl -X POST http://localhost:8000/analyze/sync \
  -H "Content-Type: application/json" \
  -d '{"recording_url": "https://example.com/interview.mp4"}'
```

### CLI

```bash
python cli.py "https://example.com/interview.mp4" \
  --role-title "Senior Backend Engineer" \
  --output result.json
```

## Supported recording links

| Source | Support |
|--------|---------|
| Direct `.mp3`, `.mp4`, `.wav`, etc. | Yes |
| YouTube | Yes (via yt-dlp) |
| Loom, Vimeo, and other platforms | Yes (via yt-dlp) |
| Zoom/Meet cloud recordings (public link) | Usually yes if publicly accessible |
| Password-protected / auth-required links | No (download will fail) |

## Example response

```json
{
  "recording_url": "https://example.com/interview.mp4",
  "role_title": "Senior Backend Engineer",
  "transcript_summary": "45-minute technical interview covering system design and Python.",
  "total_questions": 8,
  "average_score": 6.8,
  "topics_covered": ["API design", "caching", "databases"],
  "qa_pairs": [
    {
      "question": "How would you design a rate limiter?",
      "quality": "good",
      "score": 7,
      "strengths": ["Mentioned token bucket algorithm"],
      "gaps": ["Did not discuss distributed rate limiting"],
      "ideal_answer_points": ["Token bucket vs sliding window", "Redis for distributed state"]
    }
  ],
  "feedback": {
    "candidate_feedback": ["Practice structured system design answers using a clear framework"],
    "overall_recommendation": "Needs follow-up — strong fundamentals but gaps in distributed systems"
  }
}
```

## Architecture

```
Recording URL
    ↓
recording_fetcher  →  download audio (httpx or yt-dlp)
    ↓
transcriber        →  OpenAI Whisper with timestamps
    ↓
analyzer           →  GPT-4o extracts Q&A pairs and evaluates
    ↓
Structured JSON result + feedback
```

## Prepare for an upcoming interview

Use the **Prepare for interview** tab to generate likely questions before you join an interview.

1. Analyze past interview recordings first — this builds your saved question bank
2. **Tag each recording with its interview step** (recruiter screen, technical, system design, etc.)
3. Save or paste the **job description** for the role you are preparing for
4. Select the **interview step** you are preparing for
5. Click **Get possible questions**

The service will:
- Load questions from previously saved interview analyses **for the same step**
- Use other steps only as secondary context
- Predict step-appropriate questions from the job description
- Provide preparation tips and strong answer outlines

Saved job descriptions are stored in `data/jobs/`.

## Saved Q&A results

Every completed analysis is saved locally under `data/results/` as JSON, keyed by the recording link.

If you submit the **same recording again**, even with a different sharing URL, the service returns the saved questions and answers immediately instead of re-running transcription and analysis.

### URL matching

The service normalizes recording links before saving/loading, so different URL formats for the same file still match. Examples:

| Platform | Treated as the same recording |
|----------|-------------------------------|
| Google Drive | `/file/d/ID/view`, `open?id=ID`, `uc?export=download&id=ID` |
| YouTube | `watch?v=ID`, `youtu.be/ID`, `/embed/ID` |
| Loom | `/share/ID` and `/embed/ID` |
| Dropbox / Zoom / OneDrive | Shared link variants with the same underlying ID |

Tracking query params like `?usp=sharing` are ignored.

To force a fresh run, check **Re-analyze even if this recording was processed before** in the UI.

### Optional: Google Drive storage

You can also sync saved results to a Google Drive folder.

1. Create a [Google Cloud service account](https://console.cloud.google.com/iam-admin/serviceaccounts)
2. Enable the Google Drive API
3. Download the service account JSON key to `credentials/google-service-account.json`
4. Create a Google Drive folder and share it with the service account email (Editor access)
5. Copy the folder ID from the folder URL and set it in `.env`:

```bash
GOOGLE_DRIVE_ENABLED=true
GOOGLE_DRIVE_CREDENTIALS_PATH=./credentials/google-service-account.json
GOOGLE_DRIVE_FOLDER_ID=your-folder-id-here
```

Saved files appear in Drive as `interview-analysis-<id>.json`.

Lookup order:
1. Local `data/results/`
2. Google Drive (if enabled)

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (required) | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o` | Model for analysis |
| `WHISPER_MODEL` | `whisper-1` | Transcription model |
| `TEMP_DIR` | `./tmp` | Temp storage for downloads |
| `RESULTS_DIR` | `./data/results` | Local saved Q&A storage |
| `TEAM_USERNAME` | (empty) | Shared login username for teammates |
| `TEAM_PASSWORD` | (empty) | Shared login password for teammates |
| `GOOGLE_DRIVE_ENABLED` | `false` | Sync saved results to Google Drive |
| `GOOGLE_DRIVE_CREDENTIALS_PATH` | `./credentials/google-service-account.json` | Service account key |
| `GOOGLE_DRIVE_FOLDER_ID` | (empty) | Shared Drive folder ID |

## Limitations & next steps

- **No speaker diarization yet** — the LLM infers interviewer vs candidate from context. For higher accuracy, integrate AssemblyAI or Deepgram for speaker labels.
- **In-memory job store** — active jobs are lost on restart, but completed Q&A is persisted locally/Drive.
- **Shared team login** — optional HTTP Basic Auth (`TEAM_USERNAME` / `TEAM_PASSWORD`); no per-user accounts yet.
- **Cost** — Whisper + GPT-4o usage scales with recording length.

Possible enhancements:
- Web UI for uploading links and viewing reports
- Rubric templates per role (SWE, PM, design)
- Compare multiple candidates for the same role
- Export PDF interview scorecards
