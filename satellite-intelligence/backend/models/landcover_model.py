import json
import pickle
from pathlib import Path

import numpy as np

try:
    from sklearn.ensemble import RandomForestClassifier
except ImportError:  # pragma: no cover - dependency is optional until installation.
    RandomForestClassifier = None


class LandCoverModel:
    """Stage-A ML-ready wrapper around a replaceable RandomForest classifier."""

    CLASS_NAMES = ["Agriculture", "Vegetation", "Water", "Built-up", "Bare land"]

    def __init__(self, estimator=None):
        self.estimator = estimator
        self._trained = False
        self._classes = list(self.CLASS_NAMES)

    @property
    def trained(self) -> bool:
        return self._trained and self.estimator is not None

    def train(self, features, labels):
        if RandomForestClassifier is None:
            raise RuntimeError("scikit-learn is required to train the supervised land-cover model.")
        matrix = np.asarray(features, dtype=np.float32)
        targets = np.asarray(labels)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.size == 0 or targets.size == 0:
            raise ValueError("Training labels are required for validated supervised classification.")
        self.estimator = RandomForestClassifier(n_estimators=200, random_state=42)
        self.estimator.fit(matrix, targets)
        self._classes = list(self.estimator.classes_)
        self._trained = True
        return self

    def predict(self, features):
        if not self.trained:
            raise ValueError("Training labels are required for validated supervised classification.")
        matrix = np.asarray(features, dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        return self.estimator.predict(matrix)

    def predict_proba(self, features):
        if not self.trained:
            raise ValueError("Training labels are required for validated supervised classification.")
        matrix = np.asarray(features, dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        return self.estimator.predict_proba(matrix)

    def save(self, path):
        if not self.trained:
            raise ValueError("The model must be trained before it can be saved.")
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as handle:
            pickle.dump({"classes": self._classes, "estimator": self.estimator}, handle)
        return str(output)

    def load(self, path):
        input_path = Path(path)
        with input_path.open("rb") as handle:
            payload = pickle.load(handle)
        self.estimator = payload["estimator"]
        self._classes = payload.get("classes", list(self.CLASS_NAMES))
        self._trained = self.estimator is not None
        return self

    def status(self):
        return {
            "available": RandomForestClassifier is not None,
            "method": "RandomForestClassifier",
            "trained": self.trained,
            "training_labels_required": True,
            "message": "Training labels are required for validated supervised classification.",
        }

    def explain_prediction(self, probabilities, labels=None):
        if probabilities is None:
            return None
        labels = labels or self._classes
        if isinstance(probabilities, np.ndarray):
            probabilities = probabilities.tolist()
        if not isinstance(probabilities, list):
            probabilities = [probabilities]
        ranked = []
        for index, value in enumerate(probabilities[0] if probabilities and isinstance(probabilities[0], (list, tuple, np.ndarray)) else probabilities):
            ranked.append({"class": labels[index], "probability": float(value)})
        ranked.sort(key=lambda item: item["probability"], reverse=True)
        return ranked
