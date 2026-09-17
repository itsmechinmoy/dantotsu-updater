import os
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ANILIST_USERNAME = os.environ.get("ANILIST_USERNAME", "itsmechinmoy")
ANILIST_TOKEN = os.environ.get("ANILIST_TOKEN", "").strip()
MANGABAKA_KEY = os.environ.get("MANGABAKA_API_KEY", "").strip()
CACHE_FILE = "mapping_cache.json"

MB_BASE = "https://api.mangabaka.org"
AL_BASE = "https://graphql.anilist.co"

STATUS_MAP = {
    "CURRENT": "reading",
    "PLANNING": "plan_to_read",
    "COMPLETED": "completed",
    "DROPPED": "dropped",
    "PAUSED": "paused",
    "REPEATING": "rereading"
}

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load cache: {e}")
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"Warning: Failed to save cache: {e}")

def format_date(d):
    if not d or not d.get("year"):
        return None
    year = d["year"]
    month = d.get("month") or 1
    day = d.get("day") or 1
    return f"{year:04d}-{month:02d}-{day:02d}"

def fetch_anilist_entries():
    print(f"Fetching AniList manga collection for '{ANILIST_USERNAME}'...")
    query = """
    query ($userName: String) {
      MediaListCollection(userName: $userName, type: MANGA) {
        lists {
          name
          entries {
            mediaId
            status
            progress
            progressVolumes
            score(format: POINT_100)
            private
            notes
            startedAt { year month day }
            completedAt { year month day }
            media {
              title {
                userPreferred
                romaji
                english
              }
            }
          }
        }
      }
    }
    """
    headers = {"Content-Type": "application/json"}
    if ANILIST_TOKEN:
        headers["Authorization"] = f"Bearer {ANILIST_TOKEN}"
        print("Using AniList Bearer token for authenticated access (private lists included).")

    res = requests.post(AL_BASE, headers=headers, json={
        "query": query,
        "variables": {"userName": ANILIST_USERNAME}
    }, timeout=30)
    res.raise_for_status()
    data = res.json()

    if "errors" in data:
        raise RuntimeError(f"AniList GraphQL error: {data['errors']}")

    lists = data.get("data", {}).get("MediaListCollection", {}).get("lists", [])
    all_entries = {}
    for l in lists:
        for entry in l.get("entries", []):
            mid = entry["mediaId"]
            all_entries[mid] = entry

    print(f"Retrieved {len(all_entries)} unique manga entries from AniList.")
    return list(all_entries.values())

def lookup_mangabaka(session, entry, cache):
    anilist_id = entry["mediaId"]
    key = str(anilist_id)
    if key in cache and cache[key] is not None:
        return cache[key]

    # 1. Try direct AniList ID source lookup
    try:
        url = f"{MB_BASE}/v1/source/anilist/{anilist_id}"
        r = session.get(url, params={"with_series": 1}, timeout=15)
        if r.status_code == 200:
            series_list = r.json().get("data", {}).get("series", [])
            if series_list:
                s = series_list[0]
                mb_id = s.get("merged_with") if s.get("state") == "merged" else s.get("id")
                cache[key] = mb_id
                return mb_id
    except Exception:
        pass

    # 2. Fallback: Search by title (English, Romaji, userPreferred)
    media_info = entry.get("media", {})
    titles_to_try = []
    title_obj = media_info.get("title", {})
    for t_key in ["english", "userPreferred", "romaji"]:
        val = title_obj.get(t_key)
        if val and val not in titles_to_try:
            titles_to_try.append(val)

    for title in titles_to_try:
        try:
            r = session.get(f"{MB_BASE}/v1/series/match", params={"q": title}, timeout=15)
            if r.status_code == 200:
                results = r.json().get("data", [])
                if results and isinstance(results, list):
                    s = results[0]
                    mb_id = s.get("merged_with") if s.get("state") == "merged" else s.get("id")
                    cache[key] = mb_id
                    return mb_id
        except Exception:
            pass

    cache[key] = None
    return None

def resolve_all_ids(entries, cache):
    to_resolve = [e for e in entries if str(e["mediaId"]) not in cache or cache[str(e["mediaId"])] is None]
    cached_count = len(entries) - len(to_resolve)
    print(f"{cached_count} entries already in cache; {len(to_resolve)} to resolve...")

    if to_resolve:
        with requests.Session() as s:
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {executor.submit(lookup_mangabaka, s, e, cache): e for e in to_resolve}
                count = 0
                for f in as_completed(futures):
                    count += 1
                    if count % 50 == 0 or count == len(to_resolve):
                        print(f"Resolved {count}/{len(to_resolve)} lookups...")
        save_cache(cache)

def push_batches(mb_entries):
    if not MANGABAKA_KEY:
        print("MANGABAKA_API_KEY not set. Skipping push to MangaBaka (dry run).")
        return

    headers = {
        "x-api-key": MANGABAKA_KEY,
        "Content-Type": "application/json"
    }

    total = len(mb_entries)
    print(f"Pushing {total} entries to MangaBaka in batches of up to 100...")

    for i in range(0, total, 100):
        chunk = mb_entries[i:i + 100]
        url = f"{MB_BASE}/v1/my/library/batch"
        res = requests.post(url, headers=headers, json=chunk, timeout=30)
        
        if res.status_code == 200:
            print(f"Batch {i // 100 + 1}/{(total - 1) // 100 + 1}: OK (HTTP 200)")
        else:
            print(f"Batch {i // 100 + 1} rejected (HTTP {res.status_code}): {res.text}")
            print("Falling back to single updates for this batch to isolate failures...")
            for item in chunk:
                single_url = f"{MB_BASE}/v1/my/library/{item['series_id']}"
                sr = requests.post(single_url, headers=headers, json=item, timeout=15)
                if sr.status_code not in (200, 201):
                    sr = requests.put(single_url, headers=headers, json=item, timeout=15)
                if sr.status_code not in (200, 201):
                    print(f"  Series {item['series_id']} failed: {sr.status_code} - {sr.text}")

def main():
    cache = load_cache()
    entries = fetch_anilist_entries()
    resolve_all_ids(entries, cache)

    mb_payload = []
    unmapped = 0

    for entry in entries:
        mid = entry["mediaId"]
        mb_id = cache.get(str(mid))
        if not mb_id:
            unmapped += 1
            continue

        item = {
            "series_id": mb_id,
            "state": STATUS_MAP.get(entry["status"], "reading"),
            "progress_chapter": min(entry.get("progress") or 0, 10000),
            "progress_volume": min(entry.get("progressVolumes") or 0, 10000),
            "rating": int(round(entry["score"])) if entry.get("score") and entry["score"] > 0 else None,
            "is_private": bool(entry.get("private")),
            "note": entry.get("notes") or None,
            "start_date": format_date(entry.get("startedAt")),
            "finish_date": format_date(entry.get("completedAt")),
        }
        clean_item = {k: v for k, v in item.items() if v is not None}
        mb_payload.append(clean_item)

    print(f"Ready to sync: {len(mb_payload)} entries ({unmapped} unmapped on MangaBaka).")
    push_batches(mb_payload)
    print("Sync process completed!")

if __name__ == "__main__":
    main()
