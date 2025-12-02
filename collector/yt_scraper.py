import datetime
import os

from database import SessionLocal
from googleapiclient.discovery import build
from models import VideoStat, init_db

# --- CONFIGURATION ---
API_KEY = os.environ.get("YOUTUBE_API_KEY")

# Target Videos (hardcoded these for now to demonstrate functionality)
TARGET_VIDEOS = [
    "xuCn8ux2gbs", # history of the entire world i guess
    "zqOGDO-kSpE", # the bottom 2 (glorb)
    "dQw4w9WgXcQ" # never gonna give you up
]

def get_youtube_data():
    """
    Connects to YouTube API and fetches stats + metadata.
    """
    print("Connecting to YouTube API...")
    youtube = build("youtube", "v3", developerKey=API_KEY)
    
    # Fetch 'statistics' (views) and 'snippet' (title, thumbnails)
    request = youtube.videos().list(
        part="statistics,snippet",
        id=",".join(TARGET_VIDEOS)
    )
    response = request.execute()
    
    data_objects = []
    # Use UTC time for consistency
    current_time = datetime.datetime.now(datetime.timezone.utc)
    
    for item in response.get("items", []):
        stats = item["statistics"]
        snippet = item["snippet"]
        
        # safely fetch tags
        tags_list = snippet.get("tags", [])
        tags_str = ",".join(tags_list) if tags_list else ""

        # Fetch the best available thumbnail
        thumbnails = snippet.get("thumbnails", {})
        # Try 'high' (480x360), fallback to 'default' (120x90)
        thumb_url = (
            thumbnails.get("high", {}).get("url") 
            or thumbnails.get("default", {}).get("url")
        )

        video_snapshot = VideoStat(
            time=current_time,
            video_id=item["id"],
            views=int(stats.get("viewCount", 0)),
            likes=int(stats.get("likeCount", 0)),
            comments=int(stats.get("commentCount", 0)),
            title=snippet.get("title", ""),
            description=snippet.get("description", ""),
            tags=tags_str,
            thumbnail_url=thumb_url
        )
        data_objects.append(video_snapshot)
        
    return data_objects

def save_to_db(data):
    """
    Writes the data to Neon.
    """
    session = SessionLocal()
    try:
        session.bulk_save_objects(data)
        session.commit()
        print(f"Saved {len(data)} video snapshots to Neon.")
    except Exception as e:
        session.rollback()
        print(f"Error saving to database: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    # 1. Initialize DB
    init_db()
    
    # 2. Check API Key
    if not API_KEY:
        print("Error: YOUTUBE_API_KEY not found.")
        exit(1)
        
    # 3. Run Scraper
    data = get_youtube_data()
    if data:
        save_to_db(data)
    else:
        print("No data found.")