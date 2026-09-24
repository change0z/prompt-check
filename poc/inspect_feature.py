import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

df = pd.read_parquet("data/prompts.parquet")
train = df[df.split == "train"]
model = make_pipeline(
    TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
    LogisticRegression(max_iter=2000, class_weight="balanced"),
)
model.fit(train.text, train.category)

vec, clf = model[0], model[1]
names = vec.get_feature_names_out()
for i, label in enumerate(clf.classes_):
    top = clf.coef_[i].argsort()[-15:][::-1]
    print(f"{label}: {', '.join(names[j] for j in top)}\n")