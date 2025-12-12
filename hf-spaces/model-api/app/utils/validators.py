from fastapi import HTTPException


def validate_video_stats(views: int, duration: int):
    """
    Sanity checks for video inputs.
    """
    if views < 0:
        raise HTTPException(status_code=400, detail="View count cannot be negative.")
    
    if duration <= 0:
        raise HTTPException(status_code=400, detail="Duration must be positive.")

def validate_rank_history(ranks: list):
    """
    Ensures rank history data is valid.
    """
    if not ranks:
        raise HTTPException(status_code=400, detail="Rank history cannot be empty.")
    
    if any(r < 1 for r in ranks):
        raise HTTPException(status_code=400, detail="Ranks must be >= 1.")

def check_model_compatibility(
    model_name: str, input_features: int, expected_features: int
):
    """
    Validates that the input vector matches the model's expected shape.
    """
    if input_features != expected_features:
        raise ValueError(
            f"Model {model_name} expects {expected_features} features, "
            f"but got {input_features}"
        )