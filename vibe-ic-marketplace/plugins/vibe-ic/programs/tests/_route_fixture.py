"""Owner-declared delivery route for synthetic front-door project inputs."""
import json
from pathlib import Path


def stage_owner_route(project: Path, route: str) -> None:
    delivery = {"ic": "DIE", "ip": "HARDMACRO"}[route]
    path = project / "input/step_0_5ic_answers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = json.loads(path.read_text()) if path.exists() else {"answers": {}}
    existing = doc.get("answers", {}).get("deliverable")
    if existing not in (None, delivery):
        raise ValueError(f"fixture route {route} conflicts with {existing}")
    doc["answers"]["deliverable"] = delivery
    doc.setdefault("answer_provenance", {})["deliverable"] = {
        "answered_by": "owner",
        "citation": f"Owner: deliverable is {delivery} for this test project.",
    }
    path.write_text(json.dumps(doc) + "\n")
