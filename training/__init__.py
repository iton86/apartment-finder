"""
Offline tooling for the POI-extraction model: data export, labeling,
fine-tuning and evaluation.

Deliberately outside src/: none of this ships in the scraper image, and
nothing in apartment_finder may import from here. The dependency only
runs the other way — these scripts reuse the app's persistence settings
and models to read listings.

Run scripts as modules from the repo root, e.g.:
    uv run python -m training.export_descriptions
"""
