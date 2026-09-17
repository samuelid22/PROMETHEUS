from __future__ import annotations

import json


class FakeAPIError(Exception):
    def __init__(self, code: int):
        super().__init__(f"fake api error {code}")
        self.code = code


class FakeResponse:
    def __init__(self, text: str | None = None, finish_reason: str = "unknown"):
        self._text = text
        self.candidates = [type("Candidate", (), {"finish_reason": finish_reason})()]

    @property
    def text(self) -> str:
        if self._text is None:
            raise ValueError("no text part")
        return self._text


class FakeModels:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0
        self.last_kwargs = None

    def generate_content(self, *, model, contents, config=None):
        self.calls += 1
        self.last_kwargs = {"model": model, "contents": contents, "config": config}
        result = self.behavior(self.calls)
        if isinstance(result, Exception):
            raise result
        return result


class FakeClient:
    def __init__(self, behavior):
        self.models = FakeModels(behavior)


VALID_GLOBAL_JSON = json.dumps({
    "summary": "A generated test scene.",
    "categories": {
        "subject": {
            "observations": ["OBSERVATION: red cube centered at t=0.5s"],
            "inferences": ["INFERENCE: the cube is likely the intended hero subject"],
            "confidence": 0.9,
        },
        "lighting": {
            "observations": ["OBSERVATION: hard shadow at t=0.5s"],
            "inferences": ["INFERENCE: single key light from the right"],
            "confidence": 0.7,
        },
    },
})

VALID_SCENE_JSON = json.dumps({
    "description": "A red cube rotating on a dark desk while a lamp swings.",
    "categories": {
        "subjects": {
            "observations": ["OBSERVATION: red cube centered at t=0.5s"],
            "inferences": ["INFERENCE: the cube is likely the intended hero subject"],
            "confidence": 0.9,
        },
        "camera": {
            "observations": ["OBSERVATION: static framing across frames"],
            "inferences": ["INFERENCE: apparent locked-off wide shot"],
            "confidence": 0.6,
        },
    },
})
