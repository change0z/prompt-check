import pandas as pd

df = pd.read_parquet("data/prompts.parquet")

lengths = df.assign(chars=df.text.str.len())
print(lengths.groupby("category").chars.describe()[["mean", "50%", "max"]].round(0), "\n")

for category in df.category.unique():
    example = df[df.category == category].text.iloc[0]
    print(f"--- {category} ---\n{example[:300]}\n")