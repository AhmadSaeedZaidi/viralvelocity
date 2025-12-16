import unittest

import numpy as np
import pandas as pd

from training.feature_engineering import base_features, temporal_features, text_features


class TestFeatureEngineering(unittest.TestCase):

    def setUp(self):
        # Create dummy data for testing
        self.df_stats = pd.DataFrame({
            'views': [100, 0, 50],
            'likes': [10, 0, 5],
            'comments': [5, 0, 1]
        })
        
        self.df_dates = pd.DataFrame({
            'published_at': ['2023-10-27 10:00:00', '2023-10-28 14:00:00'] # Fri, Sat
        })
        
        self.text_input = "Check out my Minecraft   video! http://link.com #gaming"

    # --- Base Features Tests ---
    def test_clean_dataframe(self):
        df_dirty = pd.DataFrame({'a': [1, np.inf, np.nan]})
        df_clean = base_features.clean_dataframe(df_dirty, fill_value=0)
        self.assertFalse(np.isinf(df_clean['a']).any())
        self.assertFalse(df_clean['a'].isna().any())
        self.assertEqual(df_clean['a'].iloc[2], 0)

    def test_engagement_ratios(self):
        df = base_features.calculate_engagement_ratios(self.df_stats.copy())
        # Check division by zero handling (index 1 has 0 views)
        self.assertEqual(df['like_view_ratio'].iloc[1], 0) 
        # Check calculation: 10/100 = 0.1
        self.assertEqual(df['like_view_ratio'].iloc[0], 0.1)

    # --- Text Features Tests ---
    def test_text_cleaning(self):
        cleaned = text_features.clean_text(self.text_input)
        # Expect: lowercase, no url, no special chars, single spaces
        expected = "check out my minecraft video gaming"
        self.assertEqual(cleaned, expected)

    def test_prepare_text_features(self):
        df = pd.DataFrame({
            'title': ['Video 1'], 
            'tags': [self.text_input]
        })
        res = text_features.prepare_text_features(df)
        # The result is a Series of strings
        self.assertIn("minecraft", res.iloc[0])
        self.assertIn("video 1", res.iloc[0])

    def test_get_tags_list(self):
        tags_str = "Minecraft, Gaming,  Tutorial "
        res = text_features.get_tags_list(tags_str)
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0], "minecraft")
        self.assertEqual(res[2], "tutorial")

    # --- Temporal Features Tests ---
    def test_date_features(self):
        df = temporal_features.add_date_features(self.df_dates.copy())
        # Fri = 4, Sat = 5
        self.assertEqual(df['publish_day'].iloc[0], 4)
        self.assertEqual(df['is_weekend'].iloc[1], 1)

    def test_velocity_features(self):
        df = pd.DataFrame({
            'channel_id': ['c1', 'c1', 'c1'],
            'published_at': pd.to_datetime(['2023-01-01', '2023-01-02', '2023-01-03']),
            'views': [100, 200, 300]
        })
        df = temporal_features.calculate_velocity_features(df, window=2)
        
        # Video 1: No history -> 0
        self.assertEqual(df.iloc[0]['channel_avg_views_recent'], 0)
        # Video 2: History is [100] -> 100
        self.assertEqual(df.iloc[1]['channel_avg_views_recent'], 100)
        # Video 3: History is [100, 200] -> 150
        self.assertEqual(df.iloc[2]['channel_avg_views_recent'], 150)

if __name__ == '__main__':
    unittest.main()