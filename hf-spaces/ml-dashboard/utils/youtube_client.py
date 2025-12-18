import datetime
import os
import re

import isodate
import streamlit as st
from googleapiclient.discovery import build


class YouTubeDataClient:
    def __init__(self):
        # Try to get key from env vars (HF Spaces) or Streamlit secrets
        self.api_key = os.environ.get("YOUTUBE_API_KEY")
        if not self.api_key:
            # Fallback to checking st.secrets if available
            try:
                self.api_key = st.secrets["YOUTUBE_API_KEY"]
            except Exception:
                pass

        if not self.api_key:
            raise ValueError(
                "YOUTUBE_API_KEY not found in environment variables or secrets."
            )

        self.youtube = build("youtube", "v3", developerKey=self.api_key)

    def extract_video_id(self, url: str) -> str:
        """
        Extracts video ID from various YouTube URL formats.
        """
        if len(url) == 11 and " " not in url:
            return url  # It's already an ID

        patterns = [
            r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
            r"(?:youtu\.be\/)([0-9A-Za-z_-]{11})",
            r"(?:embed\/)([0-9A-Za-z_-]{11})",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    def get_video_details(self, video_id: str):
        """
        Fetches snippet, statistics, and contentDetails for a video.
        """
        try:
            request = self.youtube.videos().list(
                part="snippet,statistics,contentDetails", id=video_id
            )
            response = request.execute()

            if not response.get("items"):
                return None

            item = response["items"][0]
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            content = item.get("contentDetails", {})

            # Parse Duration
            duration_sec = 0
            try:
                duration_obj = isodate.parse_duration(content.get("duration", "PT0S"))
                duration_sec = int(duration_obj.total_seconds())
            except Exception:
                pass

            # Parse Published At
            published_at = snippet.get("publishedAt")
            published_dt = None
            if published_at:
                # Handle ISO format with Z
                published_dt = datetime.datetime.fromisoformat(
                    published_at.replace("Z", "+00:00")
                )

            return {
                "video_id": video_id,
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "tags": snippet.get("tags", []),
                "channel_id": snippet.get("channelId"),
                "published_at": published_dt,
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
                "duration_seconds": duration_sec,
                "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url"),
            }

        except Exception as e:
            print(f"Error fetching video details: {e}")
            return None
