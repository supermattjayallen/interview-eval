#!/usr/bin/env python3
"""CLI client for the Interview Evaluation Service."""

import argparse
import json
import sys
import time

import httpx


def poll_job(base_url: str, job_id: str, poll_interval: float = 3.0) -> dict:
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        while True:
            response = client.get(f"/analyze/{job_id}")
            response.raise_for_status()
            data = response.json()
            status = data["status"]
            message = data.get("message", "")
            print(f"[{status}] {message}", file=sys.stderr)

            if status == "completed":
                return data["result"]
            if status == "failed":
                raise RuntimeError(message or "Analysis failed")

            time.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze an interview recording")
    parser.add_argument("recording_url", help="URL to the interview recording")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--role-title", help="Job title")
    parser.add_argument("--role-description", help="Job description or required skills")
    parser.add_argument("--criteria", nargs="*", help="Custom evaluation criteria")
    parser.add_argument("--sync", action="store_true", help="Use synchronous endpoint (blocks until done)")
    parser.add_argument("--output", "-o", help="Write JSON result to file")
    args = parser.parse_args()

    payload = {
        "recording_url": args.recording_url,
        "role_title": args.role_title,
        "role_description": args.role_description,
        "evaluation_criteria": args.criteria,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    with httpx.Client(base_url=args.base_url, timeout=600.0) as client:
        if args.sync:
            response = client.post("/analyze/sync", json=payload)
            response.raise_for_status()
            result = response.json()
        else:
            response = client.post("/analyze", json=payload)
            response.raise_for_status()
            job_id = response.json()["job_id"]
            result = poll_job(args.base_url, job_id)

    output = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Result written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
