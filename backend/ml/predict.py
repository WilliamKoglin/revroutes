"""
predict.py — inference wrapper. Loads the 3 artifacts and predicts delay
for a single flight. This is the MIRROR of train.py and the file that gets
copied into the backend (Phase 4).

Flow (reverse of training):
  metadata.json -> rebuild empty model
  model.pt      -> load trained weights
  encoders.pkl  -> convert strings ("AA","JFK") to the same indices as training
"""
import json
import pickle

import torch

from model import DelayNet

ARTIFACTS_DIR = "backend/ml/artifacts"


class DelayPredictor:
    def __init__(self, artifacts_dir: str = ARTIFACTS_DIR):
        with open(f"{artifacts_dir}/metadata.json") as f:
            self.meta = json.load(f)

        with open(f"{artifacts_dir}/encoders.pkl", "rb") as f:
            self.encoders = pickle.load(f)

        self.model = DelayNet(
            vocab_sizes=self.meta["vocab_sizes"],
            num_numeric=self.meta["num_numeric"],
            emb_dim=self.meta["emb_dim"],
        )
        self.model.load_state_dict(torch.load(f"{artifacts_dir}/model.pt",
                                              map_location="cpu"))
        self.model.eval()  # inference mode: disables dropout

    def predict_buffer(self, flight: dict) -> dict:
        """Return point prediction AND the P90-buffered recommendation."""
        base = self.predict(flight)
        margin = self.meta.get("buffer_margin", 0.0)
        return {
            "predicted_delay_min": round(base, 1),
            "buffer_margin_min": round(margin, 1),
            "recommended_buffer_min": round(base + margin, 1),
            "confidence_pct": self.meta.get("buffer_percentile", 90),
        }
    
    def _encode_row(self, flight: dict):
        cat_cols = self.meta["cat_cols"]
        num_cols = self.meta["num_cols"]
        scaler = self.meta["scaler"]

        # categorical: string -> index, unseen -> 0 (the "unknown" slot)
        cat_vals = []
        for col in cat_cols:
            mapping = self.encoders[col]
            cat_vals.append(mapping.get(flight[col], 0))

        # numeric: apply the same normalization stats from training
        num_vals = []
        for col in num_cols:
            mean = scaler[col]["mean"]
            std = scaler[col]["std"]
            num_vals.append((flight[col] - mean) / std)

        cat = torch.tensor([cat_vals], dtype=torch.long)
        num = torch.tensor([num_vals], dtype=torch.float32)
        return cat, num

    def predict(self, flight: dict) -> float:
        cat, num = self._encode_row(flight)
        with torch.no_grad():
            pred = self.model(cat, num)
        return float(pred.item())


# quick manual test
if __name__ == "__main__":
    predictor = DelayPredictor()

    sample_flight = {
        "Reporting_Airline": "AA",
        "Origin": "LAX",
        "Dest": "IAH",
        "Month": 1,
        "DayOfWeek": 4,
        "DayofMonth": 1,
        "DepHour": 7,
    }

    pred = predictor.predict(sample_flight)
    print(f"Predicted arrival delay: {pred:.1f} min")
    print(predictor.predict_buffer(sample_flight))