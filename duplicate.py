#!/usr/bin/env python3
import argparse
import os
import pickle
import re
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import requests
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity


CACHE_DIR = Path(".cache")
CACHE_FILE = CACHE_DIR / "issue_index.pkl"
DEFAULT_MODEL = "sentence-transformers/all-mpnet-base-v2"
DEFAULT_TOP_K = 5
DEFAULT_THRESHOLD = 0.55
DEFAULT_BATCH_SIZE = 100
DEFAULT_ISSUE_FILE = "issue.txt"


@dataclass
class Config:
	jira_url: str
	jira_token: str
	jira_project_key: str
	jira_user: str | None
	model_name: str
	top_k: int
	min_score: float
	refresh_cache: bool
	build_only: bool
	exclude_done: bool
	issue_file: str
	title: str
	description: str


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Check if a new ticket is likely a duplicate of existing Jira issues."
	)
	parser.add_argument("--jira-url", default=os.getenv("JIRA_URL"))
	parser.add_argument("--jira-token", default=os.getenv("JIRA_TOKEN"))
	parser.add_argument("--jira-project-key", default=os.getenv("JIRA_PROJECT_KEY"))
	parser.add_argument("--jira-user", default=os.getenv("JIRA_USER"))
	parser.add_argument("--model", default=DEFAULT_MODEL)
	parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
	parser.add_argument("--min-score", type=float, default=DEFAULT_THRESHOLD)
	parser.add_argument("--refresh-cache", action="store_true")
	parser.add_argument("--build-only", action="store_true")
	parser.add_argument("--exclude-done", action="store_true")
	parser.add_argument("--issue-file", default=DEFAULT_ISSUE_FILE)
	parser.add_argument("--title", default="")
	parser.add_argument("--description", default="")
	return parser.parse_args()


def normalize_text(value: str) -> str:
	return re.sub(r"\s+", " ", (value or "").strip())


def parse_issue_file(path: str) -> tuple[str, str]:
	content = Path(path).read_text(encoding="utf-8")
	upper_content = content.upper()
	title_marker = "TITLE:"
	description_marker = "DESCRIPTION:"
	title_idx = upper_content.find(title_marker)
	description_idx = upper_content.find(description_marker)

	if title_idx == -1 or description_idx == -1 or description_idx < title_idx:
		raise ValueError(
			f"Issue file '{path}' must contain both 'TITLE:' and 'DESCRIPTION:' sections."
		)

	title_start = title_idx + len(title_marker)
	description_start = description_idx + len(description_marker)
	title = normalize_text(content[title_start:description_idx])
	description = normalize_text(content[description_start:])
	if not title and not description:
		raise ValueError(f"Issue file '{path}' is empty after parsing.")
	return title, description


def adf_to_text(node: Any) -> str:
	if node is None:
		return ""

	if isinstance(node, str):
		return node

	if isinstance(node, list):
		return " ".join(adf_to_text(item) for item in node).strip()

	if isinstance(node, dict):
		text_parts: list[str] = []
		if "text" in node and isinstance(node["text"], str):
			text_parts.append(node["text"])
		if "content" in node:
			text_parts.append(adf_to_text(node["content"]))
		return " ".join(part for part in text_parts if part).strip()

	return ""


def build_jql(project_key: str, exclude_done: bool) -> str:
	base = (
		f'project = "{project_key}" '
		"AND issuetype in (Epic, Story, Bug, Task) "
		'AND issuetype not in ("Test Execution")'
	)
	if exclude_done:
		base += " AND statusCategory != Done"
	return base


def issue_url(jira_url: str, key: str) -> str:
	return f"{jira_url.rstrip('/')}/browse/{key}"


def get_auth_headers_and_params(token: str, user: str | None) -> tuple[dict[str, str], tuple[str, str] | None]:
	headers = {"Accept": "application/json", "Content-Type": "application/json"}
	auth = None
	if user:
		auth = (user, token)
	else:
		headers["Authorization"] = f"Bearer {token}"
	return headers, auth


def call_jira_search(
	jira_url: str,
	headers: dict[str, str],
	auth: tuple[str, str] | None,
	payload: dict[str, Any],
) -> dict[str, Any]:
	errors: list[str] = []
	for api_version in ("3", "2"):
		endpoint = f"{jira_url.rstrip('/')}/rest/api/{api_version}/search"
		try:
			resp = requests.post(endpoint, json=payload, headers=headers, auth=auth, timeout=30)
			if resp.status_code == 404:
				errors.append(f"{endpoint} -> 404")
				continue
			resp.raise_for_status()
			return resp.json()
		except requests.RequestException as exc:
			errors.append(f"{endpoint} -> {exc}")
	raise RuntimeError("Jira search failed:\n" + "\n".join(errors))


def fetch_issues(cfg: Config) -> list[dict[str, Any]]:
	headers, auth = get_auth_headers_and_params(cfg.jira_token, cfg.jira_user)
	jql = build_jql(cfg.jira_project_key, cfg.exclude_done)

	fields = ["key", "summary", "description", "issuetype", "status", "updated"]
	start_at = 0
	total = None
	issues: list[dict[str, Any]] = []

	while total is None or start_at < total:
		payload = {
			"jql": jql,
			"startAt": start_at,
			"maxResults": DEFAULT_BATCH_SIZE,
			"fields": fields,
		}
		data = call_jira_search(cfg.jira_url, headers, auth, payload)
		total = data.get("total", 0)
		batch = data.get("issues", [])

		for issue in batch:
			fields_data = issue.get("fields", {})
			key = issue.get("key", "")
			summary = normalize_text(fields_data.get("summary", ""))
			description_text = normalize_text(adf_to_text(fields_data.get("description")))
			text_for_embedding = normalize_text(f"{summary}. {description_text}")
			issue_type = (fields_data.get("issuetype") or {}).get("name", "")
			status = (fields_data.get("status") or {}).get("name", "")
			updated = fields_data.get("updated", "")

			if not summary:
				continue

			issues.append(
				{
					"key": key,
					"summary": summary,
					"description": description_text,
					"text": text_for_embedding,
					"issue_type": issue_type,
					"status": status,
					"updated": updated,
					"url": issue_url(cfg.jira_url, key),
				}
			)

		start_at += len(batch)
		if not batch:
			break

	return issues


def embed_texts(model: SentenceTransformer, texts: list[str]) -> np.ndarray:
	vectors = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
	return vectors.astype(np.float32)


def load_cache() -> dict[str, Any] | None:
	if not CACHE_FILE.exists():
		return None
	with CACHE_FILE.open("rb") as f:
		return pickle.load(f)


def save_cache(payload: dict[str, Any]) -> None:
	CACHE_DIR.mkdir(parents=True, exist_ok=True)
	with CACHE_FILE.open("wb") as f:
		pickle.dump(payload, f)


def build_or_load_index(cfg: Config, model: SentenceTransformer) -> tuple[np.ndarray, list[dict[str, Any]]]:
	cache = load_cache()
	if cache and not cfg.refresh_cache:
		if cache.get("model_name") == cfg.model_name and cache.get("project_key") == cfg.jira_project_key:
			vectors = cache.get("vectors")
			metadata = cache.get("metadata")
			if isinstance(vectors, np.ndarray) and isinstance(metadata, list) and len(metadata) == len(vectors):
				return vectors, metadata

	issues = fetch_issues(cfg)
	if not issues:
		raise RuntimeError("No Jira issues fetched for indexing. Check your JQL/project settings.")

	texts = [item["text"] for item in issues]
	vectors = embed_texts(model, texts)
	fetched_at = datetime.now(timezone.utc).isoformat()

	save_cache(
		{
			"model_name": cfg.model_name,
			"project_key": cfg.jira_project_key,
			"fetched_at": fetched_at,
			"vectors": vectors,
			"metadata": issues,
		}
	)

	return vectors, issues


def get_cache_last_fetch() -> str | None:
	cache = load_cache()
	if cache:
		fetched_at = cache.get("fetched_at")
		if isinstance(fetched_at, str) and fetched_at.strip():
			return fetched_at

	if CACHE_FILE.exists():
		# Backward compatibility for old cache files without fetched_at.
		return datetime.fromtimestamp(CACHE_FILE.stat().st_mtime, tz=timezone.utc).isoformat()

	return None


def to_confidence(score: float) -> str:
	if score >= 0.8:
		return "high"
	if score >= 0.65:
		return "medium"
	return "low"


def rank_similar(
	model: SentenceTransformer,
	vectors: np.ndarray,
	metadata: list[dict[str, Any]],
	title: str,
	description: str,
	top_k: int,
	min_score: float,
) -> list[dict[str, Any]]:
	candidate_text = normalize_text(f"{title}. {title}. {description}")
	if not candidate_text.strip(". "):
		raise ValueError("Provide at least a non-empty title or description.")

	candidate_vec = embed_texts(model, [candidate_text])
	scores = cosine_similarity(candidate_vec, vectors)[0]
	ranked_indices = np.argsort(scores)[::-1]

	results: list[dict[str, Any]] = []
	for idx in ranked_indices:
		score = float(scores[idx])
		if score < min_score:
			continue
		issue = metadata[idx]
		results.append(
			{
				"key": issue["key"],
				"summary": issue["summary"],
				"issue_type": issue["issue_type"],
				"status": issue["status"],
				"updated": issue["updated"],
				"url": issue["url"],
				"score": score,
				"confidence": to_confidence(score),
			}
		)
		if len(results) >= top_k:
			break

	return results


def print_results(results: list[dict[str, Any]]) -> None:
	if not results:
		print("No likely duplicates found above the configured threshold.")
		return

	print("Likely duplicate tickets:")
	print("=" * 80)
	for rank, item in enumerate(results, start=1):
		pct = item["score"] * 100
		print(f"{rank}. {item['key']} | {item['summary']}")
		print(
			f"   score={pct:.1f}% ({item['confidence']}) | "
			f"type={item['issue_type']} | status={item['status']}"
		)
		print(f"   updated={item['updated']}")
		print(f"   {item['url']}")


def config_from_args(args: argparse.Namespace) -> Config:
	missing = []
	if not args.jira_url:
		missing.append("JIRA_URL / --jira-url")
	if not args.jira_token:
		missing.append("JIRA_TOKEN / --jira-token")
	if not args.jira_project_key:
		missing.append("JIRA_PROJECT_KEY / --jira-project-key")

	if missing:
		raise ValueError("Missing required config: " + ", ".join(missing))

	title = normalize_text(args.title)
	description = normalize_text(args.description)

	if not args.build_only and (not title or not description):
		file_title = ""
		file_description = ""
		try:
			file_title, file_description = parse_issue_file(args.issue_file)
		except FileNotFoundError:
			if not title and not description:
				raise ValueError(
					f"No --title/--description provided and issue file '{args.issue_file}' was not found."
				)
		except ValueError as exc:
			if not title and not description:
				raise ValueError(str(exc))

		if not title:
			title = file_title
		if not description:
			description = file_description

	if not args.build_only and not title and not description:
		raise ValueError(
			"Provide --title/--description or create a valid issue file with TITLE and DESCRIPTION sections."
		)

	return Config(
		jira_url=args.jira_url,
		jira_token=args.jira_token,
		jira_project_key=args.jira_project_key,
		jira_user=args.jira_user,
		model_name=args.model,
		top_k=max(1, args.top_k),
		min_score=max(0.0, min(1.0, args.min_score)),
		refresh_cache=args.refresh_cache,
		build_only=args.build_only,
		exclude_done=args.exclude_done,
		issue_file=args.issue_file,
		title=title,
		description=description,
	)


def main() -> int:
	load_dotenv()
	args = parse_args()

	try:
		cfg = config_from_args(args)
	except ValueError as exc:
		print(str(exc), file=sys.stderr)
		return 2

	try:
		model = SentenceTransformer(cfg.model_name)
		vectors, metadata = build_or_load_index(cfg, model)

		if cfg.build_only:
			print(f"Index ready. Stored {len(metadata)} issues in {CACHE_FILE}.")
			return 0

		results = rank_similar(
			model=model,
			vectors=vectors,
			metadata=metadata,
			title=cfg.title,
			description=cfg.description,
			top_k=cfg.top_k,
			min_score=cfg.min_score,
		)
		print_results(results)
		return 0
	except Exception as exc:
		print(f"Error: {exc}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	raise SystemExit(main())
