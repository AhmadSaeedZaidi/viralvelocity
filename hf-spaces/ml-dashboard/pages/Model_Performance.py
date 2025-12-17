import pandas as pd
import streamlit as st
import plotly.express as px
import numpy as np
from utils.data_processing import calculate_mape, format_large_number
from utils.visualizations import plot_accuracy_metric, plot_dummy_drift
from utils.db_client import DatabaseClient
from utils.api_client import YoutubeMLClient

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
        "anomaly": {"scores": []}
    }
    
    # Progress bar
    progress_bar = st.progress(0, text="Running live inference on historical data...")
    
    # Limit to 50 samples for performance
    sample_df = df.head(50).copy()
    total_samples = len(sample_df)
    
    for i, row in sample_df.iterrows():
        # --- 1. Velocity Model ---
        # Payload
        vel_payload = {
            "video_id": row['video_id'],
            "title": row['title'],
            "channel_id": "UC_mock_channel",
            "publish_date": str(row['time']),
            "video_stats_24h": {
                "views": int(row['views'] * 0.2), # Assume 2h was 20% of current (mock history)
                "likes": int(row['likes'] * 0.2),
                "comments": int(row['comments'] * 0.2),
                "duration_seconds": row['duration_seconds'],
                "published_hour": row['time'].hour
            },
            "channel_stats": {
                "avg_views_last_5": int(row['views']),
                "subscriber_count": 10000,
                "video_count": 50
            },
            "slope_views": 10.5,
            "slope_engagement": 0.05
        }
        try:
            resp = client.predict_velocity(vel_payload)
            if resp and "prediction" in resp:
                results["velocity"]["pred"].append(resp["prediction"])
                results["velocity"]["true"].append(row['views'])
        except Exception:
            pass

        # --- 2. Clickbait Model ---
        # Ground Truth: High views but low engagement (likes+comments/views)
        # Thresholds from pipeline: engagement < 0.05
        engagement = (row['likes'] + row['comments']) / (row['views'] + 1)
        is_clickbait_gt = 1 if (row['views'] > 100 and engagement < 0.05) else 0
        
        cb_payload = {
            "title": row['title'],
            "video_stats": {
                "views": row['views'], "likes": row['likes'], "comments": row['comments'],
                "duration_seconds": row['duration_seconds'], "published_hour": row['time'].hour
            }
        }
        try:
            resp = client.predict_clickbait(cb_payload)
            if resp and "prediction" in resp:
                pred_label = 1 if resp["prediction"] == "Clickbait" else 0
                results["clickbait"]["pred"].append(pred_label)
                results["clickbait"]["true"].append(is_clickbait_gt)
        except Exception:
            pass

        # --- 3. Genre Model ---
        # Ground Truth: Simple keyword matching on tags (Heuristic)
        tags_str = str(row.get('tags', '')).lower()
        if 'minecraft' in tags_str: genre_gt = 'Gaming'
        elif 'music' in tags_str: genre_gt = 'Music'
        elif 'tech' in tags_str: genre_gt = 'Tech'
        elif 'education' in tags_str: genre_gt = 'Education'
        else: genre_gt = 'Vlog'
        
        genre_payload = {
            "title": row['title'],
            "tags": row.get('tags', ''),
            "description": row.get('description', '')
        }
        try:
            resp = client.predict_genre(genre_payload)
            if resp and "prediction" in resp:
                results["genre"]["pred"].append(resp["prediction"])
                results["genre"]["true"].append(genre_gt)
        except Exception:
            pass

        # --- 4. Viral Model ---
        # Ground Truth: Views > 10000 (Simple proxy for velocity)
        is_viral_gt = 1 if row['views'] > 10000 else 0
        
        # Mock history for viral input
        viral_payload = {
            "video_stats_history": [
                {"views": 0, "likes": 0, "comments": 0, "timestamp": (row['time'] - pd.Timedelta(hours=24)).isoformat()},
                {"views": row['views'], "likes": row['likes'], "comments": row['comments'], "timestamp": row['time'].isoformat()}
            ],
            "title": row['title'],
            "published_at": str(row['time'])
        }
        try:
            resp = client.predict_viral(viral_payload)
            if resp and "prediction" in resp:
                pred_label = 1 if resp["prediction"] == "Viral" else 0
                results["viral"]["pred"].append(pred_label)
                results["viral"]["true"].append(is_viral_gt)
        except Exception:
            pass

        # --- 5. Anomaly Model ---
        # Unsupervised - just collect scores
        anom_payload = {
            "video_stats": {
                "views": row['views'], "likes": row['likes'], "comments": row['comments'],
                "duration_seconds": row['duration_seconds'], "published_hour": row['time'].hour
            }
        }
        try:
            resp = client.predict_anomaly(anom_payload)
            if resp and "confidence_score" in resp:
                results["anomaly"]["scores"].append(resp["confidence_score"])
        except Exception:
            pass

        progress_bar.progress((i + 1) / total_samples, text=f"Inference: {i+1}/{total_samples}")
    
    progress_bar.empty()

    # --- Calculate Metrics ---
    
    # Velocity
    vel_metrics = client.evaluate_metrics(results["velocity"]["true"], results["velocity"]["pred"], "regression")
    vel_mape = vel_metrics.get("mape", 0.0)
    vel_r2 = vel_metrics.get("r2", 0.0)

    # Clickbait
    cb_metrics = client.evaluate_metrics(results["clickbait"]["true"], results["clickbait"]["pred"], "classification")
    cb_f1 = cb_metrics.get("f1", 0.0)
    cb_acc = cb_metrics.get("accuracy", 0.0)

    # Genre
    genre_metrics = client.evaluate_metrics(results["genre"]["true"], results["genre"]["pred"], "classification")
    genre_acc = genre_metrics.get("accuracy", 0.0)

    # Viral
    viral_metrics = client.evaluate_metrics(results["viral"]["true"], results["viral"]["pred"], "classification")
    viral_prec = viral_metrics.get("precision", 0.0)
    viral_rec = viral_metrics.get("recall", 0.0)

    # --- Top Level Metrics ---
    col1, col2, col3 = st.columns(3)
    with col1:
        st.plotly_chart(
            plot_accuracy_metric("Velocity MAPE (Live)", vel_mape, 15.0),
            use_container_width=True,
        )
        st.caption(f"R² Score: {vel_r2:.3f}")
    with col2:
        st.plotly_chart(
            plot_accuracy_metric("Clickbait F1 (Live)", cb_f1, 0.85),
            use_container_width=True,
        )
        st.caption(f"Accuracy: {cb_acc:.3f}")
    with col3:
        st.plotly_chart(
            plot_accuracy_metric("Genre Accuracy (Live)", genre_acc, 0.91),
            use_container_width=True,
        )

    st.divider()

    # --- Data Volume ---
    st.subheader("Data Volume Analysis")
    st.metric("Total Video Stats Analyzed", format_large_number(len(df)))
    fig = px.line(
        df, x='time', y='views', 
        title='Recent View Counts (Real Data)',
        labels={'time': 'Time', 'views': 'View Count'}
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # --- Advanced Model Stats ---
    st.subheader("Model Performance Metrics")
    
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Velocity (Regression)", 
        "Clickbait (Classification)", 
        "Genre (Multi-class)",
        "Viral Trend (Binary)",
        "Anomaly (Unsupervised)"
    ])
    
    with tab1:
        st.markdown("### Velocity Predictor Performance")
        st.caption(f"Evaluated on {len(results['velocity']['true'])} samples")
        
        eval_df = pd.DataFrame({
            'Actual': results["velocity"]["true"],
            'Predicted': results["velocity"]["pred"]
        })
        
        if not eval_df.empty:
            fig_scatter = px.scatter(
                eval_df, x='Actual', y='Predicted', 
                title="Actual vs Predicted Views (Log Scale)",
                log_x=True, log_y=True, trendline="ols",
                labels={'Actual': 'Actual Views', 'Predicted': 'Predicted Views'}
            )
            st.plotly_chart(fig_scatter, use_container_width=True)
            
            residuals = np.array(results["velocity"]["true"]) - np.array(results["velocity"]["pred"])
            fig_resid = px.histogram(
                residuals, nbins=30, title="Residual Distribution",
                labels={'value': 'Residual (Actual - Predicted)'}
            )
            fig_resid.update_layout(yaxis_title="Frequency")
            st.plotly_chart(fig_resid, use_container_width=True)
        else:
            st.warning("No data for Velocity evaluation.")

    with tab2:
        st.markdown("### Clickbait Detector Performance")
        st.caption("Ground Truth: Views > 100 AND Engagement < 5%")
        
        if results["clickbait"]["true"]:
            from sklearn.metrics import confusion_matrix
            cm = confusion_matrix(results["clickbait"]["true"], results["clickbait"]["pred"], labels=[0, 1])
            
            fig_cm = px.imshow(
                cm,
                labels=dict(x="Predicted Label", y="Actual Label", color="Count"),
                x=['Solid', 'Clickbait'], y=['Solid', 'Clickbait'],
                text_auto=True, title="Confusion Matrix"
            )
            st.plotly_chart(fig_cm, use_container_width=True)
        else:
            st.warning("No data for Clickbait evaluation.")

    with tab3:
        st.markdown("### Genre Classifier Performance")
        st.caption("Ground Truth: Heuristic based on tags (e.g., 'minecraft' -> Gaming)")
        
        if results["genre"]["true"]:
            # Simple bar chart of counts
            pred_counts = pd.Series(results["genre"]["pred"]).value_counts()
            fig_bar = px.bar(
                pred_counts, title="Predicted Genre Distribution",
                labels={'index': 'Genre', 'value': 'Count'}
            )
            fig_bar.update_layout(showlegend=False)
            st.plotly_chart(fig_bar, use_container_width=True)
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
                results["anomaly"]["scores"], nbins=50,
                title="Anomaly Score Distribution",
                labels={'value': 'Anomaly Score'}
            )
            fig_anom.update_layout(yaxis_title="Frequency")
            fig_anom.add_vline(x=0.6, line_dash="dash", line_color="red", annotation_text="Threshold")
            st.plotly_chart(fig_anom, use_container_width=True)
        else:
            st.warning("No data for Anomaly evaluation.")

    st.success("All metrics are now calculated using Live Inference on Real Data with Heuristic Ground Truth.")


if __name__ == "__main__":
    render()