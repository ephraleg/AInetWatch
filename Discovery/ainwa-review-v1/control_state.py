"""State and workflow helpers for the AINWA four-column control center."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

WORKFLOW_LOCK = threading.RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class ControlState:
    LOCATIONS = ("scanned", "candidates", "standby", "publish_queue")

    def __init__(self, data_dir: Path):
        self.root = data_dir
        self.state_dir = data_dir / "state"
        self.logs_dir = data_dir / "logs"
        self.locks_dir = data_dir / "locks"
        self.files = {
            "scanned": self.state_dir / "scanned-stories.json",
            "candidates": self.state_dir / "candidate-queue.json",
            "standby": self.state_dir / "standby-queue.json",
            "publish_queue": self.state_dir / "publish-queue.json",
            "history": self.state_dir / "sourcing-history.json",
            "usage": self.logs_dir / "ai-usage.json",
            "events": self.logs_dir / "operation-log.json",
        }

    def ensure(self):
        for directory in (self.root, self.state_dir, self.logs_dir, self.locks_dir, self.root / "backups"):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for key in self.LOCATIONS:
            if not self.files[key].exists():
                write_json(self.files[key], {"version": 1, "updated_at": None, "stories": []})
        if not self.files["history"].exists():
            write_json(self.files["history"], {"version": 1, "exact": {}, "similar": {}})
        if not self.files["usage"].exists():
            write_json(self.files["usage"], {"version": 1, "events": []})
        if not self.files["events"].exists():
            write_json(self.files["events"], {"version": 1, "events": []})

    def _stories(self, location):
        payload = read_json(self.files[location], {"stories": []})
        if not isinstance(payload, dict):
            return []
        if location == "candidates":
            return payload.get("candidates", payload.get("stories", [])) or []
        return payload.get("stories", []) or []

    def _save(self, location, stories):
        key = "candidates" if location == "candidates" else "stories"
        write_json(self.files[location], {"version": 1, "updated_at": now_iso(), key: stories})

    def snapshot(self):
        with WORKFLOW_LOCK:
            data = {key: self._stories(key) for key in self.LOCATIONS}
            seen = {}
            duplicates = []
            for location, stories in data.items():
                for story in stories:
                    sid = str(story.get("id") or "")
                    if not sid:
                        continue
                    if sid in seen:
                        duplicates.append({"id": sid, "locations": [seen[sid], location]})
                    seen[sid] = location
            if duplicates:
                raise ValueError(f"stories exist in multiple workflow locations: {duplicates}")
            usage = self.usage_today()
            return {**data, "counts": {k: len(v) for k, v in data.items()}, "ai_usage_today": usage}

    def find(self, story_id):
        for location in self.LOCATIONS:
            for story in self._stories(location):
                if str(story.get("id")) == str(story_id):
                    return location, story
        return None, None

    def move(self, story_id, destination):
        if destination not in self.LOCATIONS:
            raise ValueError("invalid destination")
        with WORKFLOW_LOCK:
            source, story = self.find(story_id)
            if not story:
                raise KeyError(story_id)
            if source == destination:
                return story
            source_stories = [s for s in self._stories(source) if str(s.get("id")) != str(story_id)]
            dest_stories = self._stories(destination)
            if any(str(s.get("id")) == str(story_id) for s in dest_stories):
                raise ValueError("story already exists at destination")
            story = dict(story)
            story["workflow_location"] = destination
            story["workflow_updated_at"] = now_iso()
            dest_stories.append(story)
            self._save(source, source_stories)
            self._save(destination, dest_stories)
            self.log("move", story_id=story_id, source=source, destination=destination)
            return story

    def add(self, story, destination="scanned"):
        if destination not in self.LOCATIONS:
            raise ValueError("invalid destination")
        with WORKFLOW_LOCK:
            sid = str(story.get("id") or "")
            if not sid:
                raise ValueError("story id is required")
            if self.find(sid)[1]:
                raise ValueError("story already exists in the workflow")
            stories = self._stories(destination)
            item = dict(story)
            item["workflow_location"] = destination
            item["workflow_updated_at"] = now_iso()
            stories.append(item)
            self._save(destination, stories)
            self.log("add", story_id=sid, destination=destination)
            return item

    def update_story(self, story_id, edits):
        with WORKFLOW_LOCK:
            location, story = self.find(story_id)
            if not story:
                raise KeyError(story_id)
            stories = self._stories(location)
            updated = None
            for index, item in enumerate(stories):
                if str(item.get("id")) == str(story_id):
                    updated = dict(item)
                    proposal = dict(updated.get("proposal") or {})
                    proposal.update(edits)
                    updated["proposal"] = proposal
                    updated["workflow_updated_at"] = now_iso()
                    stories[index] = updated
                    break
            self._save(location, stories)
            self.log("edit", story_id=story_id, location=location)
            return updated

    def clear(self, location):
        if location not in ("scanned", "candidates", "standby"):
            raise ValueError("invalid clear target")
        with WORKFLOW_LOCK:
            stories = self._stories(location)
            if location == "standby":
                existing = self._stories("candidates")
                ids = {str(s.get("id")) for s in existing}
                for story in stories:
                    if str(story.get("id")) not in ids:
                        moved = dict(story)
                        moved["workflow_location"] = "candidates"
                        existing.append(moved)
                self._save("candidates", existing)
                result = "returned_to_candidates"
            else:
                history = read_json(self.files["history"], {"version": 1, "exact": {}, "similar": {}})
                for story in stories:
                    source = story.get("source") or {}
                    url = source.get("url") or story.get("url")
                    if url:
                        history.setdefault("exact", {})[url] = {"at": now_iso(), "reason": f"clear_{location}"}
                write_json(self.files["history"], history)
                result = "returned_to_history"
            self._save(location, [])
            self.log(f"clear_{location}", count=len(stories), result=result)
            return {"count": len(stories), "result": result}

    def reject(self, story_id, reason=""):
        with WORKFLOW_LOCK:
            location, story = self.find(story_id)
            if not story:
                raise KeyError(story_id)
            self._save(location, [s for s in self._stories(location) if str(s.get("id")) != str(story_id)])
            history = read_json(self.files["history"], {"version": 1, "exact": {}, "similar": {}})
            url = (story.get("source") or {}).get("url") or story.get("url")
            if url:
                history.setdefault("exact", {})[url] = {"at": now_iso(), "reason": "rejected"}
            write_json(self.files["history"], history)
            self.log("reject", story_id=story_id, source=location, reason=reason)

    def log(self, action, **fields):
        payload = read_json(self.files["events"], {"version": 1, "events": []})
        payload.setdefault("events", []).append({"action": action, "at": now_iso(), **fields})
        write_json(self.files["events"], payload)

    def record_usage(self, operation, provider, model, input_tokens=0, output_tokens=0, estimated_cost=0.0):
        payload = read_json(self.files["usage"], {"version": 1, "events": []})
        payload.setdefault("events", []).append({
            "at": now_iso(), "operation": operation, "provider": provider, "model": model,
            "input_tokens": int(input_tokens or 0), "output_tokens": int(output_tokens or 0),
            "estimated_cost": round(float(estimated_cost or 0), 6),
        })
        write_json(self.files["usage"], payload)

    def usage_today(self):
        today = datetime.now(timezone.utc).date().isoformat()
        events = read_json(self.files["usage"], {"events": []}).get("events", [])
        events = [e for e in events if str(e.get("at", "")).startswith(today)]
        return {
            "input_tokens": sum(int(e.get("input_tokens", 0)) for e in events),
            "output_tokens": sum(int(e.get("output_tokens", 0)) for e in events),
            "estimated_cost": round(sum(float(e.get("estimated_cost", 0)) for e in events), 4),
        }
