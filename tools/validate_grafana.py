import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request


PANEL_IDS = {1, 4, 7, 10}


def build_queries(dashboard, datasource_uid):
    panels = [p for p in dashboard["panels"] if p["id"] in PANEL_IDS]
    if {p["id"] for p in panels} != PANEL_IDS:
        raise ValueError("The four required business panels are not all present")
    queries = []
    for panel in panels:
        if not panel["targets"]:
            raise ValueError(f"Panel {panel['id']} has no query targets")
        for target in panel["targets"]:
            instant = target.get("instant", False)
            queries.append({
                **target,
                "refId": f"{panel['id']}-{target['refId']}",
                "datasource": {"type": "prometheus", "uid": datasource_uid},
                "instant": instant,
                "range": not instant,
                "intervalMs": 10000,
                "maxDataPoints": 180,
            })
    return queries


def check_results(queries, payload, require_data):
    summaries = []
    errors = []
    for query in queries:
        ref = query["refId"]
        result = payload.get("results", {}).get(ref)
        if result is None:
            errors.append(f"{ref}: query result missing")
            continue
        if result.get("error") or result.get("status", 200) != 200:
            errors.append(f"{ref}: {result.get('error', result.get('status'))}")
            continue
        samples = 0
        for frame in result.get("frames", []):
            for index, field in enumerate(frame["schema"]["fields"]):
                if field["type"] == "number":
                    samples += sum(
                        type(value) in (int, float) and math.isfinite(value)
                        for value in frame["data"]["values"][index]
                    )
        if require_data and not samples:
            errors.append(f"{ref}: no numeric samples returned")
        summaries.append({"ref": ref, "samples": samples})
    if errors:
        raise ValueError("; ".join(errors))
    return summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--query-endpoint", required=True)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    credentials = subprocess.run(
        ["az", "account", "get-access-token", "--resource",
         "ce34e7e5-485f-4d76-964f-b3d2b16d1e4f",
         "--only-show-errors", "--output", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if credentials.returncode:
        raise ValueError(f"Grafana authentication failed: {credentials.stderr.strip()}")
    token = json.loads(credentials.stdout)["accessToken"]

    def request(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            args.endpoint.rstrip("/") + path,
            data=data,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            payload = error.read().decode()
            if path == "/api/ds/query" and error.code == 400:
                return json.loads(payload)
            raise ValueError(f"{path}: HTTP {error.code}: {payload[:1500]}") from error

    sources = [
        source for source in request("/api/datasources")
        if source["type"] == "prometheus"
        and source["url"].rstrip("/") == args.query_endpoint.rstrip("/")
    ]
    if len(sources) != 1:
        raise ValueError("Expected exactly one datasource for the configured metrics workspace")
    source = sources[0]
    if args.prepare:
        definition = Path(__file__).resolve().parents[1] / "dashboards/zava-shipment-flow.json"
        dashboard = json.loads(definition.read_text())["dashboard"]
    else:
        dashboard = request("/api/dashboards/uid/zava-shipment-flow")["dashboard"]
        variables = [
            var for var in dashboard["templating"]["list"]
            if var["name"] == "DS_PROMETHEUS"
        ]
        if len(variables) != 1 or variables[0]["current"].get("value") != source["uid"]:
            raise ValueError("Published dashboard is not bound to the configured metrics workspace")
        for panel in dashboard["panels"]:
            if panel["id"] in PANEL_IDS:
                if panel["datasource"]["uid"] not in ("${DS_PROMETHEUS}", source["uid"]):
                    raise ValueError(f"Panel {panel['id']} selects a different datasource")
                for target in panel["targets"]:
                    if target.get("datasource", panel["datasource"])["uid"] not in (
                        "${DS_PROMETHEUS}", source["uid"],
                    ):
                        raise ValueError(f"Panel {panel['id']} target selects a different datasource")
    queries = build_queries(dashboard, source["uid"])
    now = int(time.time() * 1000)
    started = time.monotonic()
    payload = request("/api/ds/query", {
        "from": str(now - 30 * 60 * 1000), "to": str(now), "queries": queries,
    })
    summaries = check_results(queries, payload, require_data=not args.prepare)
    print(json.dumps({
        "mode": "prepare-query-path" if args.prepare else "stage-panel-data",
        "datasource": source["uid"],
        "query_seconds": round(time.monotonic() - started, 2),
        "queries": summaries,
        "current_samples_required": not args.prepare,
    }))


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError, KeyError, urllib.error.URLError, TimeoutError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"Grafana acceptance failed: {error}", file=sys.stderr)
        sys.exit(1)
