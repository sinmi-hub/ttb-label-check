# Sample labels

Eight synthetic label images (`01`-`08`) plus `labels.csv`, used to demo and
test the checker against a batch of fake "applications". None of these
depict a real brand, bottler, or product.

The cases cover: an exact match, a brand-name casing difference that should
still match, a wrong ABV, a title-case warning heading, a non-bold warning
heading, a subtly reworded warning, an imported wine with a country of
origin line, and a rotated/glare-affected copy of the first label that a
person can still read.

`labels.csv` holds the application-form values for each image (what the
form says, not always what's printed) with an `expected_overall` column of
`Match` or `Mismatch`, so the CSV plus the PNGs can drive the app's batch
mode directly.

Regenerate with:

```
uv run python samples/make_samples.py
```
