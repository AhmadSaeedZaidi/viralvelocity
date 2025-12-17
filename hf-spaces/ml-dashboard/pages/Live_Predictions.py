import streamlit as st
from utils.api_client import YoutubeMLClient
from utils.data_processing import clean_tags_input, format_large_number


def render():
    client = YoutubeMLClient()

    st.title("Live Model Inference")

    # Tabbed interface for models
    tabs = st.tabs(["Velocity", "Clickbait", "Genre", "Tags", "Viral", "Anomaly"])

    # --- TAB 1: Velocity ---
    with tabs[0]:
        st.subheader("Predict 24-Hour View Count")
        col1, col2 = st.columns(2)
        with col1:
            views_2h = st.number_input("Views (2h)", 1000, step=100)
            likes_2h = st.number_input("Likes (2h)", 100, step=10)
            comments_2h = st.number_input("Comments (2h)", 10, step=1)
            duration = st.number_input("Duration (sec)", 300, step=30)
        with col2:
            title = st.text_input("Title", "My Awesome Video")
            publish_hour = st.slider("Publish Hour (0-23)", 0, 23, 12)
            publish_day = st.selectbox(
                "Day of Week",
                range(7),
                format_func=lambda x: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][
                    x
                ],
            )
            is_weekend = 1 if publish_day >= 5 else 0

        if st.button("Forecast Velocity"):
            import numpy as np

            # Feature Engineering (Client-Side Simulation)
            log_start_views = np.log1p(views_2h)
            log_duration = np.log1p(duration)
            video_age_hours = 2.0

            # Initial Virality Slope
            ivs = log_start_views / np.log1p(video_age_hours)

            # Interaction Density
            interaction_density = np.log1p(likes_2h + comments_2h * 2) / np.log1p(
                views_2h + 1
            )

            # Ratios
            like_view_ratio = likes_2h / (views_2h + 1)
            comment_view_ratio = comments_2h / (views_2h + 1)

            # Time
            hour_sin = np.sin(2 * np.pi * publish_hour / 24)
            hour_cos = np.cos(2 * np.pi * publish_hour / 24)

            # Text
            title_len = len(title)
            caps_ratio = sum(1 for c in title if c.isupper()) / (title_len + 1)
            exclamation_count = title.count("!")
            question_count = title.count("?")
            has_digits = 1 if any(c.isdigit() for c in title) else 0

            payload = {
                "log_start_views": float(log_start_views),
                "log_duration": float(log_duration),
                "initial_virality_slope": float(ivs),
                "interaction_density": float(interaction_density),
                "like_view_ratio": float(like_view_ratio),
                "comment_view_ratio": float(comment_view_ratio),
                "video_age_hours": float(video_age_hours),
                "hour_sin": float(hour_sin),
                "hour_cos": float(hour_cos),
                "publish_day": int(publish_day),
                "is_weekend": int(is_weekend),
                "title_len": int(title_len),
                "caps_ratio": float(caps_ratio),
                "exclamation_count": int(exclamation_count),
                "question_count": int(question_count),
                "has_digits": int(has_digits),
                "category_id": -1,
            }

            res = client.predict_velocity(payload)
            if res:
                formatted_pred = format_large_number(res["prediction"])
                message = (
                    f"Predicted Views (24 Hours): **{formatted_pred}** "
                    f"({res['prediction']})"
                )
                st.success(message)
                st.json(res)

    # --- TAB 2: Clickbait ---
    with tabs[1]:
        st.subheader("Clickbait Detector")
        title = st.text_input("Video Title", "YOU WON'T BELIEVE THIS!")
        c_views = st.number_input("Current Views", 50000)
        c_likes = st.number_input("Current Likes", 100)
        c_comments = st.number_input("Current Comments", 50)

        if st.button("Check Clickbait"):
            payload = {
                "title": title,
                "view_count": c_views,
                "like_count": c_likes,
                "comment_count": c_comments,
            }
            res = client.predict_clickbait(payload)
            if res:
                color = "red" if res["prediction"] == "Clickbait" else "green"
                st.markdown(f"Verdict: :{color}[**{res['prediction']}**]")
                st.progress(
                    res["probability"], text=f"Probability: {res['probability']:.2f}"
                )

    # --- TAB 3: Genre ---
    with tabs[2]:
        st.subheader("Genre Classifier (PCA + MLP)")
        g_title = st.text_input("Title", "Minecraft Speedrun World Record")
        g_tags = st.text_input("Tags (comma separated)", "minecraft, glitch")

        if st.button("Classify Genre"):
            payload = {"title": g_title, "tags": clean_tags_input(g_tags)}
            res = client.predict_genre(payload)
            if res:
                st.info(f"Category: **{res['prediction']}**")
                st.metric("Confidence", f"{res['confidence_score']:.2%}")

    # --- TAB 4: Tags ---
    with tabs[3]:
        st.subheader("Tag Recommender")
        current_tags = st.text_input("Current Tags", "python, tutorial")

        if st.button("Get Recommendations"):
            payload = {"current_tags": clean_tags_input(current_tags)}
            res = client.predict_tags(payload)
            if res:
                st.write("Recommended Tags:")
                st.write(res["prediction"])

    # --- TAB 5: Viral ---
    with tabs[4]:
        st.subheader("Viral Trend Probability")
        col1, col2 = st.columns(2)
        with col1:
            v_views = st.number_input(
                "Views (Current)", min_value=0, value=100, step=10
            )
            v_likes = st.number_input("Likes (Current)", min_value=0, value=10, step=1)
            v_comments = st.number_input(
                "Comments (Current)", min_value=0, value=2, step=1
            )
            v_duration = st.number_input(
                "Duration (s)", min_value=1, value=300, step=30
            )
        with col2:
            v_title = st.text_input("Title", "Viral Video Candidate")
            v_age_hours = st.number_input(
                "Video Age (Hours)", min_value=0.1, value=2.0, step=0.5
            )
            v_hour = st.slider("Publish Hour", 0, 23, 12)

        if st.button("Predict Viral Status"):
            import numpy as np

            # Feature Engineering (Client-Side Simulation)
            # Assuming 2 snapshots: T=0 (0 views) and T=Current
            # Velocities
            view_velocity = v_views / v_age_hours
            like_velocity = v_likes / v_age_hours
            comment_velocity = v_comments / v_age_hours

            # Log Features
            log_start_views = np.log1p(v_views)

            # Ratios
            like_ratio = v_likes / (v_views + 1)
            comment_ratio = v_comments / (v_views + 1)

            # Advanced
            ivs = log_start_views / np.log1p(v_age_hours)
            interaction_density = np.log1p(v_likes + v_comments * 2) / np.log1p(
                v_views + 1
            )

            # Time
            hour_sin = np.sin(2 * np.pi * v_hour / 24)
            hour_cos = np.cos(2 * np.pi * v_hour / 24)

            # Text
            title_len = len(v_title)
            caps_ratio = sum(1 for c in v_title if c.isupper()) / (title_len + 1)
            has_digits = 1 if any(c.isdigit() for c in v_title) else 0

            payload = {
                "view_velocity": float(view_velocity),
                "like_velocity": float(like_velocity),
                "comment_velocity": float(comment_velocity),
                "like_ratio": float(like_ratio),
                "comment_ratio": float(comment_ratio),
                "log_start_views": float(log_start_views),
                "video_age_hours": float(v_age_hours),
                "duration_seconds": int(v_duration),
                "hour_sin": float(hour_sin),
                "hour_cos": float(hour_cos),
                "initial_virality_slope": float(ivs),
                "interaction_density": float(interaction_density),
                "title_len": int(title_len),
                "caps_ratio": float(caps_ratio),
                "has_digits": int(has_digits),
            }

            res = client.predict_viral(payload)
            if res:
                st.metric("Viral Status", res["prediction"])
                st.metric("Confidence", f"{res['probability']:.2%}")

    # --- TAB 6: Anomaly ---
    with tabs[5]:
        st.subheader("Bot/Anomaly Detection")
        a_views = st.number_input("Views", 100000)
        a_likes = st.number_input("Likes", 10)
        a_comments = st.number_input("Comments", 0)
        a_dur = st.number_input("Duration", 60)

        if st.button("Scan for Anomalies"):
            payload = {
                "view_count": a_views,
                "like_count": a_likes,
                "comment_count": a_comments,
                "duration_seconds": a_dur,
            }
            res = client.predict_anomaly(payload)
            if res:
                if "ANOMALY" in res["prediction"]:
                    st.error(f"⚠️ {res['prediction']}")
                else:
                    st.success(f"✅ {res['prediction']}")
                st.metric("Anomaly Score", f"{res['anomaly_score']:.4f}")


if __name__ == "__main__":
    render()
