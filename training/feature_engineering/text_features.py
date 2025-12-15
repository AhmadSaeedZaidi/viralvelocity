import re

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer


def clean_text(text: str) -> str:
    """
    Basic text cleaning: lowercase, remove special chars, extra spaces.
    """
    if not isinstance(text, str):
        return ""
        
    # Lowercase
    text = text.lower()
    
    # Remove URLS
    text = re.sub(r'http\S+|www\S+|https\S+', '', text, flags=re.MULTILINE)
    
    # Remove special characters but keep alphanumeric and spaces
    text = re.sub(r'[^\w\s]', ' ', text)
    
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

def prepare_text_features(df: pd.DataFrame, text_cols=['title', 'tags']) -> pd.Series:
    """
    Combines multiple text columns into a single 'corpus' column for vectorization.
    """
    # Fill NaNs
    for col in text_cols:
        df[col] = df[col].fillna("")
        
    # Combine (with space separator)
    combined_text = df[text_cols].agg(' '.join, axis=1)
    
    # Clean
    return combined_text.apply(clean_text)

def extract_title_features(df: pd.DataFrame, title_col='title') -> pd.DataFrame:
    """Extracts heuristic features from video titles (clickbait signals)."""
    if title_col not in df.columns:
        return df
        
    # Ensure string type and handle NaNs
    titles = df[title_col].fillna("").astype(str)
    
    df["title_len"] = titles.str.len()
    
    # Count uppercase characters
    df["caps_count"] = titles.apply(lambda x: sum(1 for c in x if c.isupper()))
    df["caps_ratio"] = df["caps_count"] / (df["title_len"] + 1)
    
    # Punctuation counts
    df["exclamation_count"] = titles.str.count("!")
    df["question_count"] = titles.str.count(r"\?")
    
    # Digits
    df["has_digits"] = titles.str.contains(r'\d').astype(int)
    
    return df

def get_tags_list(tags_str: str) -> list:
    """
    Parses a comma-separated tag string into a clean list.
    """
    if not isinstance(tags_str, str):
        return []
    
    return [t.strip().lower() for t in tags_str.split(',') if t.strip()]

class TextPreprocessor:
    """
    Wrapper to ensure training and inference use exact same text logic.
    """
    def __init__(self, max_features=5000):
        self.vectorizer = TfidfVectorizer(
            max_features=max_features,
            stop_words='english',
            min_df=5,    # Ignore terms that appear in < 5 docs
            max_df=0.95  # Ignore terms that appear in > 95% docs (too common)
        )

    def fit_transform(self, raw_text_series):
        return self.vectorizer.fit_transform(raw_text_series)

    def transform(self, raw_text_series):
        return self.vectorizer.transform(raw_text_series)