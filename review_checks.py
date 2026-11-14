"""Reproduce review findings without importing app.py or loading model pickles.

Uses audited source functions with explicit stubs. These are isolated logic
checks, not Flask, hardware, model-accuracy, or live-provider integration tests.
Run from any directory: python3 -B /absolute/path/to/OrbitNexus/review_checks.py
"""
import ast
import copy
import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent / "extracted"
SITE = ROOT / "ASTRA" / "website"


def extract(path, name, namespace, owner=None):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = tree.body
    if owner:
        nodes = next(n for n in nodes if isinstance(n, ast.ClassDef) and n.name == owner).body
    node = copy.deepcopy(next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name))
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


evidence = {}
python_files = list(ROOT.rglob("*.py"))
for path in python_files:
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
evidence["python_syntax_files_passed"] = len(python_files)

# Missing data and missing models both produce a nominal health summary.
ns = {}
process = extract(SITE / "astra_engine.py", "process_astronaut_telemetry", ns, "AstraIntelligenceEngine")
missing = SimpleNamespace(predict_stress=lambda _: {"error": "Stress model not loaded"},
                          detect_anomaly=lambda _: {"error": "Anomaly model not loaded"})
empty_result = process(missing, {})
missing_result = process(missing, {"stress_physio": [], "vitals": []})
assert "NOMINAL" in empty_result["mission_health_summary"]
assert "NOMINAL" in missing_result["mission_health_summary"]
evidence["empty_packet"] = empty_result
evidence["missing_models"] = missing_result

# Execute live endpoint logic with a deterministic data generator and model stub.
ns = {"telemetry_lock": threading.Lock(), "telemetry_state": {},
      "_generate_telemetry": lambda _: ({}, {"heart_rate_bpm": 123.0}),
      "engine": SimpleNamespace(process_astronaut_telemetry=lambda _: {"activity": "stub activity"}),
      "mission_elapsed": lambda: "00:01", "jsonify": lambda payload: payload,
      "smartwatch_state": {"connected": True}, "current_mode": "demo",
      "datetime": datetime, "timezone": timezone}
evaluate = extract(SITE / "app.py", "evaluate_disease_risks", ns)
live = extract(SITE / "app.py", "api_telemetry_live", ns)
live_result = live()
assert live_result["telemetry"]["heart_rate_bpm"] == 123.0
assert ns["telemetry_state"] == {}
captured = {}


def provider_stub(messages, **kwargs):
    captured["messages"] = messages
    return {"status": "unavailable", "reply": "Test stub; no network request."}


ns.update(request=SimpleNamespace(get_json=lambda **kw: {"message": "Check my readings"}),
          memory_manager=SimpleNamespace(build_context_prompt=lambda **kw: "No memory"),
          chat_lock=threading.Lock(), chat_history=[], SYSTEM_PROMPT="Test system",
          active_groq_model="test", invoke_groq_chat=provider_stub)
chat = extract(SITE / "app.py", "api_chat", ns)
chat()
assert "123" not in captured["messages"][0]["content"]
assert "Current telemetry snapshot" not in captured["messages"][0]["content"]
evidence["live_then_chat"] = {"displayed_hr": 123.0, "shared_state": ns["telemetry_state"],
                               "system_message": captured["messages"][0]["content"]}

# Auth hook allows an anonymous POST to chat.
guard_ns = {"request": SimpleNamespace(method="POST", path="/api/chat", headers={}),
            "security_manager": SimpleNamespace(audit_event=lambda *args: None)}
guard = extract(SITE / "app.py", "_security_guard", guard_ns)
assert guard() is None
evidence["anonymous_chat_guard"] = "allowed (None return)"

# Voice does not read the actual reply; it substitutes fixed sentences.
voice = extract(SITE / "voice_assistant.py", "build_reply", {"Dict": dict}, "VoiceAssistant")
spoken = voice(SimpleNamespace(voice_enabled=True), "No signs of stress were detected.")
assert spoken["text"].startswith("Stress appears elevated.")
evidence["voice_negation_mismatch"] = spoken

baseline = evaluate({})
radiation = next(d for d in baseline if d["id"] == "cosmic_radiation_dna")
changed = next(d for d in evaluate({"genomics": [999.0] * 50, "space_bio": {"alert": True}})
               if d["id"] == "cosmic_radiation_dna")
assert radiation == changed
evidence["radiation_score_ignores_genomics"] = radiation["risk_score"]

routes = defaultdict(list)
for node in ast.parse((SITE / "app.py").read_text()).body:
    if not isinstance(node, ast.FunctionDef):
        continue
    for decorator in node.decorator_list:
        if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "route" and decorator.args):
            routes[ast.literal_eval(decorator.args[0])].append(node.lineno)
evidence["duplicate_routes"] = {route: lines for route, lines in routes.items() if len(lines) > 1}
assert "/api/sensors/overview" in evidence["duplicate_routes"]

nb = json.loads((ROOT / "ASTRA/notebooks/ASTRA_AI_Training.ipynb").read_text())
code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
assert "X_hr = np.column_stack([pleth_mean, pleth_std, pleth_p2p, pleth_rms, resp_mean, resp_std])" in code
meta = json.loads((ROOT / "ASTRA/models/heart_model/heart_meta.json").read_text())
assert len(meta["features"]) == 9
evidence["heart_feature_counts"] = {"notebook": 6, "shipped_metadata": 9}
evidence["dataset_files"] = len([p for p in (ROOT / "ASTRA/datasets").rglob("*") if p.is_file()])

print(json.dumps(evidence, indent=2, ensure_ascii=False))
