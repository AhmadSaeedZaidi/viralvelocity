import pytest

from app.models import (
    AnomalyDetector,
    ClickbaitDetector,
    GenreClassifier,
    TagRecommender,
    VelocityPredictor,
    ViralTrendPredictor,
)
from app.schemas import (
    AnomalyInput,
    ClickbaitInput,
    GenreInput,
    TagInput,
    VelocityInput,
    ViralInput,
)

# --- Fixtures for reusable input data ---


@pytest.fixture
def velocity_input():
    return VelocityInput(
        log_start_views=6.9,
        log_duration=5.7,
        initial_virality_slope=1.2,
        interaction_density=0.1,
        like_view_ratio=0.05,
        comment_view_ratio=0.01,
        video_age_hours=2.0,
        hour_sin=0.5,
        hour_cos=-0.8,
        publish_day=1,
        is_weekend=0,
        title_len=50,
        caps_ratio=0.1,
        exclamation_count=1,
        question_count=0,
        has_digits=0,
        category_id=10,
    )


@pytest.fixture
def clickbait_input():
    return ClickbaitInput(
        title="YOU WON'T BELIEVE THIS",
        view_count=50000,
        like_count=100,
        comment_count=10,
    )


@pytest.fixture
def viral_input():
    return ViralInput(
        view_velocity=100.0,
        like_velocity=10.0,
        comment_velocity=2.0,
        like_ratio=0.1,
        comment_ratio=0.02,
        log_start_views=5.0,
        video_age_hours=2.0,
        duration_seconds=300,
        hour_sin=0.5,
        hour_cos=-0.8,
        initial_virality_slope=1.5,
        interaction_density=0.2,
        title_len=40,
        caps_ratio=0.1,
        has_digits=0,
    )


# --- Tests ---


def test_velocity_model_initialization(velocity_input):
    model = VelocityPredictor("test_velocity", repo_path="/tmp/mock")
    model.load()  # Should trigger mock init
    assert model.is_loaded is True

    prediction = model.predict(velocity_input)
    assert isinstance(prediction, int)
    assert prediction >= 0


def test_viral_trend_prediction(viral_input):
    model = ViralTrendPredictor("test_viral", repo_path="/tmp/mock")
    model.load()

    label, prob = model.predict(viral_input)
    assert isinstance(label, int)
    assert isinstance(prob, float)
    assert 0 <= prob <= 1


def test_clickbait_logic(clickbait_input):
    model = ClickbaitDetector("test_clickbait", repo_path="/tmp/mock")
    model.load()

    # Test the heuristic logic defined in business rules
    # High views + Low engagement = Clickbait
    label, prob = model.predict(clickbait_input)

    assert isinstance(label, int)
    assert isinstance(prob, float)
    assert 0 <= prob <= 1


def test_genre_pca_pipeline():
    model = GenreClassifier("test_genre", repo_path="/tmp/mock")
    model.load()

    input_data = GenreInput(title="Minecraft Speedrun", tags=["gaming", "glitch"])
    genre, confidence = model.predict(input_data)

    assert isinstance(genre, str)  # Should return label like "Gaming"
    assert isinstance(confidence, float)


def test_tag_recommender():
    model = TagRecommender("test_tags", repo_path="/tmp/mock")
    model.load()

    # Test specific association rule defined in mock
    input_data = TagInput(current_tags=["minecraft", "speedrun"])
    recs = model.predict(input_data)

    assert isinstance(recs, list)
    assert "dream" in recs or "manhunt" in recs


def test_anomaly_detection():
    model = AnomalyDetector("test_anomaly", repo_path="/tmp/mock")
    model.load()

    input_data = AnomalyInput(
        view_count=1000000,
        like_count=0,  # suspicious
        comment_count=0,
        duration_seconds=60,
    )
    is_anomaly, score = model.predict(input_data)

    assert isinstance(is_anomaly, bool)
    assert isinstance(score, float)
