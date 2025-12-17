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
        with col2:
            duration = st.number_input("Duration (sec)", 300, step=30)
            # Slopes are engineered features, calculated from 2h data
            # For manual input, we can approximate or ask user
            st.caption("Engineered Features (Auto-calculated in prod)")
            slope_v = st.number_input("View Slope (views/hr)", views_2h / 2.0)
            slope_e = st.number_input("Engagement Slope (eng/hr)", (likes_2h + comments_2h) / 2.0)

        if st.button("Forecast Velocity"):
            payload = {
                "video_stats_24h": {
                    "view_count": views_2h,
                    "like_count": likes_2h,
                    "comment_count": comments_2h,
                    "duration_seconds": duration,
                    "published_hour": 12,
                    "published_day_of_week": 1,
                },
                "channel_stats": {
                    "id": "demo",
                    "avg_views_last_5": 5000,
                    "subscriber_count": 1000,
                },
                "slope_views": slope_v,
                "slope_engagement": slope_e,
            }
            res = client.predict_velocity(payload)
            if res:
                formatted_pred = format_large_number(res['prediction'])
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
                color = "red" if res['prediction'] == "Clickbait" else "green"
                st.markdown(f"Verdict: :{color}[**{res['prediction']}**]")
                st.progress(
                    res['probability'], text=f"Probability: {res['probability']:.2f}"
                )

    # --- TAB 3: Genre ---
    with tabs[2]:
        st.subheader("Genre Classifier (PCA + MLP)")
        g_title = st.text_input("Title", "Minecraft Speedrun World Record")
        g_tags = st.text_input("Tags (comma separated)", "gaming, minecraft, glitch")
        
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
                st.write(res['prediction'])

    # --- TAB 5: Viral ---
    with tabs[4]:
        st.subheader("Viral Trend Probability")
        ranks = st.text_input(
            "Rank History (comma sep, last 7 days)",
            "10, 8, 6, 5, 3, 2, 1",
        )
        velocity = st.number_input("Rank Velocity", -1.5)
        
        if st.button("Predict Viral Status"):
            rank_list = [int(x.strip()) for x in ranks.split(",")]
            payload = {"discovery_rank_history": rank_list, "rank_velocity": velocity}
            res = client.predict_viral(payload)
            if res:
                st.metric("Viral Status", res['prediction'])
                st.bar_chart({"Probability": [res['probability']]})

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
                if "ANOMALY" in res['prediction']:
                    st.error(f"⚠️ {res['prediction']}")
                else:
                    st.success(f"✅ {res['prediction']}")
                st.metric("Anomaly Score", f"{res['anomaly_score']:.4f}")

if __name__ == "__main__":
    render()