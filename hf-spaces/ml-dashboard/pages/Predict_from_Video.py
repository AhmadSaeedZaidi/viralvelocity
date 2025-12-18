import datetime
import math

import numpy as np
import streamlit as st
from utils.api_client import YoutubeMLClient
from utils.youtube_client import YouTubeDataClient


def render():
    st.title("Predict from Video URL")
    st.markdown("Analyze a live YouTube video using our ML models.")

    # Input
    url = st.text_input(
        "Enter YouTube Video URL", placeholder="https://www.youtube.com/watch?v=..."
    )

    if st.button("Analyze Video"):
        if not url:
            st.error("Please enter a URL.")
            return

        try:
            yt_client = YouTubeDataClient()
        except ValueError as e:
            st.error(f"Configuration Error: {e}")
            return

        video_id = yt_client.extract_video_id(url)
        if not video_id:
            st.error("Invalid YouTube URL.")
            return

        with st.spinner("Fetching video data..."):
            details = yt_client.get_video_details(video_id)

        if not details:
            st.error("Could not fetch video details. Check the URL or API Quota.")
            return

        # Display Video Info
        st.subheader("Video Details")
        col1, col2 = st.columns([1, 2])
        with col1:
            st.image(details["thumbnail"], use_container_width=True)
        with col2:
            st.markdown(f"**Title:** {details['title']}")
            st.markdown(f"**Channel:** {details['channel_id']}")
            st.markdown(f"**Published:** {details['published_at']}")
            st.markdown(
                f"**Stats:** {details['view_count']:,} Views | "
                f"{details['like_count']:,} Likes | "
                f"{details['comment_count']:,} Comments"
            )

        st.divider()

        # --- Feature Engineering for Models ---

        # Time calculations
        now = datetime.datetime.now(datetime.timezone.utc)
        published_at = details["published_at"]
        age_hours = (now - published_at).total_seconds() / 3600.0

        publish_hour = published_at.hour
        publish_day = published_at.weekday()
        is_weekend = 1 if publish_day >= 5 else 0

        hour_sin = math.sin(2 * math.pi * publish_hour / 24)
        hour_cos = math.cos(2 * math.pi * publish_hour / 24)

        # Text features (simplified)
        title = details["title"]
        title_len = len(title)
        caps_count = sum(1 for c in title if c.isupper())
        caps_ratio = caps_count / (title_len + 1)
        exclamation_count = title.count("!")
        question_count = title.count("?")
        has_digits = 1 if any(c.isdigit() for c in title) else 0

        # Ratios
        safe_views = max(1, details["view_count"])
        like_view_ratio = details["like_count"] / safe_views
        comment_view_ratio = details["comment_count"] / safe_views

        # Log transforms
        log_views = np.log1p(details["view_count"])
        log_duration = np.log1p(details["duration_seconds"])

        # Derived
        # Initial Virality Slope (approximate using current age)
        # If age is very small, this might be unstable, but that's expected
        safe_age = max(0.1, age_hours)
        initial_virality_slope = log_views / np.log1p(safe_age)

        interaction_num = np.log1p(details["like_count"] + details["comment_count"] * 2)
        interaction_den = np.log1p(details["view_count"] + 1)
        interaction_density = interaction_num / interaction_den

        # --- Model Predictions ---
        ml_client = YoutubeMLClient()

        st.subheader("Model Predictions")

        # 1. Velocity & Viral (Time Sensitive)
        col_v1, col_v2 = st.columns(2)

        with col_v1:
            st.markdown("### 🚀 Velocity Prediction")
            if age_hours > 2.0:
                st.warning(
                    f"Video is {age_hours:.1f}h old. Velocity model requires data < 2h."
                )
            else:
                # Payload
                payload = {
                    "log_start_views": log_views,
                    "log_duration": log_duration,
                    "initial_virality_slope": initial_virality_slope,
                    "interaction_density": interaction_density,
                    "like_view_ratio": like_view_ratio,
                    "comment_view_ratio": comment_view_ratio,
                    "video_age_hours": age_hours,
                    "hour_sin": hour_sin,
                    "hour_cos": hour_cos,
                    "publish_day": publish_day,
                    "is_weekend": is_weekend,
                    "title_len": title_len,
                    "caps_ratio": caps_ratio,
                    "exclamation_count": exclamation_count,
                    "question_count": question_count,
                    "has_digits": has_digits,
                    "category_id": -1,  # Unknown
                }
                try:
                    pred = ml_client.predict("velocity", payload)
                    st.metric("Predicted 24h Views", f"{pred['prediction']:,}")
                except Exception as e:
                    st.error(f"Prediction failed: {e}")

        with col_v2:
            st.markdown("### 📈 Viral Trend")
            if age_hours > 2.0:
                st.warning(
                    f"Video is {age_hours:.1f}h old. Viral model requires data < 2h."
                )
            else:
                view_velocity = details["view_count"] / safe_age
                like_velocity = details["like_count"] / safe_age
                comment_velocity = details["comment_count"] / safe_age

                payload = {
                    "view_velocity": view_velocity,
                    "like_velocity": like_velocity,
                    "comment_velocity": comment_velocity,
                    "like_ratio": like_view_ratio,
                    "comment_ratio": comment_view_ratio,
                    "log_start_views": log_views,
                    "video_age_hours": age_hours,
                    "duration_seconds": details["duration_seconds"],
                    "hour_sin": hour_sin,
                    "hour_cos": hour_cos,
                    "initial_virality_slope": initial_virality_slope,
                    "interaction_density": interaction_density,
                    "title_len": title_len,
                    "caps_ratio": caps_ratio,
                    "has_digits": has_digits,
                }
                try:
                    pred = ml_client.predict("viral", payload)
                    is_viral = pred.get("is_viral", 0)
                    prob = pred.get("probability", 0.0)
                    label = "VIRAL" if is_viral else "Normal"
                    st.metric("Viral Status", label, f"{prob:.1%}")
                except Exception as e:
                    st.error(f"Prediction failed: {e}")

        st.divider()

        # 2. Content Models (Always Run)
        col_c1, col_c2, col_c3 = st.columns(3)

        with col_c1:
            st.markdown("### 🎣 Clickbait Detector")
            payload = {
                "title": title,
                "view_count": details["view_count"],
                "like_count": details["like_count"],
                "comment_count": details["comment_count"],
                "duration_seconds": details["duration_seconds"],
            }
            try:
                pred = ml_client.predict("clickbait", payload)
                is_cb = pred.get("is_clickbait", False)
                prob = pred.get("clickbait_probability", 0.0)
                label = "CLICKBAIT" if is_cb else "Safe"
                st.metric("Verdict", label, f"{prob:.1%}")
            except Exception as e:
                st.error(f"Error: {e}")

        with col_c2:
            st.markdown("### 🏷️ Genre Classifier")
            
            current_tags = details.get("tags", [])
            if current_tags:
                st.caption(f"Tags found: {len(current_tags)}")
            else:
                st.caption("Tags: None")

            payload = {
                "title": title,
                "tags": current_tags,
                "description": details["description"],
            }
            try:
                pred = ml_client.predict("genre", payload)
                genre = pred.get("prediction", "Unknown")
                conf = pred.get("confidence_score", 0.0)
                st.metric("Genre", genre, f"{conf:.1%}")
            except Exception as e:
                st.error(f"Error: {e}")

        with col_c3:
            st.markdown("### 🚨 Anomaly Detector")
            payload = {
                "view_count": details["view_count"],
                "like_count": details["like_count"],
                "comment_count": details["comment_count"],
                "duration_seconds": details["duration_seconds"],
                "published_hour": publish_hour,
                "published_day_of_week": publish_day,
            }
            try:
                pred = ml_client.predict("anomaly", payload)
                is_anom = pred.get("is_anomaly", False)
                score = pred.get("anomaly_score", 0.0)
                label = "ANOMALY" if is_anom else "Normal"
                st.metric("Status", label, f"Score: {score:.2f}")
            except Exception as e:
                st.error(f"Error: {e}")

        st.divider()

        # 3. Tag Recommendations
        st.subheader("🏷️ Tag Recommendations")
        current_tags = details.get("tags", [])
        
        if current_tags:
            st.markdown(f"**Current Tags:** {', '.join(current_tags)}")
            payload = {"current_tags": current_tags}
            try:
                pred = ml_client.predict("tags", payload)
                # Response is a dict, tags are in 'prediction'
                tags_list = pred.get("prediction", [])
                
                if isinstance(tags_list, list) and tags_list:
                    st.success(f"Recommended Tags: {', '.join(tags_list)}")
                elif isinstance(tags_list, list):
                    st.info("No specific recommendations found.")
                else:
                    st.warning(f"Unexpected response format: {type(tags_list)}")
            except Exception as e:
                st.error(f"Error fetching tags: {e}")
        else:
            st.info("No tags found on this video. Cannot generate recommendations.")


if __name__ == "__main__":
    render()
