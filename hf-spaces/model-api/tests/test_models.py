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
    ChannelStats,
    ClickbaitInput,
    GenreInput,
    TagInput,
    VelocityInput,
    VideoStats,
    ViralInput,
)

# --- Fixtures for reusable input data ---

@pytest.fixture
def velocity_input():
    return VelocityInput(
        video_stats_24h=VideoStats(
            view_count=1000, like_count=100, comment_count=10,
            duration_seconds=300, published_hour=14, published_day_of_week=2
        ),
        channel_stats=ChannelStats(
            id="123", avg_views_last_5=5000, subscriber_count=1000
        ),
        slope_views=10.5,
        slope_engagement=0.5
    )

@pytest.fixture
def clickbait_input():
    return ClickbaitInput(
        title="YOU WON'T BELIEVE THIS",
        view_count=50000,
        like_count=100,
        comment_count=10
    )

# --- Tests ---

def test_velocity_model_initialization(velocity_input):
    model = VelocityPredictor("test_velocity")
    model.load()  # Should trigger mock init
    assert model.is_loaded is True
    
    prediction = model.predict(velocity_input)
    assert isinstance(prediction, int)
    assert prediction >= 0

def test_clickbait_logic(clickbait_input):
    model = ClickbaitDetector("test_clickbait")
    model.load()
    
    # Test the heuristic logic defined in business rules
    # High views + Low engagement = Clickbait
    label, prob = model.predict(clickbait_input)
    
    assert isinstance(label, int)
    assert isinstance(prob, float)
    assert 0 <= prob <= 1

def test_genre_pca_pipeline():
    model = GenreClassifier("test_genre")
    model.load()
    
    input_data = GenreInput(title="Minecraft Speedrun", tags=["gaming", "glitch"])
    genre, confidence = model.predict(input_data)
    
    assert isinstance(genre, str) # Should return label like "Gaming"
    assert isinstance(confidence, float)

def test_tag_recommender():
    model = TagRecommender("test_tags")
    model.load()
    
    # Test specific association rule defined in mock
    input_data = TagInput(current_tags=["minecraft", "speedrun"])
    recs = model.predict(input_data)
    
    assert isinstance(recs, list)
    assert "dream" in recs or "manhunt" in recs

def test_viral_trend_prediction():
    model = ViralTrendPredictor("test_viral")
    model.load()
    
    input_data = ViralInput(
        discovery_rank_history=[10, 8, 6, 4, 2], # Climbing rank
        rank_velocity=-2.0
    )
    label, prob = model.predict(input_data)
    
    assert label in [0, 1]
    assert 0.0 <= prob <= 1.0

def test_anomaly_detection():
    model = AnomalyDetector("test_anomaly")
    model.load()
    
    input_data = AnomalyInput(
        view_count=1000000,
        like_count=0, # suspicious
        comment_count=0,
        duration_seconds=60
    )
    is_anomaly, score = model.predict(input_data)
    
    assert isinstance(is_anomaly, bool)
    assert isinstance(score, float)