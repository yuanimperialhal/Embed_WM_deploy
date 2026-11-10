"""Measure one streaming image request; Python standard library only."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request


def load_image(path):
    """Validate a local image's signature; do not trust its filename suffix."""
    image = Path(path)
    if not image.is_file():
        raise ValueError(f"Image file does not exist: {image}")
    raw = image.read_bytes()
    if raw.startswith(b"\xff\xd8\xff") and len(raw) > 3:
        mime = "image/jpeg"
    elif raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) > 8:
        mime = "image/png"
    elif len(raw) > 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError("Image must have a JPEG, PNG, or WebP signature")
    return raw, mime


def read_stream(response, result, start, on_text=None, echo=True):
    """Record first answer and reasoning text separately; reject incomplete SSE."""
    for line in response:
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            result["received_done"] = True
            break
        if not data:
            continue
        event = json.loads(data)
        if not isinstance(event, dict):
            raise ValueError("SSE data must be a JSON object")
        if event.get("error"):
            raise RuntimeError(f"Server stream error: {event['error']}")
        if event.get("usage") is not None:
            result["usage"] = event["usage"]
        for choice in event.get("choices") or []:
            if not isinstance(choice, dict):
                raise ValueError("Invalid choice in SSE response")
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise ValueError("Invalid delta in SSE response")
            for field, key, metric in (
                ("content", "answer", "first_text_seconds"),
                ("reasoning_content", "reasoning", "first_reasoning_seconds"),
                ("reasoning", "reasoning", "first_reasoning_seconds"),
            ):
                content = delta.get(field)
                if not content:
                    continue
                if not isinstance(content, str):
                    raise ValueError(f"Non-text delta.{field} in server response")
                if result[metric] is None:
                    result[metric] = time.perf_counter() - start
                result[key] += content
                if key == "answer":
                    if echo:
                        print(content, end="", flush=True)
                    if on_text is not None:
                        on_text(content)
            if choice.get("finish_reason"):
                result["finish_reason"] = choice["finish_reason"]
    if not result["received_done"]:
        raise RuntimeError("Stream ended before [DONE]; response is incomplete")
    if not result["answer"].strip():
        raise RuntimeError("Server returned no answer text")
    if not result["finish_reason"]:
        raise RuntimeError("Server returned no finish_reason")
    if result["finish_reason"] not in ("stop", "length"):
        raise RuntimeError(f"Unexpected finish_reason: {result['finish_reason']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="images/test.jpg")
    parser.add_argument("--url", default="http://127.0.0.1:8000/v1/chat/completions")
    parser.add_argument("--model", default="cosmos")
    parser.add_argument("--prompt", default="请用中文简洁描述图片中可以直接观察到的内容。")
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--output", default="logs/image-result.json")
    args = parser.parse_args(argv)
    if args.max_tokens <= 0 or args.timeout <= 0:
        parser.error("--max-tokens and --timeout must be positive")
    result = dict(status="error", image=str(Path(args.image).resolve()),
                  model=args.model, url=args.url, prompt=args.prompt,
                  max_tokens=args.max_tokens, temperature=0, timeout_seconds=args.timeout,
                  started_at=datetime.now(timezone.utc).isoformat(),
                  first_text_seconds=None, first_reasoning_seconds=None,
                  total_seconds=None, finish_reason=None, usage=None,
                  received_done=False, answer="", reasoning="")
    start = None
    exit_code = 1
    try:
        raw, mime = load_image(args.image)
        result.update(image_sha256=hashlib.sha256(raw).hexdigest(),
                      image_bytes=len(raw), image_mime=mime)
        data_url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
        payload = dict(model=args.model, temperature=0, max_tokens=args.max_tokens,
                       stream=True, stream_options={"include_usage": True}, messages=[
                           {"role": "user", "content": [
                               {"type": "image_url", "image_url": {"url": data_url}},
                               {"type": "text", "text": args.prompt}]}])
        request = urllib.request.Request(args.url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        start = time.perf_counter()
        with opener.open(request, timeout=args.timeout) as response:
            read_stream(response, result, start)
        exit_code = 2 if result["finish_reason"] == "length" else 0
        result["status"] = "truncated" if exit_code == 2 else "ok"
    except urllib.error.HTTPError as exc:
        result["error"] = f"HTTP {exc.code}: {exc.read(4096).decode(errors='replace')}"
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if start is not None:
            result["total_seconds"] = time.perf_counter() - start
    try:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"\nCannot save result: {exc}", file=sys.stderr)
        return 1
    print("\n" + json.dumps({k: v for k, v in result.items()
                             if k not in ("answer", "reasoning")}, ensure_ascii=False, indent=2))
    if exit_code == 2:
        print("注意：达到 token 或上下文上限，回答被截断；本次不是完整回答。", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
