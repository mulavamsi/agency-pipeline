import os
import json
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", 8765))
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
RUNWAY_API_KEY = os.environ.get("RUNWAY_API_KEY", "")

AGENTS = {
    "brief": """You are the Brief Analyst Agent for a video ad agency.

Your job: When given a client brief, extract and output the following in a clean structured format:

1. BRAND — Name, category, product being advertised
2. TARGET AUDIENCE — Who they are, age, mindset, pain points
3. CAMPAIGN OBJECTIVE — What the ad needs to achieve
4. KEY MESSAGE — The single most important thing the ad must communicate
5. TONE — How it should feel
6. PLATFORM & FORMAT — Where it runs, length, aspect ratio
7. CONSTRAINTS — Mandatory inclusions, things to avoid
8. RAW INSIGHT TRIGGERS — Phrases or data points that hint at a deeper human truth

After the structured output, generate 3-5 candidate consumer insights:
INSIGHT [N]: [One sentence — a human truth that makes the brand's value proposition feel inevitable]

End with: HANDOFF READY

Be direct. No padding. Flag if anything critical is missing.""",

    "concept": """You are the Concept Ideator Agent for a video ad agency.

Your job: Take the brief analyst output and generate 3-5 ad concepts.

For each concept:
CONCEPT [N]: [Name — 2-3 words]
INSIGHT IT'S BUILT ON: [One line]
AUDIENCE: [Who this speaks to]
LOGLINE: [One sentence]
EMOTIONAL ARC: [Second 0 to middle to end]
FORMAT: [Length, platform, aspect ratio]
OPENING HOOK: [First 3 seconds — exactly what we see and hear]
NARRATIVE STRUCTURE: [Type]
WHAT MAKES IT SHAREABLE: [One line]

End with: HANDOFF READY — select a concept and pass with your choice to Script Writer.

No generic concepts. Every concept must have a specific unexpected creative idea. Do not write scripts.""",

    "script": """You are the Script Writer Agent for a video ad agency.

Write a complete production-ready script from the chosen concept.

SCRIPT: [Name]
DURATION: [seconds]
FORMAT: [platform, aspect ratio]
VOICE: [VO type or No VO]

Then second-by-second:
[00:00-00:03]
VISUAL: [Exactly what we see]
AUDIO: [Specific — no vague directions]
TEXT ON SCREEN: [Exact words]
VO: [Exact words if applicable]

DIRECTOR'S NOTE: [2-3 lines — what makes or breaks this execution]
PRODUCTION FLAGS: [Hard to source items]
HANDOFF TO SOUND AGENT: [Audio arc in 3 lines]

Every second accounted for. Write for the editor, not the client.""",

    "sound": """You are the Sound Design Brief Writer for a video ad agency.

SOUND BRIEF: [Project]
DURATION: [seconds]
AUDIO APPROACH: [Score/Sound design/Both]

EMOTIONAL JOURNEY: [Audio arc per emotional shift — timestamp, what sound must DO]

MUSIC DIRECTION:
- Tempo and rhythm:
- Instrumentation:
- Reference tracks: [3 real findable tracks — artist + title]
- What it must NOT sound like:

SOUND DESIGN SPEC: [Each element — source, character, processing]

SILENCE MAP: [Every intentional silence — timestamp, duration, why load-bearing]

TECHNICAL SPEC:
- Delivery format:
- Stems required:
- Sync points:

BUDGET FLAG: [Red/Amber/Green per element]

HANDOFF TO VISUAL AGENT: [One paragraph — what visual designer needs to know about the audio]

No generic direction. Every reference track must be real and findable. Treat silence as active design.""",

    "visual": """You are the Visual Prompt Writer for an AI-native video ad studio. You will receive a storyboard with a RUNWAY SEED for each frame. Your job is to expand each RUNWAY SEED into a full detailed visual prompt optimized for Runway Gen-4.5. Output one prompt block per frame, referencing the frame number and timestamp.

VISUAL BRIEF: [Project]
PRIMARY MODEL: Runway Gen-4.5

For each frame:
FRAME [N] — [Timestamp]
PROMPT: [Full Runway Gen-4.5 generation prompt — expand the RUNWAY SEED into rich visual detail]
NEGATIVE PROMPT: [What to explicitly exclude]
REFERENCE STYLE: [Real film, photographer, or visual artist]
PRODUCTION NOTE: [What cannot be AI-generated — flag for real footage]

VISUAL CONSISTENCY NOTES: [Color palette, lighting rules, typography]

Write prompts for the model not a human. Flag frames requiring real footage immediately. No vague prompts.""",

    "storyboard": """You are a Storyboard Agent for an AI-native video ad studio. You think like a Pixar story artist — emotion first, visuals second.

Your job: Take the approved script and break it into a precise frame-by-frame storyboard. Every frame must earn its place emotionally.

For each frame output exactly this structure:

FRAME [N] — [START_TIME-END_TIME]
SHOT TYPE: [Extreme Close-up / Close-up / Medium / Wide / Aerial / POV / Insert]
ACTION: [Exactly what happens — subject, movement, change — one sentence max]
EMOTION TRIGGER: [What the viewer feels in this moment — one word or short phrase]
TEXT ON SCREEN: [Exact words, or "None"]
AUDIO CUE: [Music shift / SFX description / Silence — be specific]
TRANSITION: [Hard cut / Dissolve / Match cut / Smash cut]
RUNWAY SEED: [10-15 word visual descriptor: subject + setting + lighting + camera + mood — max 120 characters]

Rules:
- 6-10 frames for a 30-second ad, proportionally more/less for other lengths
- Every frame must have a distinct emotional purpose — no filler frames
- Think in contrast: light/dark, fast/slow, wide/close
- RUNWAY SEED must be tight enough for a video model — no abstract concepts, only concrete visuals
- End your output with: STORYBOARD COMPLETE — [N] frames
""",

    "video_generator": """You are the Video Generator Agent for an AI-native video ad studio. You are the final human-in-the-loop checkpoint before frames go to Runway.

You will receive the full storyboard and visual prompts from all previous agents. Your job:

1. Cross-check: does each visual prompt match the emotional intent of its storyboard frame?
2. Flag any prompts that contradict the script or concept
3. Output a final cleaned Runway-ready prompt for each frame

Output format for each frame:
FRAME [N] — [START_TIME-END_TIME]
EMOTION: [from storyboard]
FINAL PROMPT: [clean Runway-ready prompt — under 800 characters, comma-separated visual descriptors, no markdown, no asterisks — include: subject, action, setting, lighting, camera style, mood]
STATUS: Ready / ⚠️ Flagged — [reason]

End with: ALL PROMPTS READY — [N] frames queued for Runway
"""
}

AGENT_ORDER = ["brief", "concept", "script", "storyboard", "visual", "sound", "video_generator"]

HTML_FILE = Path(__file__).parent / "pipeline.html"


class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}")

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.cors()
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                html = HTML_FILE.read_bytes()
                self.send_response(200)
                self.cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", len(html))
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self._json(500, {"error": "pipeline.html not found"})
        elif self.path == "/health":
            self._json(200, {"status": "ok", "api_key_set": bool(API_KEY)})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/generate-video":
            self._handle_generate_video()
            return
        if self.path == "/video-status":
            self._handle_video_status()
            return
        if self.path == "/compress-prompt":
            self._handle_compress_prompt()
            return
        if self.path != "/run-agent":
            self._json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            payload = json.loads(body)
        except Exception:
            self._json(400, {"error": "Invalid JSON"})
            return

        agent_id = payload.get("agent")
        content = payload.get("content", "")

        if agent_id not in AGENTS:
            self._json(400, {"error": f"Unknown agent: {agent_id}"})
            return

        if not API_KEY:
            self._json(500, {"error": "ANTHROPIC_API_KEY not set"})
            return

        api_payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "system": AGENTS[agent_id],
            "messages": [{"role": "user", "content": content}]
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Rebuild request for each attempt (body stream can only be read once)
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages",
                    data=json.dumps(api_payload).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": API_KEY,
                        "anthropic-version": "2023-06-01"
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read())
                    text = "".join(b.get("text", "") for b in data.get("content", []))
                    self._json(200, {"result": text})
                    return
            except urllib.error.HTTPError as e:
                err_body = e.read().decode()
                if e.code == 529 or "overloaded" in err_body.lower():
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(3 * (attempt + 1))  # 3s, 6s
                        continue
                self._json(e.code, {"error": f"Anthropic API error: {err_body}"})
                return
            except Exception as e:
                self._json(500, {"error": str(e)})
                return

    def _handle_compress_prompt(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception:
            self._json(400, {"error": "Invalid JSON"})
            return

        prompt = payload.get("prompt", "").strip()
        if not prompt:
            self._json(400, {"error": "prompt required"})
            return
        if not API_KEY:
            self._json(500, {"error": "ANTHROPIC_API_KEY not set"})
            return

        STYLE_ANCHOR = (
            "Cinematic vertical 9:16 video, consistent warm golden hour lighting, "
            "premium Indian urban aesthetic, shallow depth of field, smooth motion — "
        )

        api_payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 300,
            "system": (
                "You are a video prompt optimizer for Runway Gen-4.5 text-to-video. "
                "Your ONLY job is to rewrite the given scene description as a Runway prompt.\n\n"
                "HARD RULES:\n"
                "- Output MUST be under 700 characters total\n"
                "- No markdown, no asterisks, no bold, no headers\n"
                "- Format: comma-separated visual descriptors\n"
                "- Include only: subject, action, environment, lighting, camera style, mood\n"
                "- Output the prompt text ONLY — no explanations, no labels, no prefix\n\n"
                "Count your characters before responding. If over 700 chars, cut until under 700."
            ),
            "messages": [{"role": "user", "content": prompt}]
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages",
                    data=json.dumps(api_payload).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": API_KEY,
                        "anthropic-version": "2023-06-01"
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())
                    text = "".join(b.get("text", "") for b in data.get("content", []))
                    compressed = text.strip()
                    # Trim compressed part to leave room for style anchor
                    max_compressed = 950 - len(STYLE_ANCHOR)
                    if len(compressed) > max_compressed:
                        cut = compressed[:max_compressed]
                        last_comma = cut.rfind(",")
                        compressed = cut[:last_comma] if last_comma != -1 else cut
                    final_prompt = STYLE_ANCHOR + compressed
                    self._json(200, {"prompt": final_prompt})
                    return
            except urllib.error.HTTPError as e:
                err_body = e.read().decode()
                if e.code == 529 or "overloaded" in err_body.lower():
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(3 * (attempt + 1))
                        continue
                self._json(e.code, {"error": f"Anthropic API error: {err_body}"})
                return
            except Exception as e:
                self._json(500, {"error": str(e)})
                return

    def _handle_generate_video(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception:
            self._json(400, {"error": "Invalid JSON"})
            return

        prompt = payload.get("prompt", "").strip()
        if not prompt:
            self._json(400, {"error": "prompt required"})
            return
        if not RUNWAY_API_KEY:
            self._json(500, {"error": "RUNWAY_API_KEY not set"})
            return

        api_payload = {
            "model": "gen4.5",
            "promptText": prompt,
            "duration": 5,
            "ratio": "1280:720"
        }
        req = urllib.request.Request(
            "https://api.dev.runwayml.com/v1/text_to_video",
            data=json.dumps(api_payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {str(RUNWAY_API_KEY).strip()}",
                "X-Runway-Version": "2024-11-06"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                self._json(200, {"task_id": data.get("id")})
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            self._json(e.code, {"error": f"Runway API error: {err_body}"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _handle_video_status(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception:
            self._json(400, {"error": "Invalid JSON"})
            return

        task_id = payload.get("task_id", "").strip()
        if not task_id:
            self._json(400, {"error": "task_id required"})
            return
        if not RUNWAY_API_KEY:
            self._json(500, {"error": "RUNWAY_API_KEY not set"})
            return

        req = urllib.request.Request(
            f"https://api.dev.runwayml.com/v1/tasks/{task_id}",
            headers={
                "Authorization": f"Bearer {str(RUNWAY_API_KEY).strip()}",
                "X-Runway-Version": "2024-11-06"
            },
            method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                status = data.get("status", "UNKNOWN")
                output = data.get("output") or []
                self._json(200, {
                    "status": status,
                    "url": output[0] if output else None,
                    "progress": data.get("progress", 0)
                })
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            self._json(e.code, {"error": f"Runway API error: {err_body}"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"\n🚀 Pipeline server on http://0.0.0.0:{PORT}")
    if not API_KEY:
        print("⚠️  ANTHROPIC_API_KEY not set")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
