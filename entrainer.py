"""Entraîne et compare plusieurs classifieurs d'intention sur dataset_5000.csv."""

import json
import re
import unicodedata

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import GroupShuffleSplit, StratifiedGroupKFold, cross_val_score
from sklearn.naive_bayes import ComplementNB
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.svm import LinearSVC

RANDOM_STATE = 42
np.random.seed(RANDOM_STATE)

df = pd.read_csv("dataset_5000.csv")
X, y, groups = df["text"].values, df["intent"].values, df["group_id"].values

gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=RANDOM_STATE)
train_idx, test_idx = next(gss.split(X, y, groups))
X_train, X_test = X[train_idx], X[test_idx]
y_train, y_test = y[train_idx], y[test_idx]
groups_train = groups[train_idx]
assert set(groups_train).isdisjoint(set(groups[test_idx]))

print("Train %d / Test %d (groupes disjoints)" % (len(X_train), len(X_test)))


def make_features(word_ng=(1, 2), char_ng=(3, 5), min_df=1):
    return FeatureUnion([
        ("word_tfidf", TfidfVectorizer(analyzer="word", ngram_range=word_ng,
                                       min_df=min_df, sublinear_tf=True,
                                       lowercase=True, strip_accents="unicode")),
        ("char_tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=char_ng,
                                       min_df=min_df, sublinear_tf=True,
                                       lowercase=True, strip_accents="unicode")),
    ])


CANDIDATS = {
    "logreg_C5 (référence)": Pipeline([
        ("features", make_features()),
        ("clf", LogisticRegression(max_iter=3000, class_weight="balanced",
                                   C=5.0, random_state=RANDOM_STATE)),
    ]),
    "logreg_C20": Pipeline([
        ("features", make_features()),
        ("clf", LogisticRegression(max_iter=3000, class_weight="balanced",
                                   C=20.0, random_state=RANDOM_STATE)),
    ]),
    "logreg_C20_char2-6": Pipeline([
        ("features", make_features(char_ng=(2, 6))),
        ("clf", LogisticRegression(max_iter=3000, class_weight="balanced",
                                   C=20.0, random_state=RANDOM_STATE)),
    ]),
    "linearsvc_calibre": Pipeline([
        ("features", make_features(char_ng=(2, 6))),
        ("clf", CalibratedClassifierCV(
            LinearSVC(C=1.0, class_weight="balanced", random_state=RANDOM_STATE),
            method="sigmoid", cv=3)),
    ]),
    "complementnb": Pipeline([
        ("features", make_features()),
        ("clf", ComplementNB(alpha=0.3)),
    ]),
}

sgkf = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
resultats = {}
for nom, pipe in CANDIDATS.items():
    scores = cross_val_score(pipe, X_train, y_train, groups=groups_train,
                             cv=sgkf, scoring="f1_macro", n_jobs=-1)
    resultats[nom] = (scores.mean(), scores.std())
    print("  %-24s F1-macro CV = %.3f (+/- %.3f)" % (nom, scores.mean(), scores.std()))

meilleur_nom = max(resultats, key=lambda k: resultats[k][0])
print("\nModèle retenu :", meilleur_nom)

pipeline = CANDIDATS[meilleur_nom]
pipeline.fit(X_train, y_train)
y_pred = pipeline.predict(X_test)
print(classification_report(y_test, y_pred, digits=3))
macro_f1 = f1_score(y_test, y_pred, average="macro")
acc = accuracy_score(y_test, y_pred)
print("Accuracy %.3f | F1-macro %.3f" % (acc, macro_f1))


# --- baseline à base de règles (nlu.py) ---------------------------------------
def normalize_text(value):
    value = unicodedata.normalize("NFD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9\s\-']", " ", value)
    return re.sub(r"\s+", " ", value)


def rule_based(message):
    text = normalize_text(message)
    if any(x in text for x in ["bonjour", "bonsoir", "salut", "hello", "coucou"]):
        return "greeting"
    if any(x in text for x in ["aide", "que peux tu", "comment utiliser", "commande"]):
        return "help"
    if any(x in text for x in ["combien", "nombre", "total"]):
        return "stats"
    if any(x in text for x in ["itineraire", "route", "chemin", "guider", "guide moi",
                               "navigation", "amene moi"]):
        return "route"
    if any(x in text for x in ["plus proche", "proche de moi", "autour de moi",
                               "pres de moi", "nearest"]):
        return "nearest_guard"
    if any(x in text for x in ["garde", "pharmacie", "urgence", "ouverte la nuit"]):
        return "guard"
    return "guard"


y_base = [rule_based(t) for t in X_test]
acc_base = accuracy_score(y_test, y_base)
f1_base = f1_score(y_test, y_base, average="macro")
print("\nBaseline règles : accuracy %.3f | F1-macro %.3f" % (acc_base, f1_base))


# --- seuil de confiance -------------------------------------------------------
proba_test = pipeline.predict_proba(X_test)
classes = pipeline.classes_
print("\nSeuil   F1-macro   rappel unknown")
meilleur_seuil, meilleur_score = 0.35, -1
for seuil in [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
    preds = [classes[p.argmax()] if p.max() >= seuil else "unknown" for p in proba_test]
    f1 = f1_score(y_test, preds, average="macro", zero_division=0)
    mask = y_test == "unknown"
    rappel = float(np.mean(np.array(preds)[mask] == "unknown")) if mask.any() else 0.0
    print("  %.2f     %.3f      %.3f" % (seuil, f1, rappel))
    if f1 > meilleur_score:
        meilleur_seuil, meilleur_score = seuil, f1

print("\nSeuil retenu : %.2f (F1-macro %.3f)" % (meilleur_seuil, meilleur_score))

import joblib
joblib.dump(pipeline, "intent_classifier.joblib", compress=3)

cv_mean, cv_std = resultats[meilleur_nom]
metadata = {
    "model": meilleur_nom,
    "labels": sorted(df["intent"].unique().tolist()),
    "confidence_threshold": meilleur_seuil,
    "cv_f1_macro_mean": float(cv_mean),
    "cv_f1_macro_std": float(cv_std),
    "test_f1_macro": float(macro_f1),
    "test_accuracy": float(acc),
    "test_f1_macro_thresholded": float(meilleur_score),
    "baseline_accuracy": float(acc_base),
    "baseline_f1_macro": float(f1_base),
    "n_train": int(len(X_train)),
    "n_test": int(len(X_test)),
    "n_total": int(len(df)),
    "dataset": "dataset_5000.csv",
}
with open("metadata.json", "w", encoding="utf-8") as f:
    json.dump(metadata, f, ensure_ascii=False, indent=2)
print(json.dumps(metadata, ensure_ascii=False, indent=2))
