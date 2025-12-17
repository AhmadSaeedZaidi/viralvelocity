import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def plot_accuracy_metric(metric_name, value, delta):
    fig = go.Figure(
        go.Indicator(
            mode="number+delta",
            value=value,
            delta={"position": "top", "reference": delta},
            title={"text": metric_name},
            domain={"row": 0, "column": 0},
        )
    )
    fig.update_layout(height=250)
    return fig


def plot_dummy_drift():
    """Generates a dummy drift chart until we have real data."""
    dates = pd.date_range(start="2024-01-01", periods=30)
    drift = np.random.normal(loc=0.5, scale=0.1, size=30)
    drift[25:] += 0.3  # Simulate drift at end

    df = pd.DataFrame({"Date": dates, "Drift Score": drift})
    fig = px.line(
        df,
        x="Date",
        y="Drift Score",
        title="Input Feature Drift (Simulated)",
    )
    fig.add_hline(
        y=0.8, line_dash="dash", line_color="red", annotation_text="Threshold"
    )
    return fig
