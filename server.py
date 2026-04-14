import os
import json
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", 8765))
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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

    "visual": """You are the Visual Prompt Writer for a video ad agency.

VISUAL BRIEF: [Project]
PRIMARY MODELS: [Runway Gen-4 / Kling 1.6 / Real footage — specify per scene]

For each scene:
SCENE [N] — [Timestamp]
PROMPT: [Full generation prompt optimized for specified model]
NEGATIVE PROMPT: [What to explicitly exclude]
REFERENCE STYLE: [Real film, photographer, or visual artist]
PRODUCTION NOTE: [What cannot be AI-generated — flag for real footage]

VISUAL CONSISTENCY NOTES: [Color palette, lighting rules, typography]

HANDOFF TO PRODUCTION COORDINATOR: [Asset list — AI scenes, real footage, motion graphics]

Write prompts for the model not a human. Flag scenes requiring real footage immediately. No vague prompts.""",

    "production": """You are the Production Coordinator Agent for a video ad agency.

Assemble all upstream outputs into a single production brief for the production team (Indrajeet).

PRODUCTION BRIEF: [Project]
DATE: [Today's date]
STATUS: Ready for production / Pending approvals / Flagged issues

PROJECT SUMMARY: [3 lines — brand, concept, what we're making]

ASSET LIST:
- Real footage required:
- AI-generated scenes: [Model, scene number, prompt reference]
- Motion graphics:
- Sound design elements:
- Music:
- Typography/text animations:

PRODUCTION SEQUENCE: [Ordered list — dependencies noted]

OPEN FLAGS: [Red = blocking, Amber = needs decision]

BUDGET INDICATORS: [Red/Amber/Green per asset]

DELIVERY SPEC:
- Final format:
- Aspect ratios:
- Length:
- Platform requirements:

HANDOFF CHECKLIST:
[ ] Script locked
[ ] Sound brief complete
[ ] Visual prompts complete
[ ] Real footage sourced or scheduled
[ ] Client approvals needed before production starts

Flag contradictions across upstream outputs. If not production-ready, say so clearly."""
}

AGENT_ORDER = ["brief", "concept", "script", "sound", "visual", "production"]

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

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
                text = "".join(b.get("text", "") for b in data.get("content", []))
                self._json(200, {"result": text})
        except urllib.error.HTTPError as e:
            self._json(e.code, {"error": f"Anthropic API error: {e.read().decode()}"})
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
