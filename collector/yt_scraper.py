import datetime
import os
import random

import isodate
from googleapiclient.discovery import build
from prefect import flow, task

from collector.database import SessionLocal
from collector.models import VideoStat, init_db

API_KEY = os.environ.get("YOUTUBE_API_KEY")

# --- CONFIGURATION ---
# We rotate through these topics to build a diverse dataset for Clustering/Association
TOPIC_POOL = [
    "Gaming|Minecraft|Roblox", 
    "Tech Review|Smartphone|Laptop", 
    "AI News|Machine Learning|ChatGPT",
    "Cooking|Recipe|Street Food",
    "Vlog|Travel|Daily Life",
    "Sports|Highlights|NBA|Football",
    "Music|Pop|Hip Hop|Cover"
]

# We want roughly 200 videos per run
VIDEOS_PER_SEARCH = 150 
VIDEOS_FROM_TRENDING = 50 

@task(name="Discover Search", retries=3)
def discover_from_search():
    """
    Picks a random topic and finds recent uploads (0-24h old).
    Good for: Finding 'Rising Stars' for Viral Prediction.
    """
    # Pick one random topic group to focus on this run
    current_query = random.choice(TOPIC_POOL)
    print(f"Discovering videos for topic: '{current_query}'...")
    
    youtube = build("youtube", "v3", developerKey=API_KEY)
    published_after = ((datetime.datetime.now() 
                        - datetime.timedelta(days=1)).isoformat() + "Z")
    
    video_ids = []
    next_page_token = None
    
    # Pagination Loop
    while len(video_ids) < VIDEOS_PER_SEARCH:
        try:
            request = youtube.search().list(
                part="id",
                q=current_query,
                type="video",
                order="date", # Get newest first
                publishedAfter=published_after,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()
            
            new_ids = [item['id']['videoId'] for item in response.get('items', [])]
            video_ids.extend(new_ids)
            
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
        except Exception as e:
            print(f"Search failed: {e}")
            break
            
    return video_ids[:VIDEOS_PER_SEARCH]

@task(name="Discover Trending", retries=3)
def discover_trending():
    """
    Fetches the current top trending videos globally.
    Used for establishing 'Success' baselines for Regression/Classification.
    """
    print("Fetching Global Trending videos...")
    youtube = build("youtube", "v3", developerKey=API_KEY)
    
    # The 'mostPopular' chart is extremely cheap (1 unit per page)
    request = youtube.videos().list(
        part="id",
        chart="mostPopular",
        regionCode="US",
        maxResults=VIDEOS_FROM_TRENDING
    )
    response = request.execute()
    
    video_ids = [item['id'] for item in response.get('items', [])]
    return video_ids

@task(name="Fetch Rich Stats")
def get_youtube_data(target_video_ids):
    # Dedup IDs
    target_video_ids = list(set(target_video_ids))
    if not target_video_ids:
        return []

    print(f"Fetching rich stats for {len(target_video_ids)} videos...")
    youtube = build("youtube", "v3", developerKey=API_KEY)
    data_objects = []
    current_time = datetime.datetime.now(datetime.timezone.utc)
    
    # Process in batches of 50
    for i in range(0, len(target_video_ids), 50):
        batch_ids = target_video_ids[i:i+50]
        
        request = youtube.videos().list(
            part="statistics,snippet,contentDetails",
            id=",".join(batch_ids)
        )
        response = request.execute()
        
        for item in response.get("items", []):
            stats = item["statistics"]
            snippet = item["snippet"]
            content = item["contentDetails"]
            
            # Feature Extraction
            duration_str = content.get("duration", "PT0S")
            try:
                duration_obj = isodate.parse_duration(duration_str)
                duration_sec = int(duration_obj.total_seconds())
            except Exception:
                duration_sec = 0
                
            definition = content.get("definition", "sd")
            category_id = snippet.get("categoryId", "0")
            pub_str = snippet.get("publishedAt")
            
            tags_list = snippet.get("tags", [])
            tags_str = ",".join(tags_list) if tags_list else ""
            thumb_url = snippet.get("thumbnails", {}).get("high", {}).get("url")

            video_snapshot = VideoStat(
                time=current_time,
                video_id=item["id"],
                views=int(stats.get("viewCount", 0)),
                likes=int(stats.get("likeCount", 0)),
                comments=int(stats.get("commentCount", 0)),
                title=snippet.get("title", ""),
                description=snippet.get("description", "")[:1500],
                tags=tags_str,
                thumbnail_url=thumb_url,
                duration_seconds=duration_sec,
                definition=definition,
                category_id=category_id,
                published_at=pub_str
            )
            data_objects.append(video_snapshot)
            
    return data_objects

@task(name="Save to DB")
def save_to_db(data):
    session = SessionLocal()
    try:
        session.bulk_save_objects(data)
        session.commit()
        print(f"Successfully saved {len(data)} snapshots.")
    except Exception as e:
        session.rollback()
        print(f"Error saving to database: {e}")
    finally:
        session.close()

@flow(name="Diverse Collection Pipeline")
def run_scraper_flow():
    init_db()
    if not API_KEY: 
        return
    
    # 1. Hybrid Discovery
    search_ids = discover_from_search()
    trending_ids = discover_trending()
    
    # Combine lists
    all_ids = search_ids + trending_ids
    print(f"Total IDs to process: {len(all_ids)}")
    
    # 2. Fetch Data
    data = get_youtube_data(all_ids)
    
    # 3. Save
    if data: 
        save_to_db(data)

if __name__ == "__main__":
    run_scraper_flow()