def railway_set_service_variable(key: str, value: str) -> None:
    """
    Sets/updates a service variable in Railway via Public GraphQL API.
    Uses Railway-provided env vars: RAILWAY_PROJECT_ID / RAILWAY_ENVIRONMENT_ID / RAILWAY_SERVICE_ID.
    Requires RAILWAY_TOKEN.
    """
    if not RAILWAY_TOKEN:
        raise RuntimeError("RAILWAY_TOKEN is missing")

    project_id = os.environ.get("RAILWAY_PROJECT_ID", "").strip()
    environment_id = os.environ.get("RAILWAY_ENVIRONMENT_ID", "").strip()
    service_id = os.environ.get("RAILWAY_SERVICE_ID", "").strip() or RAILWAY_SERVICE_ID

    if not project_id or not environment_id or not service_id:
        raise RuntimeError(
            "Missing Railway IDs. Need RAILWAY_PROJECT_ID, RAILWAY_ENVIRONMENT_ID, RAILWAY_SERVICE_ID in env."
        )

    url = "https://backboard.railway.app/graphql/v2"

    # Official mutation per Railway docs
    query = """
    mutation variableUpsert($input: VariableUpsertInput!) {
      variableUpsert(input: $input)
    }
    """

    payload = {
        "query": query,
        "variables": {
            "input": {
                "projectId": project_id,
                "environmentId": environment_id,
                "serviceId": service_id,
                "name": key,
                "value": value,
            }
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {RAILWAY_TOKEN}")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            result = json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Railway HTTPError: {e.code} {e.reason} | {body}") from e

    if "errors" in result and result["errors"]:
        raise RuntimeError(f"Railway GraphQL errors: {result['errors']}")

    logging.info(f"Railway variableUpsert OK: {key}={value}")
