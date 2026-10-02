import os
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ANILIST_USERNAME = os.environ.get("ANILIST_USERNAME", "itsmechinmoy")
ANILIST_TOKEN = os.environ.get("ANILIST_TOKEN", "").strip()
MANGABAKA_KEY = os.environ.get("MANGABAKA_API_KEY", "").strip()
FORCE_FULL_SYNC = os.environ.get("FORCE_FULL_SYNC", "false").lower() in ("true", "1", "yes")

CACHE_FILE = "mapping_cache.json"
STATE_FILE = "sync_state.json"

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

def load_json(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load {filepath}: {e}")
    return {}

def save_json(filepath, data):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Warning: Failed to save {filepath}: {e}")

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
            updatedAt
            startedAt { year month day }
            completedAt { year month day }
            media {
              title {
                userPreferred
                romaji
                english
              }
              synonyms
            }
          }
        }
      }
    }
    """
    headers = {"Content-Type": "application/json"}
    if ANILIST_TOKEN:
        headers["Authorization"] = f"Bearer {ANILIST_TOKEN}"
        print("Using AniList token for authenticated access (private lists included).")

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
        r = session.get(url, params={"with_series": 1}, timeout=10)
        if r.status_code == 200:
            series_list = r.json().get("data", {}).get("series", [])
            if series_list:
                s = series_list[0]
                mb_id = s.get("merged_with") if s.get("state") == "merged" else s.get("id")
                cache[key] = mb_id
                return mb_id
    except Exception:
        pass

    # 2. Try titles: english, userPreferred, romaji + all synonyms
    media_info = entry.get("media", {})
    title_obj = media_info.get("title", {})
    synonyms = media_info.get("synonyms") or []

    candidate_titles = []
    for t_key in ["english", "userPreferred", "romaji"]:
        val = title_obj.get(t_key)
        if val and val.strip() and val.strip() not in candidate_titles:
            candidate_titles.append(val.strip())
    for s_val in synonyms:
        if s_val and s_val.strip() and s_val.strip() not in candidate_titles:
            candidate_titles.append(s_val.strip())

    for title in candidate_titles:
        # Exact/compact match
        try:
            r = session.get(f"{MB_BASE}/v1/series/match", params={"q": title}, timeout=10)
            if r.status_code == 200:
                results = r.json().get("data", [])
                if isinstance(results, list) and len(results) > 0:
                    s = results[0]
                    mb_id = s.get("merged_with") if s.get("state") == "merged" else s.get("id")
                    cache[key] = mb_id
                    return mb_id
        except Exception:
            pass

        # Search fallback
        try:
            r = session.get(f"{MB_BASE}/v1/series/search", params={"q": title}, timeout=10)
            if r.status_code == 200:
                results = r.json().get("data", [])
                if isinstance(results, list) and len(results) > 0:
                    for s in results:
                        if s.get("title", "").strip().lower() == title.lower():
                            mb_id = s.get("merged_with") if s.get("state") == "merged" else s.get("id")
                            cache[key] = mb_id
                            return mb_id
        except Exception:
            pass

    cache[key] = None
    return None

def resolve_all_ids(entries, cache, force_full=False):
    if force_full:
        unmapped_keys = [k for k, v in cache.items() if v is None]
        for k in unmapped_keys:
            del cache[k]
        print(f"Force full sync: wiped {len(unmapped_keys)} unmapped entries from cache to re-evaluate.")

    to_resolve = [e for e in entries if str(e["mediaId"]) not in cache or cache[str(e["mediaId"])] is None]
    cached_count = len(entries) - len(to_resolve)
    print(f"{cached_count} entries already mapped; {len(to_resolve)} to resolve with MangaBaka...")

    if to_resolve:
        with requests.Session() as s:
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {executor.submit(lookup_mangabaka, s, e, cache): e for e in to_resolve}
                count = 0
                for f in as_completed(futures):
                    count += 1
                    if count % 50 == 0 or count == len(to_resolve):
                        print(f"Resolved {count}/{len(to_resolve)} lookups...")
        save_json(CACHE_FILE, cache)

def push_batches(mb_entries):
    if not MANGABAKA_KEY:
        print("MANGABAKA_API_KEY not set. Skipping push to MangaBaka (dry run).")
        return True

    headers = {
        "x-api-key": MANGABAKA_KEY,
        "Content-Type": "application/json"
    }

    total = len(mb_entries)
    print(f"Pushing {total} updated entries to MangaBaka in batches of up to 100...")
    all_success = True

    for i in range(0, total, 100):
        chunk = mb_entries[i:i + 100]
        url = f"{MB_BASE}/v1/my/library/batch"
        res = requests.post(url, headers=headers, json=chunk, timeout=30)
        
        if res.status_code == 200:
            print(f"Batch {i // 100 + 1}/{(total - 1) // 100 + 1}: OK (HTTP 200)")
        else:
            print(f"Batch {i // 100 + 1} rejected (HTTP {res.status_code}): {res.text}")
            print("Falling back to single updates for this batch...")
            for item in chunk:
                sid = item["series_id"]
                single_url = f"{MB_BASE}/v1/my/library/{sid}"
                # series_id is in URL path; remove from JSON body to satisfy schema
                body = {k: v for k, v in item.items() if k != "series_id"}
                sr = requests.post(single_url, headers=headers, json=body, timeout=15)
                if sr.status_code not in (200, 201):
                    sr = requests.put(single_url, headers=headers, json=body, timeout=15)
                if sr.status_code not in (200, 201):
                    print(f"  Series {sid} failed: {sr.status_code} - {sr.text}")
                    all_success = False

    return all_success

def main():
    cache = load_json(CACHE_FILE)
    state = load_json(STATE_FILE)
    synced_entries = state.get("entries", {})

    entries = fetch_anilist_entries()

    # Automatically purge cached 'None' for any entry that had activity on AniList
    for entry in entries:
        mid = str(entry["mediaId"])
        al_updated_at = entry.get("updatedAt", 0)
        cached_info = synced_entries.get(mid)
        if cache.get(mid) is None and (cached_info is None or al_updated_at > cached_info.get("updatedAt", 0)):
            if mid in cache:
                del cache[mid]

    resolve_all_ids(entries, cache, force_full=FORCE_FULL_SYNC)

    mb_payload_raw = []
    unmapped_updated = []
    updated_state = dict(synced_entries)
    mapped_count = 0

    for entry in entries:
        mid = str(entry["mediaId"])
        mb_id = cache.get(mid)
        al_updated_at = entry.get("updatedAt", 0)
        cached_info = synced_entries.get(mid)
        title = entry.get("media", {}).get("title", {}).get("userPreferred") or f"ID {mid}"

        if not mb_id:
            if cached_info and al_updated_at > cached_info.get("updatedAt", 0):
                unmapped_updated.append(f"{title} (AL ID: {mid})")
            continue

        mapped_count += 1

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

        # Change detection
        has_changed = False
        if FORCE_FULL_SYNC:
            has_changed = True
        elif cached_info is None:
            has_changed = True
        elif cached_info.get("updatedAt") != al_updated_at:
            has_changed = True
        elif cached_info.get("payload") != clean_item:
            has_changed = True

        if has_changed:
            mb_payload_raw.append(clean_item)
            updated_state[mid] = {
                "updatedAt": al_updated_at,
                "payload": clean_item
            }

    # Deduplicate mb_payload by series_id so MangaBaka batch API never sees duplicate series_id
    deduped_payload = {}
    for item in mb_payload_raw:
        sid = item["series_id"]
        if sid not in deduped_payload:
            deduped_payload[sid] = item
        else:
            # Keep the one with greater chapter progress
            existing = deduped_payload[sid]
            if (item.get("progress_chapter") or 0) >= (existing.get("progress_chapter") or 0):
                deduped_payload[sid] = item
    
    mb_payload = list(deduped_payload.values())

    print(f"\nLibrary Status: {mapped_count} mapped titles out of {len(entries)} AniList entries.")
    if unmapped_updated:
        print(f"Note: {len(unmapped_updated)} updated AniList titles could not be synced because they are not yet indexed on MangaBaka:")
        for t in unmapped_updated[:5]:
            print(f"  - {t}")

    print(f"Change detection: {len(mb_payload)} entries changed/new (after deduplicating series IDs).")

    if not mb_payload:
        print("Everything is already up-to-date with MangaBaka. No API requests needed!")
        return

    success = push_batches(mb_payload)
    if success:
        state["entries"] = updated_state
        state["last_sync_timestamp"] = int(time.time())
        save_json(STATE_FILE, state)
        print("Updated sync_state.json successfully.")

    print("Sync process completed!")

if __name__ == "__main__":
    main()
