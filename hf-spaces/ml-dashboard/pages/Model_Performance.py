import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from utils.api_client import YoutubeMLClient
from utils.data_processing import format_large_number
from utils.db_client import DatabaseClient
from utils.visualizations import plot_accuracy_metric


def render():
    st.title("Model Performance Monitoring")

    try:
        db = DatabaseClient()
        df = db.get_video_stats(days=7)
    except Exception as e:
        st.error(f"Database Connection Error: {e}")
        return

    if df.empty:
        st.warning("No data found in database.")
        return

    # --- Real-Time Inference Evaluation ---

    client = YoutubeMLClient()

    # Storage for evaluation data
    results = {
        "velocity": {"true": [], "pred": []},
        "clickbait": {"true": [], "pred": []},
        "genre": {"true": [], "pred": []},
        "viral": {"true": [], "pred": []},
        "anomaly": {"scores": []},
    }

    # Progress bar
    progress_bar = st.progress(0, text="Running live inference on historical data...")

    # Limit to 50 samples for performance
    sample_df = df.head(50).copy()
    total_samples = len(sample_df)

    for i, row in sample_df.iterrows():
        # --- 1. Velocity Model ---
        # Payload - ensure all required fields are present and valid
        try:
            views = float(row["views"]) if pd.notna(row["views"]) else 0.0
            likes = float(row["likes"]) if pd.notna(row["likes"]) else 0.0
            comments = float(row["comments"]) if pd.notna(row["comments"]) else 0.0
            duration = (
                float(row["duration_seconds"])
                if pd.notna(row["duration_seconds"])
                else 300.0
            )
            title = str(row["title"]) if pd.notna(row["title"]) else ""

            # Calculate features
            video_age_hours = 24.0
            views_2h = views * 0.2
            log_start_views = np.log1p(max(0, views_2h))
            log_duration = np.log1p(max(1, duration))
            ivs = (
                log_start_views / np.log1p(video_age_hours)
                if video_age_hours > 0
                else 0.0
            )
            interaction_density = (
                np.log1p(likes + comments * 2) / np.log1p(views + 1)
                if views > 0
                else 0.0
            )

            vel_payload = {
                "log_start_views": float(log_start_views),
                "log_duration": float(log_duration),
                "initial_virality_slope": float(ivs),
                "interaction_density": float(interaction_density),
                "like_view_ratio": float(likes / (views + 1)),
                "comment_view_ratio": float(comments / (views + 1)),
                "video_age_hours": float(video_age_hours),
                "hour_sin": float(np.sin(2 * np.pi * row["time"].hour / 24)),
                "hour_cos": float(np.cos(2 * np.pi * row["time"].hour / 24)),
                "publish_day": int(row["time"].dayofweek),
                "is_weekend": 1 if row["time"].dayofweek >= 5 else 0,
                "title_len": int(len(title)),
                "caps_ratio": (
                    float(sum(1 for c in title if c.isupper()) / (len(title) + 1))
                    if title
                    else 0.0
                ),
                "exclamation_count": int(title.count("!")),
                "question_count": int(title.count("?")),
                "has_digits": 1 if any(c.isdigit() for c in title) else 0,
                "category_id": -1,
            }

            resp = client.predict_velocity(vel_payload)
            if resp and "prediction" in resp and resp["prediction"] is not None:
                results["velocity"]["pred"].append(resp["prediction"])
                results["velocity"]["true"].append(float(views))
        except Exception:
            # Silently skip on errors to avoid spam
            pass

        # --- 2. Clickbait Model ---
        # Ground Truth: High views but low engagement (likes+comments/views)
        # Thresholds from pipeline: engagement < 0.05
        try:
            views = float(row["views"]) if pd.notna(row["views"]) else 0.0
            likes = float(row["likes"]) if pd.notna(row["likes"]) else 0.0
            comments = float(row["comments"]) if pd.notna(row["comments"]) else 0.0
            title = str(row["title"]) if pd.notna(row["title"]) else ""

            engagement = (likes + comments) / (views + 1)
            is_clickbait_gt = 1 if (views > 100 and engagement < 0.05) else 0

            cb_payload = {
                "title": title,
                "view_count": int(views),
                "like_count": int(likes),
                "comment_count": int(comments),
                "publish_hour": int(row["time"].hour),
                "publish_day": int(row["time"].dayofweek),
                "is_weekend": 1 if row["time"].dayofweek >= 5 else 0,
            }
            resp = client.predict_clickbait(cb_payload)
            if resp and "prediction" in resp:
                pred_label = 1 if resp["prediction"] == "Clickbait" else 0
                results["clickbait"]["pred"].append(pred_label)
                results["clickbait"]["true"].append(is_clickbait_gt)
        except Exception:
            pass

        # --- 3. Genre Model ---
        # Ground Truth: Simple keyword matching on tags (Heuristic)
        try:
            tags_str = (
                str(row.get("tags", "")).lower() if pd.notna(row.get("tags")) else ""
            )
            if "minecraft" in tags_str:
                genre_gt = "Gaming"
            elif "music" in tags_str:
                genre_gt = "Music"
            elif "tech" in tags_str:
                genre_gt = "Tech"
            elif "education" in tags_str:
                genre_gt = "Education"
            else:
                genre_gt = "Vlog"

            # Convert tags string to list
            tags_raw = row.get("tags", "")
            if pd.notna(tags_raw) and tags_raw:
                tags_list = [t.strip() for t in str(tags_raw).split(",") if t.strip()]
            else:
                tags_list = []

            genre_payload = {
                "title": str(row["title"]) if pd.notna(row["title"]) else "",
                "tags": tags_list,
            }
            resp = client.predict_genre(genre_payload)
            if resp and "prediction" in resp:
                results["genre"]["pred"].append(resp["prediction"])
                results["genre"]["true"].append(genre_gt)
        except Exception:
            pass

        # --- 4. Viral Model ---
        # Ground Truth: Views > 10000 (Simple proxy for velocity)
        try:
            views = float(row["views"]) if pd.notna(row["views"]) else 0.0
            likes = float(row["likes"]) if pd.notna(row["likes"]) else 0.0
            comments = float(row["comments"]) if pd.notna(row["comments"]) else 0.0
            duration = (
                float(row["duration_seconds"])
                if pd.notna(row["duration_seconds"])
                else 300.0
            )
            title = str(row["title"]) if pd.notna(row["title"]) else ""

            is_viral_gt = 1 if views > 10000 else 0

            # Calculate viral features
            video_age_hours = 24.0
            view_velocity = views / video_age_hours if video_age_hours > 0 else 0.0
            like_velocity = likes / video_age_hours if video_age_hours > 0 else 0.0
            comment_velocity = (
                comments / video_age_hours if video_age_hours > 0 else 0.0
            )
            log_start_views = np.log1p(views)
            like_ratio = likes / (views + 1) if views > 0 else 0.0
            comment_ratio = comments / (views + 1) if views > 0 else 0.0
            ivs = (
                log_start_views / np.log1p(video_age_hours)
                if video_age_hours > 0
                else 0.0
            )
            interaction_density = (
                np.log1p(likes + comments * 2) / np.log1p(views + 1)
                if views > 0
                else 0.0
            )

            viral_payload = {
                "view_velocity": float(view_velocity),
                "like_velocity": float(like_velocity),
                "comment_velocity": float(comment_velocity),
                "like_ratio": float(like_ratio),
                "comment_ratio": float(comment_ratio),
                "log_start_views": float(log_start_views),
                "video_age_hours": float(video_age_hours),
                "duration_seconds": int(duration),
                "hour_sin": float(np.sin(2 * np.pi * row["time"].hour / 24)),
                "hour_cos": float(np.cos(2 * np.pi * row["time"].hour / 24)),
                "initial_virality_slope": float(ivs),
                "interaction_density": float(interaction_density),
                "title_len": int(len(title)),
                "caps_ratio": (
                    float(sum(1 for c in title if c.isupper()) / (len(title) + 1))
                    if title
                    else 0.0
                ),
                "has_digits": 1 if any(c.isdigit() for c in title) else 0,
            }
            resp = client.predict_viral(viral_payload)
            if resp and "prediction" in resp:
                pred_label = 1 if resp["prediction"] == "Viral" else 0
                results["viral"]["pred"].append(pred_label)
                results["viral"]["true"].append(is_viral_gt)
        except Exception:
            pass

        # --- 5. Anomaly Model ---
        # Unsupervised - just collect scores
        try:
            views = float(row["views"]) if pd.notna(row["views"]) else 0.0
            likes = float(row["likes"]) if pd.notna(row["likes"]) else 0.0
            comments = float(row["comments"]) if pd.notna(row["comments"]) else 0.0
            duration = (
                float(row["duration_seconds"])
                if pd.notna(row["duration_seconds"])
                else 300.0
            )

            anom_payload = {
                "view_count": int(views),
                "like_count": int(likes),
                "comment_count": int(comments),
                "duration_seconds": int(duration),
            }
            resp = client.predict_anomaly(anom_payload)
            if resp and "confidence_score" in resp:
                results["anomaly"]["scores"].append(resp["confidence_score"])
        except Exception:
            pass

        progress_bar.progress(
            (i + 1) / total_samples, text=f"Inference: {i+1}/{total_samples}"
        )

    progress_bar.empty()

    # --- Calculate Metrics ---

    # Velocity
    if results["velocity"]["true"]:
        vel_metrics = client.evaluate_metrics(
            results["velocity"]["true"], results["velocity"]["pred"], "regression"
        )
    else:
        vel_metrics = {}
    vel_mape = vel_metrics.get("mape", 0.0)
    vel_r2 = vel_metrics.get("r2", 0.0)

    # Clickbait
    if results["clickbait"]["true"]:
        cb_metrics = client.evaluate_metrics(
            results["clickbait"]["true"], results["clickbait"]["pred"], "classification"
        )
    else:
        cb_metrics = {}
    cb_f1 = cb_metrics.get("f1", 0.0)
    cb_acc = cb_metrics.get("accuracy", 0.0)

    # Genre
    if results["genre"]["true"]:
        genre_metrics = client.evaluate_metrics(
            results["genre"]["true"], results["genre"]["pred"], "classification"
        )
    else:
        genre_metrics = {}
    genre_acc = genre_metrics.get("accuracy", 0.0)

    # Viral
    if results["viral"]["true"]:
        viral_metrics = client.evaluate_metrics(
            results["viral"]["true"], results["viral"]["pred"], "classification"
        )
    else:
        viral_metrics = {}
    viral_prec = viral_metrics.get("precision", 0.0)
    viral_rec = viral_metrics.get("recall", 0.0)

    # --- Top Level Metrics ---
    col1, col2, col3 = st.columns(3)
    with col1:
        st.plotly_chart(
            plot_accuracy_metric("Velocity MAPE (Live)", vel_mape, 15.0),
            width="stretch",
        )
        st.caption(f"R² Score: {vel_r2:.3f}")
    with col2:
        st.plotly_chart(
            plot_accuracy_metric("Clickbait F1 (Live)", cb_f1, 0.85),
            width="stretch",
        )
        st.caption(f"Accuracy: {cb_acc:.3f}")
    with col3:
        st.plotly_chart(
            plot_accuracy_metric("Genre Accuracy (Live)", genre_acc, 0.91),
            width="stretch",
        )

    st.divider()

    # --- Data Volume ---
    st.subheader("Data Volume Analysis")
    st.metric("Total Video Stats Analyzed", format_large_number(len(df)))
    fig = px.line(
        df,
        x="time",
        y="views",
        title="Recent View Counts (Real Data)",
        labels={"time": "Time", "views": "View Count"},
    )
    st.plotly_chart(fig, width="stretch")

    st.divider()

    # --- Advanced Model Stats ---
    st.subheader("Model Performance Metrics")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "Velocity (Regression)",
            "Clickbait (Classification)",
            "Genre (Multi-class)",
            "Viral Trend (Binary)",
            "Anomaly (Unsupervised)",
        ]
    )

    with tab1:
        st.markdown("### Velocity Predictor Performance")
        st.caption(f"Evaluated on {len(results['velocity']['true'])} samples")

        eval_df = pd.DataFrame(
            {
                "Actual": results["velocity"]["true"],
                "Predicted": results["velocity"]["pred"],
            }
        )

        if not eval_df.empty:
            fig_scatter = px.scatter(
                eval_df,
                x="Actual",
                y="Predicted",
                title="Actual vs Predicted Views (Log Scale)",
                log_x=True,
                log_y=True,
                trendline="ols",
                labels={"Actual": "Actual Views", "Predicted": "Predicted Views"},
            )
            st.plotly_chart(fig_scatter, width="stretch")

            residuals = np.array(results["velocity"]["true"]) - np.array(
                results["velocity"]["pred"]
            )
            fig_resid = px.histogram(
                residuals,
                nbins=30,
                title="Residual Distribution",
                labels={"value": "Residual (Actual - Predicted)"},
            )
            fig_resid.update_layout(yaxis_title="Frequency")
            st.plotly_chart(fig_resid, width="stretch")
        else:
            st.warning("No data for Velocity evaluation.")

    with tab2:
        st.markdown("### Clickbait Detector Performance")
        st.caption("Ground Truth: Views > 100 AND Engagement < 5%")

        if results["clickbait"]["true"]:
            from sklearn.metrics import confusion_matrix

            cm = confusion_matrix(
                results["clickbait"]["true"],
                results["clickbait"]["pred"],
                labels=[0, 1],
            )

            fig_cm = px.imshow(
                cm,
                labels=dict(x="Predicted Label", y="Actual Label", color="Count"),
                x=["Solid", "Clickbait"],
                y=["Solid", "Clickbait"],
                text_auto=True,
                title="Confusion Matrix",
            )
            st.plotly_chart(fig_cm, width="stretch")
        else:
            st.warning("No data for Clickbait evaluation.")

    with tab3:
        st.markdown("### Genre Classifier Performance")
        st.caption(
            "Ground Truth: Heuristic based on tags (e.g., 'minecraft' -> Gaming)"
        )

        if results["genre"]["true"]:
            # Simple bar chart of counts
            pred_counts = pd.Series(results["genre"]["pred"]).value_counts()
            fig_bar = px.bar(
                pred_counts,
                title="Predicted Genre Distribution",
                labels={"index": "Genre", "value": "Count"},
            )
            fig_bar.update_layout(showlegend=False)
            st.plotly_chart(fig_bar, width="stretch")
        else:
            st.warning("No data for Genre evaluation.")

    with tab4:
        st.markdown("### Viral Trend Prediction")
        st.caption("Ground Truth: Views > 10,000")

        st.metric("Viral Precision", f"{viral_prec:.3f}")
        st.metric("Viral Recall", f"{viral_rec:.3f}")

    with tab5:
        st.markdown("### Anomaly Detection")
        st.caption("Unsupervised Anomaly Scores")

        if results["anomaly"]["scores"]:
            fig_anom = px.histogram(
                results["anomaly"]["scores"],
                nbins=50,
                title="Anomaly Score Distribution",
                labels={"value": "Anomaly Score"},
            )
            fig_anom.update_layout(yaxis_title="Frequency")
            fig_anom.add_vline(
                x=0.6, line_dash="dash", line_color="red", annotation_text="Threshold"
            )
            st.plotly_chart(fig_anom, width="stretch")
        else:
            st.warning("No data for Anomaly evaluation.")

    st.success(
        "All metrics are now calculated using Live Inference on Real Data with "
        "Heuristic Ground Truth."
    )


if __name__ == "__main__":
    render()
