import datetime
import math
import os

import isodate
from googleapiclient.discovery import build
from prefect import flow, task
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from collector.database import SessionLocal
from collector.models import (
    SearchDiscovery,
    TrendingDiscovery,
    Video,
    VideoStat,
    init_db,
)

# --- CONFIGURATION ---
TOPIC_POOL = [
    "Gaming|Minecraft|Roblox|Esports",
    "Tech Review|Smartphone|Laptop|Coding",
    "AI News|Machine Learning|ChatGPT|LLM",
    "Cooking|Recipe|Street Food|Baking",
    "Vlog|Travel|Daily Life|Lifestyle",
    "Sports|Highlights|NBA|Football|Soccer",
    "Music|Pop|Hip Hop|Cover|Live Performance",
    "Finance|Crypto|Stock Market|Investing",
    "Science|Space|Physics|Biology",
    "Education|Tutorial|How To|DIY",
]

# ML Label Collection Schedule (in Hours)
# T=0 (Discovery) is automatic. We also fetch at:
FOLLOW_UP_SCHEDULE = [24, 48, 168]

# Budget Management
DAILY_QUOTA = 10000
QUOTA_BUFFER = 0.20
TARGET_QUOTA_PER_RUN = int(DAILY_QUOTA * (1 - QUOTA_BUFFER))
COST_SEARCH = 100
COST_DETAILS = 1


def get_current_api_key():
    keys_str = os.environ.get("YOUTUBE_API_KEYS", "")
    if not keys_str:
        raise ValueError("YOUTUBE_API_KEYS not found.")
    keys = [k.strip() for k in keys_str.split(",") if k.strip()]
    current_hour = datetime.datetime.now(datetime.timezone.utc).hour
    return keys[(current_hour // 2) % len(keys)]


@task(name="Discover Deep Search", retries=2)
def discover_deep_search(api_key):
    youtube = build("youtube", "v3", developerKey=api_key)
    max_batches = TARGET_QUOTA_PER_RUN // (COST_SEARCH + COST_DETAILS)
    batches_per_topic = max(1, math.floor(max_batches / len(TOPIC_POOL)))
    discovery_intents = []

    # Look back exactly 2 hours to avoid overlap (2h job schedule)
    published_after = (
        datetime.datetime.now() - datetime.timedelta(hours=2)
    ).isoformat() + "Z"

    for topic in TOPIC_POOL:
        next_page_token = None
        for _ in range(batches_per_topic):
            try:
                request = youtube.search().list(
                    part="id",
                    q=topic,
                    type="video",
                    order="date",
                    publishedAfter=published_after,
                    maxResults=50,
                    pageToken=next_page_token,
                )
                response = request.execute()

                for item in response.get("items", []):
                    vid = item["id"]["videoId"]
                    discovery_intents.append(
                        {"video_id": vid, "query": topic, "type": "search"}
                    )

                next_page_token = response.get("nextPageToken")
                if not next_page_token:
                    break
            except Exception as e:
                print(f"Error searching topic {topic}: {e}")
                break

    return discovery_intents


@task(name="Discover Trending")
def discover_trending(api_key):
    youtube = build("youtube", "v3", developerKey=api_key)
    discovery_intents = []

    try:
        request = youtube.videos().list(
            part="id", chart="mostPopular", regionCode="US", maxResults=50
        )
        response = request.execute()

        for rank, item in enumerate(response.get("items", []), 1):
            vid = item["id"]
            discovery_intents.append(
                {"video_id": vid, "rank": rank, "type": "trending"}
            )

        return discovery_intents
    except Exception as e:
        print(f"Error fetching trending: {e}")
        return []


@task(name="Identify Prediction Targets")
def get_historical_targets(lag_hours):
    """
    Finds videos discovered exactly `lag_hours` ago to fetch their 'Label' stats.
    """
    session = SessionLocal()
    target_ids = []

    # Window: Target +/- 1.5 hours
    time_threshold = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        hours=lag_hours
    )
    window_start = time_threshold - datetime.timedelta(minutes=90)
    window_end = time_threshold + datetime.timedelta(minutes=90)

    try:
        query = text(
            """
            SELECT video_id FROM videos 
            WHERE first_seen_at BETWEEN :start AND :end
        """
        )
        result = session.execute(
            query, {"start": window_start, "end": window_end}
        ).fetchall()
        target_ids = [row[0] for row in result]
    except Exception as e:
        print(f"Error querying historical targets: {e}")
    finally:
        session.close()

    unique_ids = list(set(target_ids))
    print(f"Found {len(unique_ids)} videos from {lag_hours}h ago (Prediction Labels).")
    return unique_ids


@task(name="Fetch & Split Data")
def fetch_and_process_data(api_key, target_video_ids):
    target_video_ids = list(set(target_video_ids))
    if not target_video_ids:
        return [], []

    youtube = build("youtube", "v3", developerKey=api_key)
    video_objects = []
    stat_objects = []
    current_time = datetime.datetime.now(datetime.timezone.utc)

    for i in range(0, len(target_video_ids), 50):
        batch_ids = target_video_ids[i : i + 50]
        try:
            request = youtube.videos().list(
                part="statistics,snippet,contentDetails,status", id=",".join(batch_ids)
            )
            response = request.execute()

            for item in response.get("items", []):
                stats = item.get("statistics", {})
                snippet = item.get("snippet", {})
                content = item.get("contentDetails", {})
                status = item.get("status", {})

                vid = item["id"]

                duration_sec = 0
                try:
                    duration_obj = isodate.parse_duration(
                        content.get("duration", "PT0S")
                    )

                    duration_sec = int(duration_obj.total_seconds())
                except Exception as e:
                    print(f"Error parsing duration for {vid}: {e}")

                v_obj = {
                    "video_id": vid,
                    "title": snippet.get("title", ""),
                    "description": snippet.get("description", "")[:2000],
                    "tags": ",".join(snippet.get("tags", [])),
                    "published_at": snippet.get("publishedAt"),
                    "channel_id": snippet.get("channelId"),
                    "category_id": snippet.get("categoryId"),
                    "duration_seconds": duration_sec,
                    "definition": content.get("definition"),
                    "made_for_kids": status.get("madeForKids"),
                    "audio_language": snippet.get("defaultAudioLanguage"),
                    "thumbnail_url": (
                        snippet.get("thumbnails", {}).get("high", {}).get("url")
                    ),
                }
                video_objects.append(v_obj)
                s_obj = {
                    "time": current_time,
                    "video_id": vid,
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),
                    "comments": int(stats.get("commentCount", 0)),
                }
                stat_objects.append(s_obj)

        except Exception as e:
            print(f"Error fetching batch {i}: {e}")

    return video_objects, stat_objects


@task(name="Save Normalized Data")
def save_data(video_dicts, stat_dicts, discovery_intents=None):
    session = SessionLocal()
    try:
        # 1. Upsert Videos (Metadata)
        # We use PostgreSQL specific "ON CONFLICT DO NOTHING" (or Update)
        if video_dicts:
            stmt = pg_insert(Video).values(video_dicts)
            do_nothing_stmt = stmt.on_conflict_do_nothing(index_elements=["video_id"])
            session.execute(do_nothing_stmt)

        # 2. Insert Stats (Always insert new time-series row)
        if stat_dicts:
            session.bulk_insert_mappings(VideoStat, stat_dicts)

        # 3. Insert Discovery Logs (Only if provided)
        if discovery_intents:
            search_logs = []
            trending_logs = []

            for d in discovery_intents:
                if d["type"] == "search":
                    search_logs.append(
                        SearchDiscovery(video_id=d["video_id"], query=d["query"])
                    )
                elif d["type"] == "trending":
                    trending_logs.append(
                        TrendingDiscovery(video_id=d["video_id"], rank=d["rank"])
                    )

            if search_logs:
                session.bulk_save_objects(search_logs)
            if trending_logs:
                session.bulk_save_objects(trending_logs)

        session.commit()
        print(
            f"Saved: {len(video_dicts)} Videos (Upsert), "
            f"{len(stat_dicts)} Stats, "
            f"{len(discovery_intents or [])} Logs."
        )

    except Exception as e:
        session.rollback()
        print(f"Error saving to DB: {e}")
    finally:
        session.close()


@flow(name="ML Data Pipeline")
def run_scraper_flow():
    init_db()

    try:
        api_key = get_current_api_key()
    except Exception as e:
        print(f"Critical: {e}")
        return

    # --- PHASE 1: DISCOVERY (Input Features T=0) ---
    print("--- Phase 1: Discovery (T=0) ---")
    search_intents = discover_deep_search(api_key)
    trending_intents = discover_trending(api_key)

    all_intents = search_intents + trending_intents
    new_ids = list(set([d["video_id"] for d in all_intents]))

    print(f"Discovered {len(new_ids)} videos.")

    # Fetch Data
    v_objs, s_objs = fetch_and_process_data(api_key, new_ids)

    # Save (Metadata + Initial Stats + Discovery Logs)
    save_data(v_objs, s_objs, all_intents)

    # --- PHASE 2: FOLLOW-UP (Labels T=N) ---
    print("--- Phase 2: Follow-up (Labels T=N) ---")
    historical_ids = []

    for hours_ago in FOLLOW_UP_SCHEDULE:
        targets = get_historical_targets(hours_ago)
        historical_ids.extend(targets)

    # Filter out IDs
    ids_to_fetch = list(set(historical_ids) - set(new_ids))

    if ids_to_fetch:
        print(f"Fetching labels for {len(ids_to_fetch)} historical videos...")
        v_objs_hist, s_objs_hist = fetch_and_process_data(api_key, ids_to_fetch)
        save_data(v_objs_hist, s_objs_hist, discovery_intents=None)
    else:
        print("No historical videos needed updates.")


if __name__ == "__main__":
    run_scraper_flow()
