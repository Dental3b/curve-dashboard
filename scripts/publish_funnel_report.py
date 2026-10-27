"""Publish a validated aggregate snapshot using the operator's gcloud identity."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from whatsapp_webhook.funnel_report import validate_report


def firestore_value(value):
    if value is None:
        return {"nullValue": None}
    if type(value) is int:
        return {"integerValue": str(value)}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {k: firestore_value(v) for k, v in value.items()}}}
    raise ValueError("Unsupported aggregate value")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Aggregate report JSON file, or - for stdin")
    parser.add_argument("--project", required=True)
    parser.add_argument("--collection", default="d3b_whatsapp_funnel_reports")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = validate_report(json.load(sys.stdin) if args.file == "-" else json.loads(Path(args.file).read_text()))
    if args.dry_run:
        print("Validated aggregate report; no data written.")
        return
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    path = "/".join(quote(v, safe="") for v in (args.collection, report["start"] + "_" + report["end"]))
    url = "https://firestore.googleapis.com/v1/projects/" + quote(args.project, safe="") + "/databases/(default)/documents/" + path
    document = {"fields": {k: firestore_value(v) for k, v in report.items()}}
    request = Request(url, data=json.dumps(document).encode(), method="PATCH", headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    with urlopen(request, timeout=30) as response:
        response.read()
    print("Verified aggregate report published.")


if __name__ == "__main__":
    main()
