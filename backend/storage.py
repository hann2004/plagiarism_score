"""
Deliberately simple storage: one JSON file per batch, plus an index file
listing all cohorts and batches. Good enough for weekly batches across
a handful of cohorts; swap for a real database later without changing
the API shape much.
"""
import json
from pathlib import Path


class Storage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.index_path = data_dir / "index.json"
        if not self.index_path.exists():
            self._write_index({"cohorts": {}})

    def _read_index(self) -> dict:
        with open(self.index_path) as f:
            return json.load(f)

    def _write_index(self, index: dict):
        with open(self.index_path, "w") as f:
            json.dump(index, f, indent=2)

    def _batch_path(self, cohort_id: str, batch_id: str) -> Path:
        return self.data_dir / f"{cohort_id}__{batch_id}.json"

    def _submissions_path(self, cohort_id: str, batch_id: str) -> Path:
        return self.data_dir / f"{cohort_id}__{batch_id}__submissions.json"

    def cleanup_empty_batches(self) -> dict:
        index = self._read_index()
        cohorts = index.get("cohorts", {})
        modified = False

        cohort_ids = list(cohorts.keys())
        for cohort_id in cohort_ids:
            cohort = cohorts[cohort_id]
            batches = cohort.get("batches", {})
            batch_ids = list(batches.keys())
            for batch_id in batch_ids:
                batch_meta = batches[batch_id]
                batch_data = self.get_batch(cohort_id, batch_id)
                is_empty = (
                    (not batch_data and batch_meta.get("studentCount", 0) == 0) or
                    (batch_data and len(batch_data.get("pairs", [])) == 0 and batch_meta.get("studentCount", 0) == 0)
                )
                if is_empty:
                    del batches[batch_id]
                    modified = True

            if not batches:
                del cohorts[cohort_id]
                modified = True

        if modified:
            self._write_index(index)
        return {"cohorts": index.get("cohorts", {}), "pinned_batch": index.get("pinned_batch")}

    def list_cohorts(self) -> dict:
        index = self._read_index()
        modified = False
        for cohort_id, cohort in index.get("cohorts", {}).items():
            for batch_id, batch_meta in cohort.get("batches", {}).items():
                batch_data = self.get_batch(cohort_id, batch_id)
                if batch_data and "pairs" in batch_data:
                    fc = sum(1 for p in batch_data["pairs"] if p.get("similarity", 0) >= 80)
                    if batch_meta.get("flaggedCount") != fc:
                        batch_meta["flaggedCount"] = fc
                        modified = True
        if modified:
            self._write_index(index)
        return {"cohorts": index.get("cohorts", {}), "pinned_batch": index.get("pinned_batch")}

    def get_batch(self, cohort_id: str, batch_id: str):
        path = self._batch_path(cohort_id, batch_id)
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)

    def set_pair_status(self, cohort_id: str, batch_id: str, pair_id: str, status: str) -> bool:
        batch = self.get_batch(cohort_id, batch_id)
        if not batch:
            return False
        found = False
        for p in batch["pairs"]:
            if p["id"] == pair_id:
                p["status"] = status
                found = True
                break
        if found:
            with open(self._batch_path(cohort_id, batch_id), "w") as f:
                json.dump(batch, f, indent=2)
        return found

    def get_submissions(self, cohort_id: str, batch_id: str) -> list:
        path = self._submissions_path(cohort_id, batch_id)
        if not path.exists():
            return []
        with open(path) as f:
            return json.load(f)

    def add_submission(self, cohort_id: str, batch_id: str, student_name: str, github_link: str, doc_link: str):
        subs = self.get_submissions(cohort_id, batch_id)
        # Upsert by student_name without overwriting existing links with empty strings
        found = False
        for s in subs:
            if s["student_name"].lower() == student_name.lower():
                if github_link and github_link.strip():
                    s["github_link"] = github_link.strip()
                if doc_link and doc_link.strip():
                    s["doc_link"] = doc_link.strip()
                found = True
                break
        if not found:
            subs.append({
                "student_name": student_name,
                "github_link": github_link.strip() if github_link else "",
                "doc_link": doc_link.strip() if doc_link else ""
            })
        
        with open(self._submissions_path(cohort_id, batch_id), "w") as f:
            json.dump(subs, f, indent=2)

        # Update index to reflect collected count so it appears in the frontend before run
        index = self._read_index()
        # Fallback labels if not set
        cohort = index["cohorts"].setdefault(cohort_id, {"label": cohort_id, "batches": {}})
        batch_entry = cohort["batches"].setdefault(batch_id, {
            "label": batch_id,
            "studentCount": 0,
            "flaggedCount": 0,
        })
        batch_entry["collectedCount"] = len(subs)
        self._write_index(index)

    def save_run(self, cohort_id, cohort_label, batch_id, batch_label, mode,
                 student_folders, comparisons, skipped_students=None, analyzed_students=None) -> dict:
        existing = self.get_batch(cohort_id, batch_id) or {
            "cohortId": cohort_id, "cohortLabel": cohort_label,
            "batchId": batch_id, "batchLabel": batch_label,
            "studentCount": 0, "pairs": [],
        }

        existing["studentCount"] = max(existing["studentCount"], len(student_folders))

        if skipped_students is not None:
            skipped_by_mode = existing.setdefault("skippedStudentsByMode", {})
            skipped_by_mode[mode] = skipped_students
            all_skipped = set()
            for s_list in skipped_by_mode.values():
                all_skipped.update(s_list)
            existing["skippedStudents"] = sorted(list(all_skipped))

        if analyzed_students is not None:
            analyzed_by_mode = existing.setdefault("analyzedStudentsByMode", {})
            analyzed_by_mode[mode] = analyzed_students

        # index existing pairs by (a,b,mode) so re-running one mode (code vs report)
        # doesn't wipe out results already stored for the other mode
        existing_by_key = {(p["a"], p["b"], p["type"]): p for p in existing["pairs"]}
        for c in comparisons:
            key = (c["a"], c["b"], mode)
            reverse_key = (c["b"], c["a"], mode)
            prior = existing_by_key.get(key) or existing_by_key.get(reverse_key)
            pair_id = prior["id"] if prior else f"{cohort_id}-{batch_id}-{mode}-{c['a']}-{c['b']}"
            status = prior["status"] if prior else "Pending"
            entry = {
                "id": pair_id,
                "a": c["a"], "b": c["b"],
                "type": "Code" if mode == "code" else "Report",
                "similarity": c["similarity"],
                "matches": c["matches"],
                "status": status,
                "crossBatch": False,
            }
            existing_by_key[key] = entry

        existing["pairs"] = list(existing_by_key.values())

        with open(self._batch_path(cohort_id, batch_id), "w") as f:
            json.dump(existing, f, indent=2)

        index = self._read_index()
        cohort = index["cohorts"].setdefault(cohort_id, {"label": cohort_label, "batches": {}})
        cohort["batches"][batch_id] = {
            "label": batch_label,
            "studentCount": existing["studentCount"],
            "flaggedCount": sum(1 for p in existing["pairs"] if p["similarity"] >= 80),
            "maxSimilarity": max([p["similarity"] for p in existing["pairs"]] + [0]),
        }
        self._write_index(index)

        return existing

    def delete_batch(self, cohort_id: str, batch_id: str) -> bool:
        path = self._batch_path(cohort_id, batch_id)
        if not path.exists():
            return False
        
        # Remove batch file
        path.unlink()
        
        # Update index
        index = self._read_index()
        if cohort_id in index["cohorts"]:
            cohort = index["cohorts"][cohort_id]
            if batch_id in cohort["batches"]:
                del cohort["batches"][batch_id]
                # Optional: if cohort is empty, you could delete it, but let's just leave it empty 
                # or remove it for cleanliness. Let's remove it if empty.
                if not cohort["batches"]:
                    del index["cohorts"][cohort_id]
                self._write_index(index)
        
        return True
