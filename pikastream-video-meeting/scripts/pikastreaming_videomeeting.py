#!/usr/bin/env python
# Copyright 2026 Pika Labs, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PikaStreaming Video Meeting — join or leave a meeting via PikaStreaming API.

Authenticates through the Pika API proxy (PIKA_API_BASE_URL + PIKA_DEV_KEY).

Requires PIKA_DEV_KEY to be set in the environment before running. When called
non-interactively (e.g. from an agent or CI), the key MUST be pre-configured —
stdin prompts will block indefinitely without a TTY.

Usage:
  python pikastreaming_videomeeting.py join --meet-url <url> --bot-name <name> [--voice-id <id>] [--image <path>] [--meeting-password <pw>] [--system-prompt <desc>] [--system-prompt-file <path>] [--timeout-sec 90]
  python pikastreaming_videomeeting.py leave --session-id <id>

Exit codes: 0=ok, 2=validation, 3=http, 4=session error, 5=timeout, 6=funding (checkout URL in stdout JSON)
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

import requests

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = SKILL_DIR / "assets" / "placeholder-avatar.jpg"
DEFAULT_VOICE = "English_radiant_girl"
DEFAULT_API_BASE = "https://srkibaanghvsriahb.pika.art"
DEFAULT_VIDEO_API_BASE = "https://7i30hpv4bo9ud5mhianq.pika.art"


def eprint(*a):
    print(*a, file=sys.stderr)


def get_api_config() -> tuple[str, dict[str, str]]:
    """Return (api_base, headers) using DevKey."""
    base_url = os.environ.get("PIKA_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")
    api_base = f"{base_url}/proxy/realtime"

    dev_key = os.environ.get("PIKA_DEV_KEY", "").strip()
    if not dev_key:
        eprint("Error: PIKA_DEV_KEY is required. Get one at https://www.pika.me/dev/login")
        sys.exit(1)
    headers = {"Authorization": f"DevKey {dev_key}"}

    return api_base, headers


def infer_platform(url: str) -> str | None:
    u = url.lower()
    if "meet.google.com" in u:
        return "google_meet"
    if "zoom.us" in u or "zoom.com" in u:
        return "zoom"
    return None


DEVKEY_FILE = Path.home() / ".pika" / "devkey"
SIGNUP_URL = "https://www.pika.me/dev/login"


def get_devkey() -> str:
    """Get DevKey from env or local file. Returns empty string if not found."""
    key = os.environ.get("PIKA_DEV_KEY", "").strip()
    if key:
        return key
    if DEVKEY_FILE.exists():
        key = DEVKEY_FILE.read_text().strip()
    return key


def check_balance(base_url: str, dev_key: str) -> int | None:
    """Return balance or None on error."""
    try:
        r = requests.get(
            f"{base_url}/developer/balance",
            headers={"Authorization": f"DevKey {dev_key}"},
            timeout=15,
        )
        if r.ok:
            return r.json().get("data", r.json()).get("balance", 0)
        eprint(f"Balance check failed: HTTP {r.status_code}")
        return None
    except Exception as e:
        eprint(f"Balance check error: {e}")
        return None


def ensure_funded(min_balance: int = 100, poll_interval: int = 10, poll_timeout: int = 300) -> bool:
    """Full funding pipeline. Returns True when funded, False on failure.

    Flow:
      1. Check DevKey exists → if not, output signup URL and wait for user to provide key
      2. Check balance → if funded, return True
      3. Auto-create topup checkout → output URL and poll until funded
    """
    base_url = os.environ.get("PIKA_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")

    # --- Step 1: Ensure DevKey exists ---
    dev_key = get_devkey()
    if not dev_key:
        print(json.dumps({
            "status": "no_devkey",
            "message": f"No Pika Developer Key found. Sign up at {SIGNUP_URL} to get your API key, then set PIKA_DEV_KEY in the environment.",
            "signup_url": SIGNUP_URL,
        }))
        return False

    eprint(f"DevKey: dk_...{dev_key[-4:]}")
    auth_headers = {"Authorization": f"DevKey {dev_key}"}

    # --- Step 2: Check balance ---
    balance = check_balance(base_url, dev_key)
    if balance is None:
        return False

    if balance >= min_balance:
        eprint(f"Balance OK: {balance} credits")
        print(json.dumps({"status": "funded", "balance": balance}))
        return True

    eprint(f"Balance ({balance}) below minimum ({min_balance}). Creating topup...")

    # --- Step 3: Get products and create checkout ---
    try:
        r = requests.get(f"{base_url}/developer/topup/products", headers=auth_headers, timeout=15)
        products = r.json().get("data", r.json()).get("products", []) if r.ok else []
    except Exception:
        products = []

    if not products:
        print(json.dumps({
            "status": "needs_topup",
            "balance": balance,
            "message": f"No credit packs available. Visit {SIGNUP_URL} to top up.",
        }))
        return False

    # Pick smallest sufficient product
    deficit = min_balance - balance
    products_sorted = sorted(products, key=lambda p: p["numCredits"])
    chosen = next((p for p in products_sorted if p["numCredits"] >= deficit), products_sorted[-1])

    # Create checkout session
    try:
        r = requests.post(
            f"{base_url}/developer/topup",
            headers={**auth_headers, "Content-Type": "application/json"},
            json={"product_id": chosen["productId"]},
            timeout=15,
        )
        checkout_url = r.json().get("data", r.json()).get("checkout_url", "") if r.ok else ""
    except Exception:
        checkout_url = ""

    if not checkout_url:
        print(json.dumps({"status": "needs_topup", "balance": balance, "message": "Failed to create checkout"}))
        return False

    print(json.dumps({
        "status": "needs_topup",
        "balance": balance,
        "product": chosen["name"],
        "credits": chosen["numCredits"],
        "checkout_url": checkout_url,
        "message": f"Open the checkout URL to purchase {chosen['name']}. Waiting for payment...",
    }))
    sys.stdout.flush()

    # --- Step 4: Poll until funded ---
    eprint(f"Waiting for payment (timeout: {poll_timeout}s)...")
    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        balance = check_balance(base_url, dev_key)
        if balance is not None and balance >= min_balance:
            eprint(f"Payment received! Balance: {balance}")
            print(json.dumps({"status": "funded", "balance": balance}))
            return True
        eprint(f"  balance: {balance or '?'} — waiting...")

    print(json.dumps({"status": "payment_timeout", "message": "Payment not completed in time. Try again."}))
    return False


def cmd_join(args):
    api_base, auth_headers = get_api_config()

    # MANDATORY: full funding pipeline before every join
    if not ensure_funded(min_balance=100):
        return 6

    platform = args.platform or infer_platform(args.meet_url)
    if not platform:
        eprint("Error: can't infer platform from URL; pass --platform")
        return 2

    image_src = args.image or str(DEFAULT_IMAGE)

    # Resolve system prompt: --system-prompt-file takes priority over --system-prompt
    system_prompt = args.system_prompt
    if args.system_prompt_file:
        sp_path = Path(args.system_prompt_file)
        if not sp_path.exists():
            eprint(f"Error: system prompt file not found: {sp_path}")
            return 2
        system_prompt = sp_path.read_text().strip()

    tmp_image = None
    if image_src.startswith(("http://", "https://")):
        import tempfile
        eprint(f"Downloading image: {image_src[:80]}...")
        try:
            r = requests.get(image_src, timeout=15)
            r.raise_for_status()
        except Exception as e:
            eprint(f"Error: failed to download image: {e}")
            return 2
        suffix = Path(image_src.split("?")[0]).suffix or ".png"
        fd, tmp_name = tempfile.mkstemp(suffix=suffix)
        tmp = Path(tmp_name)
        os.write(fd, r.content)
        os.close(fd)
        img = tmp
        tmp_image = tmp
    else:
        img = Path(image_src)

    try:
        if not img.exists() or img.stat().st_size == 0:
            eprint(f"Error: image missing: {img}")
            return 2

        voice = args.voice_id or DEFAULT_VOICE
        mime = mimetypes.guess_type(str(img))[0] or "application/octet-stream"

        with img.open("rb") as fh:
            resp = requests.post(
                f"{api_base}/meeting-session",
                headers=auth_headers,
                files={"image": (img.name, fh, mime)},
                data={
                    "voice_id": voice,
                    "meet_url": args.meet_url,
                    "bot_name": args.bot_name,
                    "platform": platform,
                    **({"meeting_password": args.meeting_password} if args.meeting_password else {}),
                    **({"system_prompt": system_prompt} if system_prompt else {}),
                },
                timeout=180,
            )
    finally:
        if tmp_image and tmp_image.exists():
            tmp_image.unlink(missing_ok=True)

    if not resp.ok:
        eprint(f"Error: HTTP {resp.status_code}: {resp.text[:300]}")
        return 3

    sid = resp.json().get("session_id")
    if not sid:
        eprint(f"Error: no session_id: {resp.text[:300]}")
        return 3

    # Print session_id immediately so agent can capture it
    print(json.dumps({"session_id": sid, "platform": platform, "status": "created"}))
    sys.stdout.flush()

    # Poll — only print on status change to reduce noise
    poll_url = f"{api_base}/session/{sid}"
    deadline = time.time() + args.timeout_sec
    last_status = None

    while time.time() < deadline:
        time.sleep(2)
        try:
            r = requests.get(poll_url, headers=auth_headers, timeout=15)
            if not r.ok:
                continue
            d = r.json()
        except (requests.RequestException, ValueError):
            continue
        status = d.get("status")
        video = bool(d.get("video_worker_connected") or d.get("video_connected"))
        bot = bool(d.get("meeting_bot_connected"))

        if status != last_status:
            print(json.dumps({"session_id": sid, "status": status, "video": video, "bot": bot}))
            sys.stdout.flush()
            last_status = status

        if status == "ready" or (video and bot):
            return 0
        if status in ("error", "closed"):
            eprint(f"Error: {status}: {d.get('error_message', '')}")
            return 4

    eprint("Error: timeout")
    return 5


def cmd_generate_avatar(args):
    """Generate a default avatar image via OpenAI proxy."""
    dev_key = os.environ.get("PIKA_DEV_KEY", "")
    base_url = os.environ.get("PIKA_VIDEO_API_BASE_URL", DEFAULT_VIDEO_API_BASE).rstrip("/")
    if not dev_key:
        eprint("Error: PIKA_DEV_KEY is required")
        return 1

    prompt = args.prompt or (
        "A friendly, approachable portrait headshot of a virtual assistant avatar. "
        "Simple, clean background. Professional but warm expression. "
        "Photorealistic style, good lighting, centered face, suitable for video calls."
    )

    eprint("Generating avatar image...")
    try:
        resp = requests.post(
            f"{base_url}/proxy/openai/v1/images/generations",
            headers={
                "Authorization": f"DevKey {dev_key}",
                "Content-Type": "application/json",
                "X-Generation-Type": "image",
                "X-Model": args.model,
            },
            json={
                "model": args.model,
                "prompt": prompt,
                "size": "1024x1024",
                "n": 1,
                "quality": "medium",
                "output_format": "png",
            },
            timeout=120,
        )
    except requests.RequestException as e:
        eprint(f"Error: request failed: {e}")
        return 3

    if not resp.ok:
        eprint(f"Error: HTTP {resp.status_code}: {resp.text[:300]}")
        return 3

    try:
        data = resp.json()["data"][0]
    except (KeyError, IndexError, ValueError):
        eprint(f"Error: unexpected response: {resp.text[:300]}")
        return 3

    import base64

    if "b64_json" in data:
        img_bytes = base64.b64decode(data["b64_json"])
    elif "url" in data:
        eprint("Downloading generated image...")
        try:
            dl = requests.get(data["url"], timeout=30)
            dl.raise_for_status()
            img_bytes = dl.content
        except Exception as e:
            eprint(f"Error: failed to download: {e}")
            return 3
    else:
        eprint("Error: no image in response")
        return 3

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(img_bytes)
    print(json.dumps({"path": str(out), "size_bytes": len(img_bytes)}))
    return 0


NATIVE_AUDIO_FORMATS = [".mp3", ".m4a", ".wav"]
CONVERTIBLE_AUDIO_FORMATS = [".ogg", ".mp4", ".webm", ".mkv", ".mov", ".flac", ".aac", ".wma", ".opus"]


def convert_to_mp3(input_file: str) -> str:
    """Convert audio file to mp3 using ffmpeg. Returns path to converted file."""
    import subprocess
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        subprocess.run(
            ["ffmpeg", "-i", input_file, "-codec:a", "libmp3lame", "-b:a", "128k", tmp, "-y"],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        os.unlink(tmp)
        eprint("Error: ffmpeg is required for audio conversion but was not found.")
        return ""
    except subprocess.CalledProcessError as e:
        os.unlink(tmp)
        eprint(f"Error: ffmpeg conversion failed: {e.stderr.decode()}")
        return ""
    return tmp


def prepare_audio(audio_path: str) -> str | None:
    """Ensure audio is in a format the APIs accept. Returns path (may be a temp file)."""
    ext = os.path.splitext(audio_path)[1].lower()
    if ext in NATIVE_AUDIO_FORMATS:
        return audio_path
    if ext in CONVERTIBLE_AUDIO_FORMATS:
        eprint(f"Converting {ext} to mp3...")
        converted = convert_to_mp3(audio_path)
        return converted or None
    eprint(f"Warning: {ext} may not be supported. Attempting upload anyway...")
    return audio_path


def clone_voice(base_url: str, api_key: str, audio_path: str, voice_name: str,
                noise_reduction: bool = False) -> dict | None:
    """Clone a voice via the Pika voice proxy."""
    from datetime import datetime, timezone

    headers = {"Authorization": f"DevKey {api_key}"}
    ext = os.path.splitext(audio_path)[1].lower()
    content_type = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav"}.get(ext, "audio/mpeg")

    eprint("Uploading audio for voice cloning...")
    try:
        with open(audio_path, "rb") as f:
            resp = requests.post(
                f"{base_url}/proxy/minimax/v1/files/upload",
                headers=headers,
                files={"file": (os.path.basename(audio_path), f, content_type)},
                data={"purpose": "voice_clone"},
                timeout=60,
            )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        eprint(f"Error: upload failed: {e}")
        return None

    if "base_resp" in result:
        if result["base_resp"].get("status_code", 0) != 0:
            eprint(f"Error: voice upload: {result['base_resp'].get('status_msg', 'unknown')}")
            return None

    file_id = None
    if "file" in result and isinstance(result["file"], dict):
        file_id = result["file"].get("file_id")
    file_id = file_id or result.get("file_id") or result.get("id")
    if not file_id:
        eprint(f"Error: no file_id in upload response: {json.dumps(result)[:200]}")
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    voice_id = f"voice_{voice_name}_{timestamp}"

    eprint(f"Cloning voice as {voice_id}...")
    try:
        resp = requests.post(
            f"{base_url}/proxy/minimax/v1/voice_clone",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "file_id": file_id,
                "voice_id": voice_id,
                "model": "speech-2.8-hd",
                "need_noise_reduction": noise_reduction,
                "need_volume_normalization": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        eprint(f"Error: clone failed: {e}")
        return None

    if "base_resp" in result:
        if result["base_resp"].get("status_code", 0) != 0:
            eprint(f"Error: voice clone: {result['base_resp'].get('status_msg', 'unknown')}")
            return None

    return {
        "voice_id": voice_id,
        "provider": "pika",
        "cloned_at": datetime.now(timezone.utc).isoformat(),
        "source_audio": os.path.basename(audio_path),
        "retention_warning": "Cloned voices may be deleted after 7 days of non-use",
    }


def cmd_clone_voice(args):
    """Clone a voice from an audio file via the Pika voice proxy."""
    dev_key = os.environ.get("PIKA_DEV_KEY", "")
    base_url = os.environ.get("PIKA_VIDEO_API_BASE_URL", DEFAULT_VIDEO_API_BASE).rstrip("/")
    if not dev_key:
        eprint("Error: PIKA_DEV_KEY is required")
        return 1

    audio_path = args.audio
    if not os.path.exists(audio_path):
        eprint(f"Error: audio file not found: {audio_path}")
        return 2

    prepared = prepare_audio(audio_path)
    if not prepared:
        return 2
    tmp_audio = prepared if prepared != audio_path else None

    try:
        result = clone_voice(base_url, dev_key, prepared, args.name,
                             noise_reduction=args.noise_reduction)
    finally:
        if tmp_audio and os.path.exists(tmp_audio):
            os.unlink(tmp_audio)

    if not result:
        return 3

    life_dir = Path("life")
    life_dir.mkdir(parents=True, exist_ok=True)
    (life_dir / "voice_id.txt").write_text(result["voice_id"])

    config = {
        "voice_id": result["voice_id"],
        "provider": result["provider"],
        "cloned_at": result["cloned_at"],
        "source_audio": result["source_audio"],
    }
    if "retention_warning" in result:
        config["retention_warning"] = result["retention_warning"]

    with open(life_dir / "voice_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print(json.dumps(result))
    return 0


def cmd_leave(args):
    api_base, auth_headers = get_api_config()

    r = requests.delete(
        f"{api_base}/session/{args.session_id}",
        headers=auth_headers,
        timeout=30,
    )
    if not r.ok:
        eprint(f"Error: HTTP {r.status_code}: {r.text[:300]}")
        return 3

    print(json.dumps({"session_id": args.session_id, "closed": True}))
    return 0


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    j = sub.add_parser("join")
    j.add_argument("--meet-url", required=True)
    j.add_argument("--platform", choices=["google_meet", "zoom"])
    j.add_argument("--bot-name", required=True)
    j.add_argument("--voice-id")
    j.add_argument("--meeting-password")
    j.add_argument("--system-prompt", help="System prompt text for the meeting bot")
    j.add_argument("--system-prompt-file", help="Path to file containing fallback system prompt")
    j.add_argument("--image")
    j.add_argument("--timeout-sec", type=int, default=90)

    lv = sub.add_parser("leave")
    lv.add_argument("--session-id", required=True)

    ga = sub.add_parser("generate-avatar")
    ga.add_argument("--output", required=True, help="Path to save the generated image")
    ga.add_argument("--prompt", help="Custom prompt for avatar generation")
    ga.add_argument("--model", default="gpt-image-1-mini", help="OpenAI image model")

    cv = sub.add_parser("clone-voice")
    cv.add_argument("--audio", required=True, help="Audio file (10s-5min, clear speech)")
    cv.add_argument("--name", required=True, help="Name for the cloned voice")
    cv.add_argument("--noise-reduction", action="store_true", help="Enable noise reduction")

    args = p.parse_args()
    cmds = {
        "join": cmd_join,
        "leave": cmd_leave,
        "generate-avatar": cmd_generate_avatar,
        "clone-voice": cmd_clone_voice,
    }
    return cmds[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
